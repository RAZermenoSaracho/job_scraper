"""
Polite HTTP GET shared by the plain-HTTP adapters (ATS boards, web3.career API,
hashtagweb3 feed): descriptive User-Agent, 15 s timeout, bounded retries with
exponential backoff + jitter on 429/5xx/network errors honouring Retry-After (capped),
no retry on 4xx. Secrets passed in `redact` are scrubbed from every raised message.
"""

import logging
import os
import random
import time

import requests

log = logging.getLogger(__name__)

TIMEOUT = 15
MAX_WAIT = 60


class FetchError(RuntimeError):
    """A failed fetch; message is safe to surface (redacted, no stack)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def user_agent() -> str:
    contact = os.environ.get("JOB_SCRAPER_CONTACT")
    return "job-scraper/1.0 (personal job search" + (f"; contact {contact})" if contact else ")")


def _scrub(text: str, redact) -> str:
    for secret in redact:
        if secret:
            text = text.replace(secret, "REDACTED")
    return text


def _retry_after(response) -> float | None:
    try:
        return min(float(response.headers.get("Retry-After")), MAX_WAIT)
    except (TypeError, ValueError):
        return None


def get(url, *, params=None, headers=None, redact=(), tries=3, ok=(200,)):
    """
    GET with retries; returns the response when its status is in `ok` (default 200).
    Raises FetchError otherwise: status 404/other 4xx immediately, 429/5xx/network
    errors after `tries` attempts.
    """
    headers = {"User-Agent": user_agent(), **(headers or {})}
    last = "unknown error"
    for attempt in range(tries):
        wait = None
        try:
            response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last = _scrub(f"{type(exc).__name__}: {exc}", redact)
        else:
            status = response.status_code
            if status in ok:
                return response
            if status == 429 or status >= 500:
                last = f"HTTP {status}"
                wait = _retry_after(response)
            else:
                raise FetchError(f"HTTP {status}" + (" (not found)" if status == 404 else ""), status)
        if attempt < tries - 1:
            if wait is None:
                wait = min(2 ** attempt + random.uniform(0, 1), MAX_WAIT)
            log.warning("GET failed (%s); retrying in %.1fs", last, wait)
            time.sleep(wait)
    raise FetchError(f"{last} after {tries} attempts")
