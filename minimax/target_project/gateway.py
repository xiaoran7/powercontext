"""
SDD Target Project: AI Token Rate-Limiting & Circuit-Breaker Gateway
Implements:
- SPEC-GW-01: Sliding-window dual-bucket (request count + token quota) limiter
- SPEC-GW-02: Three-state upstream circuit breaker (CLOSED / OPEN / HALF_OPEN)
- SPEC-GW-03: Standard observability headers export
"""

from collections import deque
from dataclasses import dataclass
import time
from typing import Callable, Deque, Dict, Tuple


@dataclass(frozen=True)
class RateLimitDecision:
    """SPEC-GW-03: Gateway decision and standard HTTP header export contract."""

    allowed: bool
    remaining_tokens: int
    retry_after_sec: float
    circuit_state: str
    reason: str

    def to_headers(self) -> Dict[str, str]:
        return {
            "X-RateLimit-Allowed": "true" if self.allowed else "false",
            "X-RateLimit-Remaining-Tokens": str(max(0, self.remaining_tokens)),
            "X-RateLimit-Retry-After": f"{max(0.0, self.retry_after_sec):.2f}",
            "X-Circuit-State": self.circuit_state,
            "X-Gateway-Reason": self.reason,
        }


class TokenBucketGateway:
    """
    Implements SPEC-GW-01, SPEC-GW-02, and SPEC-GW-03 while enforcing
    DEC-GW-01 (injected monotonic clock), DEC-GW-02 (circuit-first evaluation),
    PIT-GW-01 (no window mutation on rejected requests), and PIT-GW-02 (refresh opened_at on half-open failure).
    """

    def __init__(
        self,
        window_sec: float = 60.0,
        max_requests_per_window: int = 10,
        max_tokens_per_window: int = 1000,
        failure_threshold: int = 3,
        recovery_timeout_sec: float = 30.0,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window_sec = float(window_sec)
        self.max_requests_per_window = int(max_requests_per_window)
        self.max_tokens_per_window = int(max_tokens_per_window)
        self.failure_threshold = int(failure_threshold)
        self.recovery_timeout_sec = float(recovery_timeout_sec)
        self._clock = clock_fn

        self._windows: Dict[str, Deque[Tuple[float, int]]] = {}
        self._circuit_state: str = "CLOSED"
        self._consecutive_failures: int = 0
        self._opened_at: float = 0.0

    def _evict_expired(self, tenant_id: str, now: float) -> Deque[Tuple[float, int]]:
        q = self._windows.setdefault(tenant_id, deque())
        cutoff = now - self.window_sec
        while q and q[0][0] <= cutoff:
            q.popleft()
        return q

    def _current_circuit_state(self, now: float) -> str:
        if self._circuit_state == "OPEN":
            if (now - self._opened_at) >= self.recovery_timeout_sec:
                self._circuit_state = "HALF_OPEN"
        return self._circuit_state

    def check_and_consume(self, tenant_id: str, requested_tokens: int) -> RateLimitDecision:
        """SPEC-GW-01 & SPEC-GW-02: Circuit-first admission and sliding-window token deduction."""
        if requested_tokens <= 0:
            raise ValueError("requested_tokens must be positive")

        now = self._clock()
        q = self._evict_expired(tenant_id, now)
        used_tokens = sum(t for _, t in q)
        remaining_before = max(0, self.max_tokens_per_window - used_tokens)

        # DEC-GW-02: Evaluate circuit breaker BEFORE deducting tenant quota
        c_state = self._current_circuit_state(now)
        if c_state == "OPEN":
            retry_after = max(0.0, self.recovery_timeout_sec - (now - self._opened_at))
            return RateLimitDecision(
                allowed=False,
                remaining_tokens=remaining_before,
                retry_after_sec=round(retry_after, 3),
                circuit_state="OPEN",
                reason="circuit_open",
            )

        # PIT-GW-01: Validate both request count and token sum BEFORE mutating deque
        if len(q) >= self.max_requests_per_window:
            oldest_ts = q[0][0]
            retry_after = max(0.0, (oldest_ts + self.window_sec) - now)
            return RateLimitDecision(
                allowed=False,
                remaining_tokens=remaining_before,
                retry_after_sec=round(retry_after, 3),
                circuit_state=c_state,
                reason="request_count_exceeded",
            )

        if used_tokens + requested_tokens > self.max_tokens_per_window:
            oldest_ts = q[0][0] if q else now
            retry_after = max(0.0, (oldest_ts + self.window_sec) - now) if q else self.window_sec
            return RateLimitDecision(
                allowed=False,
                remaining_tokens=remaining_before,
                retry_after_sec=round(retry_after, 3),
                circuit_state=c_state,
                reason="token_quota_exceeded",
            )

        q.append((now, requested_tokens))
        return RateLimitDecision(
            allowed=True,
            remaining_tokens=self.max_tokens_per_window - (used_tokens + requested_tokens),
            retry_after_sec=0.0,
            circuit_state=c_state,
            reason="ok",
        )

    def record_upstream_outcome(self, success: bool) -> str:
        """SPEC-GW-02: Update three-state circuit breaker based on upstream call outcome."""
        now = self._clock()
        c_state = self._current_circuit_state(now)

        if success:
            self._consecutive_failures = 0
            self._circuit_state = "CLOSED"
            return self._circuit_state

        self._consecutive_failures += 1
        # PIT-GW-02: Always refresh _opened_at when HALF_OPEN probe fails or threshold reached
        if c_state == "HALF_OPEN" or self._consecutive_failures >= self.failure_threshold:
            self._circuit_state = "OPEN"
            self._opened_at = now
        return self._circuit_state
