"""utils/http.py: retry/backoff/Retry-After/redaction against a fake requests.get."""

import pytest
import requests

from utils import http


class Resp:
    def __init__(self, status=200, headers=None, payload=None):
        self.status_code, self.headers, self._payload = status, headers or {}, payload

    def json(self):
        return self._payload


@pytest.fixture
def fake(monkeypatch):
    calls, sleeps, queue = [], [], []

    def get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(http.requests, "get", get)
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    return type("F", (), {"calls": calls, "sleeps": sleeps, "queue": queue})


def test_sends_user_agent_and_timeout(fake):
    fake.queue.append(Resp())
    http.get("https://x.test/a")
    assert fake.calls[0]["timeout"] == 15 and fake.calls[0]["headers"]["User-Agent"].startswith("job-scraper/1.0")


def test_429_honours_retry_after_then_succeeds(fake):
    fake.queue += [Resp(429, {"Retry-After": "2"}), Resp(200)]
    assert http.get("https://x.test/a").status_code == 200
    assert fake.sleeps == [2.0]


def test_retry_after_is_capped(fake):
    fake.queue += [Resp(429, {"Retry-After": "9999"}), Resp(200)]
    http.get("https://x.test/a")
    assert fake.sleeps == [60.0]


def test_5xx_and_network_errors_retry_at_most_twice_more(fake):
    fake.queue += [Resp(503), requests.ConnectionError("down"), Resp(500)]
    with pytest.raises(http.FetchError, match="after 3 attempts"):
        http.get("https://x.test/a")
    assert len(fake.calls) == 3 and len(fake.sleeps) == 2


def test_404_not_retried(fake):
    fake.queue.append(Resp(404))
    with pytest.raises(http.FetchError) as err:
        http.get("https://x.test/a")
    assert err.value.status == 404 and len(fake.calls) == 1 and fake.sleeps == []


def test_other_4xx_not_retried(fake):
    fake.queue.append(Resp(401))
    with pytest.raises(http.FetchError):
        http.get("https://x.test/a")
    assert len(fake.calls) == 1


def test_secret_is_redacted_from_errors(fake):
    secret = "tok-123456"
    fake.queue += [requests.ConnectionError(f"url=/api?token={secret}")] * 3
    with pytest.raises(http.FetchError) as err:
        http.get("https://x.test/a", params={"token": secret}, redact=[secret])
    assert secret not in str(err.value) and "REDACTED" in str(err.value)


def test_extra_ok_status_returned(fake):
    fake.queue.append(Resp(304))
    assert http.get("https://x.test/a", ok=(200, 304)).status_code == 304
