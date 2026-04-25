from unittest.mock import MagicMock

import anthropic
import pytest

from src.analyzer._retry import DEFAULT_MAX_ATTEMPTS, with_retry


class _FakeStatusError(anthropic.APIStatusError):
    """Skip the SDK's full constructor — we only need status_code on instances."""

    def __init__(self, status_code: int):
        self.status_code = status_code


def test_with_retry_returns_value_on_success():
    fn = MagicMock(return_value="ok")
    out = with_retry(fn, sleep=lambda _: None)
    assert out == "ok"
    assert fn.call_count == 1


def test_with_retry_retries_429_and_succeeds():
    fn = MagicMock(side_effect=[_FakeStatusError(429), "ok"])
    sleeps: list[float] = []
    out = with_retry(fn, sleep=sleeps.append, base_delay=0.01)
    assert out == "ok"
    assert fn.call_count == 2
    # Backed off once
    assert len(sleeps) == 1


def test_with_retry_retries_503():
    fn = MagicMock(side_effect=[_FakeStatusError(503), _FakeStatusError(502), "ok"])
    out = with_retry(fn, sleep=lambda _: None, base_delay=0.01)
    assert out == "ok"
    assert fn.call_count == 3


def test_with_retry_retries_connection_errors():
    fn = MagicMock(
        side_effect=[
            anthropic.APIConnectionError(request=MagicMock()),
            "ok",
        ]
    )
    out = with_retry(fn, sleep=lambda _: None, base_delay=0.01)
    assert out == "ok"
    assert fn.call_count == 2


def test_with_retry_does_not_retry_on_400():
    """400 / 401 / 403 are caller errors — don't waste retries on them."""
    fn = MagicMock(side_effect=_FakeStatusError(400))
    with pytest.raises(_FakeStatusError):
        with_retry(fn, sleep=lambda _: None, base_delay=0.01)
    assert fn.call_count == 1


def test_with_retry_gives_up_after_max_attempts():
    fn = MagicMock(side_effect=_FakeStatusError(429))
    with pytest.raises(_FakeStatusError):
        with_retry(fn, sleep=lambda _: None, base_delay=0.01, max_attempts=3)
    assert fn.call_count == 3


def test_with_retry_default_max_attempts_is_four():
    assert DEFAULT_MAX_ATTEMPTS == 4


def test_with_retry_does_not_retry_unrelated_exceptions():
    """ValueError isn't an Anthropic error — propagate immediately."""
    fn = MagicMock(side_effect=ValueError("nope"))
    with pytest.raises(ValueError):
        with_retry(fn, sleep=lambda _: None)
    assert fn.call_count == 1


def test_with_retry_exponential_backoff_doubles():
    sleeps: list[float] = []
    fn = MagicMock(side_effect=[_FakeStatusError(500)] * 4)
    with pytest.raises(_FakeStatusError):
        with_retry(fn, sleep=sleeps.append, base_delay=1.0, max_attempts=4)
    # 3 sleeps between 4 attempts: 1, 2, 4
    assert sleeps == [1.0, 2.0, 4.0]
