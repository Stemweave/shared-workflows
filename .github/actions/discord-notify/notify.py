#!/usr/bin/env python3
"""Posts one embed to a Discord webhook.

A notification must never block a merge, so delivery problems are warnings unless FAIL_ON_ERROR is
set. Mentions are switched off, so text from a pull request title can't ping @everyone.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

HOSTS = {"discord.com", "ptb.discord.com", "canary.discord.com", "discordapp.com"}
DEFAULT_COLOR = 0x5865F2


def clip(text, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def parse_fields(text: str | None) -> list[dict]:
    """One field per 'Name: value' line."""
    fields = []
    for line in (text or "").splitlines():
        name, separator, value = line.partition(": ")
        if separator and name.strip() and value.strip():
            fields.append({"name": clip(name, 256), "value": clip(value, 1024), "inline": True})
    return fields[:25]


def parse_color(text: str | None) -> int:
    try:
        value = int(str(text).strip())
    except ValueError:
        return DEFAULT_COLOR
    return value if 0 <= value <= 0xFFFFFF else DEFAULT_COLOR


def build_payload(title: str, description: str, url: str, color: int, fields: list[dict], footer: str) -> dict:
    embed: dict = {"title": clip(title, 256), "description": clip(description, 4096), "color": color}
    if url.startswith(("https://", "http://")):
        embed["url"] = url
    if fields:
        embed["fields"] = fields
    if footer.strip():
        embed["footer"] = {"text": clip(footer, 2048)}
    return {"embeds": [embed], "allowed_mentions": {"parse": []}}


def valid_webhook(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in HOSTS and parsed.path.startswith("/api/webhooks/")


def post(url: str, payload: dict, attempts: int = 3, sleep=time.sleep, opener=urllib.request.urlopen) -> None:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "stemweave-shared-workflows"}
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with opener(request, timeout=15):
                return
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read()
            error.close()
            if status == 429 and attempt < attempts:
                try:
                    wait = float(json.loads(body).get("retry_after", 2))
                except (ValueError, AttributeError):
                    wait = 2.0
                sleep(min(wait, 10.0))
            elif status >= 500 and attempt < attempts:
                sleep(2.0 * attempt)
            else:
                raise RuntimeError(f"Discord answered {status}") from None
        except urllib.error.URLError as error:
            if attempt == attempts:
                raise RuntimeError(f"could not reach Discord: {error.reason}") from None
            sleep(2.0 * attempt)


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotate(level: str, message: str) -> None:
    print(f"::{level} title=Discord notification::{_escape(message)}")


def run(env, post_fn=post) -> int:
    fail = env.get("FAIL_ON_ERROR", "false").strip().lower() in ("1", "true", "yes")
    failed = 1 if fail else 0

    url = env.get("WEBHOOK_URL", "").strip()
    if not url:
        annotate("notice", "No Discord webhook is configured, so nothing was sent.")
        return 0
    if not valid_webhook(url):
        annotate("warning", "The webhook is not a Discord webhook URL, so nothing was sent.")
        return failed

    payload = build_payload(
        env.get("TITLE", ""),
        env.get("DESCRIPTION", ""),
        env.get("URL", "").strip(),
        parse_color(env.get("COLOR")),
        parse_fields(env.get("FIELDS")),
        env.get("FOOTER", ""),
    )
    try:
        post_fn(url, payload)
    except RuntimeError as error:
        annotate("warning", f"Could not send the notification: {error}.")
        return failed
    print("Notification sent.")
    return 0


if __name__ == "__main__":
    sys.exit(run(os.environ))
