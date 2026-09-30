"""A small GitHub REST client using only the standard library.

It reads GITHUB_API_URL, so it works on github.com and on GitHub Enterprise Server.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

RETRY_STATUSES = {429, 500, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, status: int, message: str, path: str):
        super().__init__(f"GitHub API {status} for {path}: {message}")
        self.status = status
        self.message = message
        self.path = path


class GitHub:
    def __init__(self, token: str, api_url: str | None = None, attempts: int = 4, sleep=time.sleep):
        self.token = token
        self.api_url = (api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
        self.attempts = attempts
        self.sleep = sleep

    def get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params=params)[0]

    def post(self, path: str, payload: dict):
        return self._request("POST", path, payload=payload)[0]

    def patch(self, path: str, payload: dict):
        return self._request("PATCH", path, payload=payload)[0]

    def paginate(self, path: str, params: dict | None = None, max_pages: int = 50):
        """Yields every item of a list endpoint, following Link headers."""
        target, query = path, dict(params or {})
        query.setdefault("per_page", 100)
        for _ in range(max_pages):
            data, headers = self._request("GET", target, params=query)
            yield from data
            next_url = _next_link(headers.get("Link", ""))
            if not next_url:
                return
            # The link is absolute and already carries its query, which matters on GitHub Enterprise
            # Server where the API sits under /api/v3.
            target, query = next_url, None

    def _request(self, method: str, path: str, params: dict | None = None, payload: dict | None = None):
        url = path if path.startswith(("http://", "https://")) else self.api_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "stemweave-shared-workflows")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        for attempt in range(1, self.attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read()
                    return (json.loads(body) if body else None), dict(response.headers)
            except urllib.error.HTTPError as error:
                status, message = error.code, _message(error)
                retry_after = error.headers.get("Retry-After")
                error.close()
                if status in RETRY_STATUSES and attempt < self.attempts:
                    self.sleep(_backoff(attempt, retry_after))
                    continue
                raise ApiError(status, message, path) from None
            except urllib.error.URLError as error:
                if attempt < self.attempts:
                    self.sleep(_backoff(attempt, None))
                    continue
                raise ApiError(0, str(error.reason), path) from None
        raise AssertionError("unreachable")


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), 60.0)
    return min(2.0**attempt, 30.0)


def _message(error: urllib.error.HTTPError) -> str:
    try:
        return json.loads(error.read()).get("message", error.reason)
    except (ValueError, AttributeError):
        return str(error.reason)


def _next_link(header: str) -> str | None:
    for part in header.split(","):
        segments = part.split(";")
        if len(segments) > 1 and segments[1].strip() == 'rel="next"':
            return segments[0].strip().strip("<>")
    return None
