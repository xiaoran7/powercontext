"""
Initial baseline before SDD evolution (Fixed counter, no sliding window eviction, no circuit breaker).
Preserves basic token quota deduction for regression compatibility, while target SDD features
(SPEC-GW-01 sliding window + PIT-GW-01, SPEC-GW-02 circuit breaker, SPEC-GW-03 headers) are not yet implemented.
"""

from dataclasses import dataclass
import time
from typing import Callable, Dict


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining_tokens: int
    retry_after_sec: float = 0.0
    circuit_state: str = "CLOSED"
    reason: str = "ok"

    def to_headers(self) -> Dict[str, str]:
        return {}


class TokenBucketGateway:
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
        self._used: Dict[str, int] = {}

    def check_and_consume(self, tenant_id: str, requested_tokens: int) -> RateLimitDecision:
        if requested_tokens <= 0:
            raise ValueError("requested_tokens must be positive")
        used = self._used.get(tenant_id, 0)
        # Bug before SDD (PIT-GW-01): mutates usage before checking quota and never evicts expired window
        self._used[tenant_id] = used + requested_tokens
        if self._used[tenant_id] > self.max_tokens_per_window:
            return RateLimitDecision(
                allowed=False,
                remaining_tokens=max(0, self.max_tokens_per_window - self._used[tenant_id]),
                retry_after_sec=0.0,
                circuit_state="CLOSED",
                reason="token_quota_exceeded",
            )
        return RateLimitDecision(
            allowed=True,
            remaining_tokens=self.max_tokens_per_window - self._used[tenant_id],
            retry_after_sec=0.0,
            circuit_state="CLOSED",
            reason="ok",
        )

    def record_upstream_outcome(self, success: bool) -> str:
        return "CLOSED"
