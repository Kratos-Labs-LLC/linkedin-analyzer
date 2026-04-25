"""Follower-growth attribution.

Profile snapshots are captured on every scrape (~every 2-3 days per creator
under default rotation). This module turns that time series into two derived
signals:

  - per-creator: `growth_rate_per_week` — least-squares slope over the
    snapshot series. Stable enough on ~12 snapshots over 30 days to
    quartile-rank creators by pull.
  - per-post: `growth_7d` — follower delta in the configurable window
    after the post. The cleanest "this post pulled people to the
    profile" proxy we can derive from public data.

Pure functions only. Storage I/O happens in `recompute_post_growth` and is
the only function that touches the DB.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src import storage
from src.config import AppConfig

log = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 7
SECONDS_PER_WEEK = 7 * 24 * 3600


@dataclass(frozen=True)
class Snapshot:
    """Lightweight snapshot — just what the math needs."""

    snapshot_at: datetime  # tz-aware
    follower_count: int


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_snapshots(rows: list) -> list[Snapshot]:
    """Take sqlite3.Row-like objects with snapshot_at + follower_count and
    return tz-aware Snapshot dataclasses, oldest first, dropping nulls."""
    out: list[Snapshot] = []
    for r in rows:
        ts = _parse_iso(r["snapshot_at"])
        fc = r["follower_count"]
        if ts is None or fc is None:
            continue
        out.append(Snapshot(ts, int(fc)))
    out.sort(key=lambda s: s.snapshot_at)
    return out


def compute_growth_rate(snapshots: list[Snapshot]) -> float | None:
    """Followers gained per week, via least-squares fit over the snapshot
    series. Returns None when the series is too short or all timestamps
    collapse to a single point.

    Uses x = seconds-since-first-snapshot to avoid float-blow-up on epoch
    timestamps; result rescaled to followers/week.
    """
    n = len(snapshots)
    if n < 2:
        return None
    base = snapshots[0].snapshot_at
    xs = [(s.snapshot_at - base).total_seconds() for s in snapshots]
    ys = [s.follower_count for s in snapshots]
    if max(xs) - min(xs) <= 0:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope_per_second = num / den
    return slope_per_second * SECONDS_PER_WEEK


def compute_proximity_growth(
    post_date: str | datetime,
    snapshots: list[Snapshot],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> int | None:
    """Follower delta in the `window_days` after a post.

    `post_date` defines the start of the window. We compare:
      - baseline: latest snapshot at or before `post_date` (or the
        earliest snapshot if none precede; better than nothing).
      - endpoint: latest snapshot at or before `post_date + window_days`.

    Returns None if we don't have enough data on either side to attribute.
    """
    if isinstance(post_date, str):
        anchor = _parse_iso(post_date)
        if anchor is None:
            return None
    else:
        anchor = post_date if post_date.tzinfo else post_date.replace(tzinfo=timezone.utc)

    if not snapshots:
        return None

    end = anchor + timedelta(days=window_days)

    before = [s for s in snapshots if s.snapshot_at <= anchor]
    after = [s for s in snapshots if s.snapshot_at <= end]

    baseline = before[-1] if before else snapshots[0]
    endpoint = after[-1] if after else None

    if endpoint is None or endpoint.snapshot_at <= baseline.snapshot_at:
        return None
    return endpoint.follower_count - baseline.follower_count


def recompute_post_growth(
    cfg: AppConfig,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict:
    """Bulk-update posts.growth_7d for every post that has a non-null
    post_date and a creator with at least 2 snapshots. Idempotent.

    Returns a small summary dict (`updated`, `skipped`, `creators`) for
    logging. Safe to re-run after any new collection day.
    """
    storage.init_db(cfg.db_path)
    updated = 0
    skipped = 0
    creators_with_snapshots = 0

    with storage.connect(cfg.db_path) as conn:
        creator_rows = conn.execute(
            "SELECT id FROM creators WHERE active = 1"
        ).fetchall()

        for c in creator_rows:
            cid = c["id"]
            snap_rows = storage.list_profile_snapshots(conn, cid)
            snaps = normalize_snapshots(snap_rows)
            if len(snaps) < 2:
                continue
            creators_with_snapshots += 1

            posts = conn.execute(
                "SELECT id, post_date FROM posts WHERE creator_id = ?",
                (cid,),
            ).fetchall()
            for p in posts:
                if not p["post_date"]:
                    skipped += 1
                    continue
                growth = compute_proximity_growth(
                    p["post_date"], snaps, window_days=window_days
                )
                conn.execute(
                    "UPDATE posts SET growth_7d = ? WHERE id = ?",
                    (growth, p["id"]),
                )
                if growth is not None:
                    updated += 1
                else:
                    skipped += 1

    return {
        "updated": updated,
        "skipped": skipped,
        "creators_with_snapshots": creators_with_snapshots,
        "window_days": window_days,
    }


def creator_growth_rates(cfg: AppConfig) -> dict[int, float | None]:
    """Map of creator_id -> growth_rate_per_week. Used by profile_stats."""
    out: dict[int, float | None] = {}
    with storage.connect(cfg.db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM creators WHERE active = 1"
        ).fetchall()
        for r in rows:
            cid = r["id"]
            snaps = normalize_snapshots(storage.list_profile_snapshots(conn, cid))
            out[cid] = compute_growth_rate(snaps)
    return out
