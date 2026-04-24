#!/usr/bin/env python3
"""Daily entrypoint invoked by launchd at 10:00 local."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import storage, watchdog  # noqa: E402
from src.collector import run_daily_collection, run_dry_collection  # noqa: E402
from src.config import load_config  # noqa: E402


def setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).date().isoformat()
    logfile = logs_dir / f"{date}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(logfile),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


async def main(dry_run: bool) -> int:
    cfg = load_config()
    setup_logging(cfg.logs_dir)
    log = logging.getLogger("run_daily")

    try:
        if dry_run:
            log.info("Starting DRY-RUN collection.")
            result = await run_dry_collection(cfg)
        else:
            log.info("Starting daily collection.")
            result = await run_daily_collection(cfg)
        log.info(
            "Collection finished: %d posts from %d creators (errors=%d)",
            result.posts_collected,
            result.creators_scraped,
            len(result.errors),
        )
    except Exception as e:
        log.exception("Top-level collection failure: %s", e)
        storage.init_db(cfg.db_path)
        with storage.connect(cfg.db_path) as conn:
            now = datetime.now(timezone.utc).isoformat()
            storage.record_run(
                conn,
                run_date=datetime.now(timezone.utc).date().isoformat(),
                started_at=now,
                ended_at=now,
                status="failed",
                posts_collected=0,
                creators_scraped=0,
                errors=[{"error": "top_level_exception", "detail": str(e)}],
            )

    try:
        watchdog.check_and_alert(cfg)
    except Exception:
        log.exception("Watchdog failed")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip Playwright; exercise DB loop only.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.dry_run)))
