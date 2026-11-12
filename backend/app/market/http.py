"""Tiny HTTP helper: every remote (non-yfinance) call goes through here -> timeouts, retry, browser UA."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("market.http")

DEFAULT_TIMEOUT = 20.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class RemoteError(RuntimeError):
    """Raised when a remote source could not be fetched/parsed. Always caught by jobs."""


def request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json: Any = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 2,
    backoff: float = 1.5,
) -> httpx.Response:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
    if headers:
        hdrs.update(headers)
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=hdrs) as client:
                resp = client.request(method, url, params=params, json=json)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                raise RemoteError(f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise RemoteError(f"HTTP {resp.status_code} for {url}")
            return resp
        except (httpx.HTTPError, RemoteError) as exc:
            last = exc
            if isinstance(exc, RemoteError) and "for " in str(exc):   # definitive 4xx -> no retry
                break
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise RemoteError(f"{method} {url} failed: {last}")


def get_bytes(url: str, **kw) -> bytes:
    return request("GET", url, **kw).content


def get_text(url: str, **kw) -> str:
    return request("GET", url, **kw).text


def get_json(url: str, **kw) -> Any:
    resp = request("GET", url, **kw)
    try:
        return resp.json()
    except ValueError as exc:
        raise RemoteError(f"invalid JSON from {url}: {exc}") from exc


def post_json(url: str, payload: Any, **kw) -> Any:
    resp = request("POST", url, json=payload, **kw)
    try:
        return resp.json()
    except ValueError as exc:
        raise RemoteError(f"invalid JSON from {url}: {exc}") from exc
