#!/usr/bin/env python3
"""Test + setup helper for Telegram alerts.

Modes:
  (default)    Send a test ping to the configured TELEGRAM_CHAT_ID.
  --discover   Print chat_ids from the bot's recent updates. Use this after
               messaging your bot for the first time, to find the id to put
               in .env.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

from src.config import load_config  # noqa: E402
from src.watchdog import send_test_alert  # noqa: E402


def discover(bot_token: str) -> int:
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    try:
        resp = requests.get(url, timeout=10)
    except Exception as e:
        print(f"getUpdates request failed: {e}", file=sys.stderr)
        return 2
    if resp.status_code >= 300:
        print(f"getUpdates HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return 2

    data = resp.json()
    if not data.get("ok"):
        print(f"Telegram replied not-ok: {data}", file=sys.stderr)
        return 2

    seen: dict[str, str] = {}
    for update in data.get("result", []):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        label = chat.get("title") or chat.get("username") or chat.get("first_name") or "?"
        seen[str(cid)] = f"{label} (type={chat.get('type')})"

    if not seen:
        print(
            "No chat_ids found. Send any message to your bot in Telegram, "
            "then re-run this command.",
            file=sys.stderr,
        )
        return 1

    print("chat_id    label")
    for cid, label in seen.items():
        print(f"{cid}  {label}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Print chat_ids from getUpdates instead of sending a ping.",
    )
    args = parser.parse_args()

    cfg = load_config()

    if args.discover:
        token = cfg.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            print("TELEGRAM_BOT_TOKEN is not set in .env or the environment.", file=sys.stderr)
            return 1
        return discover(token)

    if not cfg.telegram_configured:
        print(
            "Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.\n"
            "Run with --discover to find your chat_id after messaging the bot.",
            file=sys.stderr,
        )
        return 1

    ok, detail = send_test_alert(cfg)
    print(f"{'OK' if ok else 'FAIL'}: {detail}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
