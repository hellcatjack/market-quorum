import json

from tradingng_platform.vendors.alpha_vantage import (
    AlphaVantageRetryPolicy,
    CrossProcessRateGate,
    alpha_key_fingerprint,
    classify_alpha_payload,
)


def test_retry_policy_uses_bounded_exponential_and_retry_after():
    policy = AlphaVantageRetryPolicy(attempts=4, base_seconds=2, max_seconds=5)

    assert policy.delay(1) == 2
    assert policy.delay(2) == 4
    assert policy.delay(3) == 5
    assert policy.delay(1, retry_after=4.5) == 4.5
    assert policy.delay(2, retry_after=9) == 5


def test_cross_process_gate_shares_a_smooth_request_schedule(tmp_path):
    observed_now = [100.0]
    sleeps = []

    def clock():
        return observed_now[0]

    def sleep(seconds):
        sleeps.append(seconds)
        observed_now[0] += seconds

    path = tmp_path / "alpha.json"
    first = CrossProcessRateGate(path, 60, clock=clock, sleep=sleep)
    second = CrossProcessRateGate(path, 60, clock=clock, sleep=sleep)

    first.acquire()
    second.acquire()

    assert sleeps == [1.0]
    assert json.loads(path.read_text()) == {"next_allowed_at": 102.0}


def test_cross_process_gate_defers_every_instance_after_rate_limit(tmp_path):
    observed_now = [200.0]
    sleeps = []

    def clock():
        return observed_now[0]

    def sleep(seconds):
        sleeps.append(seconds)
        observed_now[0] += seconds

    path = tmp_path / "alpha.json"
    first = CrossProcessRateGate(path, 120, clock=clock, sleep=sleep)
    second = CrossProcessRateGate(path, 120, clock=clock, sleep=sleep)

    first.acquire()
    first.defer(7)
    second.acquire()

    assert sleeps == [7.0]


def test_alpha_payload_classifier_distinguishes_retry_and_authentication():
    assert classify_alpha_payload({"Note": "API call frequency exceeded"}) == "rate_limit"
    assert classify_alpha_payload({"Information": "rate limit reached"}) == "rate_limit"
    assert classify_alpha_payload({"Error Message": "temporary upstream error"}) == "transient"
    assert classify_alpha_payload({"Error Message": "invalid API key"}) == "authentication"
    assert classify_alpha_payload({"Time Series (Daily)": {}}) is None


def test_key_fingerprint_is_stable_and_does_not_contain_secret():
    fingerprint = alpha_key_fingerprint("premium-secret-key")

    assert fingerprint == alpha_key_fingerprint("premium-secret-key")
    assert len(fingerprint) == 16
    assert "premium-secret-key" not in fingerprint
