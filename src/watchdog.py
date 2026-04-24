"""Post-run alerting. §6.4 rules: 2+ consecutive non-success, <20 posts/run, auth_error."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from src import storage
from src.config import AppConfig

log = logging.getLogger(__name__)

MIN_POSTS_PER_RUN = 20
STALE_HOURS_THRESHOLD = 24


@dataclass
class Alert:
    reason: str
    summary: str
    suggested_action: str


def _rules(conn) -> list[Alert]:
    runs = storage.recent_runs(conn, limit=3)
    alerts: list[Alert] = []
    if not runs:
        return alerts

    # Rule: 0 posts for >= 24h
    latest = runs[0]
    ended_at = latest["ended_at"] or latest["started_at"]
    if ended_at:
        try:
            last_ts = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            stale = datetime.now(timezone.utc) - last_ts
            if stale > timedelta(hours=STALE_HOURS_THRESHOLD) and latest["posts_collected"] == 0:
                alerts.append(Alert(
                    reason="no_posts_24h",
                    summary=f"No posts collected in {stale}.",
                    suggested_action="Verify scheduler is running and auth is valid.",
                ))
        except ValueError:
            pass

    # Rule: auth_error in most recent run
    if latest["status"] == "auth_error":
        alerts.append(Alert(
            reason="auth_error",
            summary="Most recent run hit an auth wall.",
            suggested_action="Run `python scripts/auth_setup.py` and re-solve any challenge.",
        ))

    # Rule: 2+ consecutive non-success
    non_success = [r for r in runs if r["status"] != "success"]
    if len(non_success) >= 2 and len(runs) >= 2:
        alerts.append(Alert(
            reason="consecutive_failures",
            summary="Last 2+ runs did not finish as success.",
            suggested_action="Check logs/ for exceptions and verify LinkedIn DOM hasn't shifted.",
        ))

    # Rule: very low post count on latest successful-ish run
    if latest["status"] in ("success", "partial") and (latest["posts_collected"] or 0) < MIN_POSTS_PER_RUN:
        alerts.append(Alert(
            reason="low_yield",
            summary=f"Last run collected only {latest['posts_collected']} posts (threshold {MIN_POSTS_PER_RUN}).",
            suggested_action="Check selectors in src/parser.py — LinkedIn DOM may have changed.",
        ))

    return alerts


def _format_alert(alerts: list[Alert], runs_summary: list[dict]) -> str:
    lines = ["LinkedIn Analyzer alert"]
    for a in alerts:
        lines.append(f"- [{a.reason}] {a.summary} -> {a.suggested_action}")
    lines.append("")
    lines.append("Recent runs:")
    for r in runs_summary:
        lines.append(
            f"  {r['run_date']} status={r['status']} posts={r['posts_collected']} "
            f"creators={r['creators_scraped']}"
        )
    return "\n".join(lines)


def _post_to_slack(webhook_url: str, message: str) -> None:
    try:
        resp = requests.post(webhook_url, json={"text": message}, timeout=10)
        if resp.status_code >= 300:
            log.warning("Slack webhook returned %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("Slack post failed: %s", e)


def check_and_alert(cfg: AppConfig) -> list[Alert]:
    with storage.connect(cfg.db_path) as conn:
        alerts = _rules(conn)
        recent = [dict(r) for r in storage.recent_runs(conn, limit=3)]

    if not alerts:
        log.info("Watchdog: no alerts.")
        return alerts

    message = _format_alert(alerts, recent)
    log.warning("Watchdog alert:\n%s", message)
    if cfg.slack_webhook_url:
        _post_to_slack(cfg.slack_webhook_url, message)
    return alerts
