"""Initial baseline before SDD evolution (Fixed counter, no sliding window, no circuit breaker)."""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining_tokens: int
    reason: str


class TokenBucketGateway:
    def __init__(self, max_tokens_per_window: int = 1000) -> None:
        self.max_tokens_per_window = int(max_tokens_per_window)
        self._used: Dict[str, int] = {}

    def check_and_consume(self, tenant_id: str, requested_tokens: int) -> RateLimitDecision:
        used = self._used.get(tenant_id, 0)
        if used + requested_tokens > self.max_tokens_per_window:
            return RateLimitDecision(
                allowed=False,
                remaining_tokens=max(0, self.max_tokens_per_window - used),
                reason="token_quota_exceeded",
            )
        self._used[tenant_id] = used + requested_tokens
        return RateLimitDecision(
            allowed=True,
            remaining_tokens=self.max_tokens_per_window - self._used[tenant_id],
            reason="ok",
        )
