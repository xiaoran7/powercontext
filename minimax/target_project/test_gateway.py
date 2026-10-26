import os
import sys

if os.environ.get("PYTHONPATH"):
    for p in reversed(os.environ["PYTHONPATH"].split(os.pathsep)):
        if p and p not in sys.path:
            sys.path.insert(0, p)
        elif p in sys.path:
            sys.path.remove(p)
            sys.path.insert(0, p)

from gateway import TokenBucketGateway


class FakeClock:
    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_regression_basic_token_consumption():
    """Regression test: basic within-quota token consumption must pass both before and after modification."""
    gw = TokenBucketGateway(max_tokens_per_window=1000)
    d1 = gw.check_and_consume("tenant-reg", 300)
    assert d1.allowed is True
    assert d1.remaining_tokens == 700

    d2 = gw.check_and_consume("tenant-reg", 200)
    assert d2.allowed is True
    assert d2.remaining_tokens == 500


def test_token_quota_and_sliding_window_eviction():
    """Target feature test 1: Verifies SPEC-GW-01 and PIT-GW-01 (rejected request does not pollute window)."""
    clock = FakeClock()
    gw = TokenBucketGateway(
        window_sec=60.0,
        max_requests_per_window=3,
        max_tokens_per_window=500,
        clock_fn=clock,
    )

    d1 = gw.check_and_consume("tenant-a", 300)
    assert d1.allowed is True
    assert d1.remaining_tokens == 200

    # Oversized request must be rejected WITHOUT consuming remaining 200 tokens (PIT-GW-01)
    d_reject = gw.check_and_consume("tenant-a", 250)
    assert d_reject.allowed is False
    assert d_reject.reason == "token_quota_exceeded"
    assert d_reject.remaining_tokens == 200
    assert d_reject.retry_after_sec == 60.0

    # Subsequent valid 200-token request must still succeed
    d2 = gw.check_and_consume("tenant-a", 200)
    assert d2.allowed is True
    assert d2.remaining_tokens == 0

    # Advance clock past 60s window -> quota recovered
    clock.advance(61.0)
    d3 = gw.check_and_consume("tenant-a", 400)
    assert d3.allowed is True
    assert d3.remaining_tokens == 100


def test_circuit_breaker_state_transitions():
    """Target feature test 2: Verifies SPEC-GW-02, DEC-GW-02 (circuit-first), and PIT-GW-02."""
    clock = FakeClock()
    gw = TokenBucketGateway(
        window_sec=60.0,
        max_requests_per_window=10,
        max_tokens_per_window=1000,
        failure_threshold=2,
        recovery_timeout_sec=30.0,
        clock_fn=clock,
    )

    assert gw.record_upstream_outcome(False) == "CLOSED"
    assert gw.record_upstream_outcome(False) == "OPEN"

    # DEC-GW-02: Circuit OPEN rejects without deducting tenant token quota
    blocked = gw.check_and_consume("tenant-b", 100)
    assert blocked.allowed is False
    assert blocked.circuit_state == "OPEN"
    assert blocked.remaining_tokens == 1000

    # Advance 30s -> enters HALF_OPEN
    clock.advance(30.0)
    probe = gw.check_and_consume("tenant-b", 100)
    assert probe.allowed is True
    assert probe.circuit_state == "HALF_OPEN"

    # PIT-GW-02: HALF_OPEN failure re-opens circuit and refreshes opened_at
    assert gw.record_upstream_outcome(False) == "OPEN"
    clock.advance(10.0)
    still_open = gw.check_and_consume("tenant-b", 100)
    assert still_open.allowed is False
    assert still_open.circuit_state == "OPEN"

    # Advance remaining 20s -> HALF_OPEN -> probe succeeds -> CLOSED
    clock.advance(20.0)
    assert gw.record_upstream_outcome(True) == "CLOSED"


def test_gateway_headers_contract():
    """Target feature test 3: Verifies SPEC-GW-03 observability headers contract."""
    gw = TokenBucketGateway()
    decision = gw.check_and_consume("tenant-c", 120)
    headers = decision.to_headers()
    assert headers["X-RateLimit-Allowed"] == "true"
    assert headers["X-RateLimit-Remaining-Tokens"] == "880"
    assert headers["X-Circuit-State"] == "CLOSED"
    assert headers["X-Gateway-Reason"] == "ok"
