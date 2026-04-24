#!/usr/bin/env python3
"""One-time manual login: opens a headed browser into the persistent profile.

Procedure:
  1. Run this script.
  2. Log in to LinkedIn on the BURNER account in the opened browser.
  3. Solve any captcha / device-check LinkedIn presents.
  4. Once you see your feed, return to the terminal and press Enter.
  5. The session persists in ./chrome_profile/ for the collector to reuse.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.collector import AUTH_SENTINEL  # noqa: E402
from src.config import load_config  # noqa: E402


async def main() -> int:
    from playwright.async_api import async_playwright

    cfg = load_config()
    cfg.chrome_profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"Opening persistent Chromium profile at {cfg.chrome_profile_dir}")
    print("Log in to the burner LinkedIn account, then press Enter in this terminal.")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(cfg.chrome_profile_dir),
            headless=False,
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        await asyncio.get_event_loop().run_in_executor(None, input, "Press Enter when logged in... ")

        sentinel = cfg.chrome_profile_dir / AUTH_SENTINEL
        sentinel.write_text("authed")
        print(f"Wrote sentinel {sentinel}. Closing browser.")
        await context.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
