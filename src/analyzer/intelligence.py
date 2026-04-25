"""Per-creator competitive-intelligence pack builder.

Builds a structured "data sheet" for each eligible creator — every count,
rate, correlation the markdown report will cite. The synthesizer treats
this dict as ground truth: every numeric claim in the prose has to come
from here, not from Opus's invention.

Pure functions over a sqlite3 connection. No LLM calls. No I/O outside
the DB read. Mirrors the stats.py shape so the same _stats_for_subset
aggregator powers both cohort and per-creator views.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict

from src import storage
from src.analyzer import growth as growth_mod
from src.analyzer import stats as stats_mod

log = logging.getLogger(__name__)

# A creator needs this many posts with extracted features before their
# patterns are stable enough to analyze. Below the threshold, top/bottom
# quartile splits collapse to noise.
MIN_FEATURED_POSTS = 5

# Over/under-index thresholds. A categorical value the creator uses 15
# percentage points more than the cohort baseline gets flagged as
# "over-indexed." Symmetric for under-indexed.
OVER_INDEX_THRESHOLD = 0.15

# Sample sizes for the appendix.
TOP_POSTS_IN_PACK = 5
BOTTOM_POSTS_IN_PACK = 3
TOP_GROWTH_POSTS_IN_PACK = 3
POST_TEXT_PREVIEW_CHARS = 1200


# ----------------------------------------------------------------------
# Cohort baseline
# ----------------------------------------------------------------------


def build_cohort_baseline(conn: sqlite3.Connection) -> dict:
    """Compute the cohort baseline once per run.

    Baseline = distribution of categorical features and mean of numerics
    across every eligible creator's posts (active + ≥MIN_FEATURED_POSTS
    featured posts). Per-creator deltas reference this baseline so
    "over-indexed" means "vs the analyzable cohort," not "vs every post
    we've ever seen."
    """
    eligible_ids = _eligible_creator_ids(conn)
    if not eligible_ids:
        return {
            "n_creators_in_baseline": 0,
            "n_posts_in_baseline": 0,
            "categorical_distribution": {},
            "numeric_means": {},
            "engagement_median": None,
        }

    placeholders = ",".join("?" * len(eligible_ids))
    rows = conn.execute(
        f"""
        SELECT p.engagement_score, pf.features_json
        FROM posts p
        JOIN post_features pf ON pf.post_id = p.id
        WHERE p.creator_id IN ({placeholders})
          AND p.engagement_score IS NOT NULL
        """,
        tuple(eligible_ids),
    ).fetchall()

    cat_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    num_sums: dict[str, list[float]] = defaultdict(list)
    scores: list[float] = []

    cat_keys = stats_mod.CATEGORICAL_KEYS | stats_mod.BOOLEAN_KEYS

    for r in rows:
        feats = _safe_loads(r["features_json"])
        if feats is None:
            continue
        scores.append(float(r["engagement_score"]))
        for k in cat_keys:
            v = feats.get(k)
            if v is None:
                continue
            cat_counts[k][str(v)] += 1
        for k in stats_mod.NUMERIC_KEYS:
            v = feats.get(k)
            if isinstance(v, (int, float)):
                num_sums[k].append(float(v))

    cat_dist: dict[str, dict[str, float]] = {}
    for k, vals in cat_counts.items():
        total = sum(vals.values())
        if total == 0:
            continue
        cat_dist[k] = {val: count / total for val, count in vals.items()}

    num_means = {k: sum(vs) / len(vs) for k, vs in num_sums.items() if vs}

    return {
        "n_creators_in_baseline": len(eligible_ids),
        "n_posts_in_baseline": len(rows),
        "categorical_distribution": cat_dist,
        "numeric_means": num_means,
        "engagement_median": _median(scores),
    }


# ----------------------------------------------------------------------
# Per-creator pack
# ----------------------------------------------------------------------


def build_intelligence_pack(
    conn: sqlite3.Connection,
    creator_id: int,
    baseline: dict,
) -> dict | None:
    """Return None for ineligible creators (or skip without raising)."""
    creator_row = conn.execute(
        "SELECT id, display_name, linkedin_url, current_follower_count "
        "FROM creators WHERE id = ?",
        (creator_id,),
    ).fetchone()
    if creator_row is None:
        return None

    parsed = _parsed_posts_for_creator(conn, creator_id)
    if len(parsed) < MIN_FEATURED_POSTS:
        return None

    snapshot = storage.latest_profile_snapshot(conn, creator_id)
    snapshots_all = storage.list_profile_snapshots(conn, creator_id)
    snaps_normalized = growth_mod.normalize_snapshots(snapshots_all)
    growth_rate = growth_mod.compute_growth_rate(snaps_normalized)

    profile_features_row = conn.execute(
        "SELECT features_json, extracted_at FROM profile_features WHERE creator_id = ?",
        (creator_id,),
    ).fetchone()
    profile_features = (
        _safe_loads(profile_features_row["features_json"])
        if profile_features_row is not None
        else None
    )

    parsed_sorted = sorted(parsed, key=lambda t: t[0], reverse=True)
    self_breakdown = stats_mod._stats_for_subset(parsed_sorted)

    cohort_delta = _cohort_delta(
        parsed=parsed_sorted,
        baseline=baseline,
    )

    top_5 = _format_post_samples(
        conn,
        creator_id=creator_id,
        order_by="engagement_score DESC",
        limit=TOP_POSTS_IN_PACK,
    )
    bottom_3 = _format_post_samples(
        conn,
        creator_id=creator_id,
        order_by="engagement_score ASC",
        limit=BOTTOM_POSTS_IN_PACK,
        require_score=True,
    )

    growth_block = _growth_correlation(conn, creator_id)

    scores_only = [s for s, _ in parsed_sorted]
    pack = {
        "creator": {
            "id": creator_row["id"],
            "display_name": creator_row["display_name"],
            "linkedin_url": creator_row["linkedin_url"],
            "follower_count_latest": (
                snapshot["follower_count"] if snapshot else creator_row["current_follower_count"]
            ),
            "growth_rate_per_week": growth_rate,
            "snapshots_n": len(snapshots_all),
        },
        "profile": {
            "headline": snapshot["headline"] if snapshot else None,
            "about_text": snapshot["about_text"] if snapshot else None,
            "current_role": snapshot["current_role"] if snapshot else None,
            "current_company": snapshot["current_company"] if snapshot else None,
            "location": snapshot["location"] if snapshot else None,
            "features": profile_features,
        },
        "posts": {
            "n_total": _post_count(conn, creator_id),
            "n_with_features": len(parsed_sorted),
            "engagement": {
                "median": _median(scores_only),
                "p25": _quantile(scores_only, 0.25),
                "p75": _quantile(scores_only, 0.75),
                "cohort_median": baseline.get("engagement_median"),
            },
        },
        "self_topquartile_vs_bottomquartile": self_breakdown,
        "cohort_delta": cohort_delta,
        "growth_correlation": growth_block,
        "top_5_posts": top_5,
        "bottom_3_posts": bottom_3,
    }
    return pack


# ----------------------------------------------------------------------
# Eligibility + helpers
# ----------------------------------------------------------------------


def eligible_creator_ids(conn: sqlite3.Connection) -> list[int]:
    """Public alias for the runner — list of creator_ids that pass the
    ≥MIN_FEATURED_POSTS gate."""
    return _eligible_creator_ids(conn)


def _eligible_creator_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        """
        SELECT c.id
        FROM creators c
        WHERE c.active = 1
          AND (
            SELECT COUNT(*)
            FROM posts p
            JOIN post_features pf ON pf.post_id = p.id
            WHERE p.creator_id = c.id
              AND p.engagement_score IS NOT NULL
          ) >= ?
        ORDER BY c.id
        """,
        (MIN_FEATURED_POSTS,),
    ).fetchall()
    return [r["id"] for r in rows]


def _parsed_posts_for_creator(
    conn: sqlite3.Connection, creator_id: int
) -> list[tuple[float, dict]]:
    rows = conn.execute(
        """
        SELECT p.engagement_score, pf.features_json
        FROM posts p
        JOIN post_features pf ON pf.post_id = p.id
        WHERE p.creator_id = ?
          AND p.engagement_score IS NOT NULL
        """,
        (creator_id,),
    ).fetchall()
    parsed: list[tuple[float, dict]] = []
    for r in rows:
        feats = _safe_loads(r["features_json"])
        if feats is None:
            continue
        parsed.append((float(r["engagement_score"]), feats))
    return parsed


def _post_count(conn: sqlite3.Connection, creator_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE creator_id = ?",
        (creator_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _format_post_samples(
    conn: sqlite3.Connection,
    *,
    creator_id: int,
    order_by: str,
    limit: int,
    require_score: bool = True,
) -> list[dict]:
    where = "p.creator_id = ?"
    params: list = [creator_id]
    if require_score:
        where += " AND p.engagement_score IS NOT NULL"
    rows = conn.execute(
        f"""
        SELECT p.id, p.post_text, p.engagement_score, p.reactions, p.comments,
               p.reshares, p.follower_count_at_collection, p.growth_7d,
               pf.features_json
        FROM posts p
        LEFT JOIN post_features pf ON pf.post_id = p.id
        WHERE {where}
        ORDER BY {order_by}
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "post_id": r["id"],
                "text": (r["post_text"] or "")[:POST_TEXT_PREVIEW_CHARS],
                "engagement_score": r["engagement_score"],
                "reactions": r["reactions"],
                "comments": r["comments"],
                "reshares": r["reshares"],
                "follower_count_at_collection": r["follower_count_at_collection"],
                "growth_7d": r["growth_7d"],
                "features": _safe_loads(r["features_json"]),
            }
        )
    return out


def _growth_correlation(conn: sqlite3.Connection, creator_id: int) -> dict:
    rows = conn.execute(
        """
        SELECT p.id, p.post_text, p.growth_7d, p.engagement_score, pf.features_json
        FROM posts p
        LEFT JOIN post_features pf ON pf.post_id = p.id
        WHERE p.creator_id = ?
          AND p.growth_7d IS NOT NULL
        ORDER BY p.growth_7d DESC
        LIMIT ?
        """,
        (creator_id, TOP_GROWTH_POSTS_IN_PACK),
    ).fetchall()
    top_growth: list[dict] = []
    feature_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    cat_keys = stats_mod.CATEGORICAL_KEYS | stats_mod.BOOLEAN_KEYS
    for r in rows:
        feats = _safe_loads(r["features_json"])
        top_growth.append(
            {
                "post_id": r["id"],
                "text": (r["post_text"] or "")[:POST_TEXT_PREVIEW_CHARS],
                "growth_7d": r["growth_7d"],
                "engagement_score": r["engagement_score"],
                "features": feats,
            }
        )
        if feats:
            for k in cat_keys:
                v = feats.get(k)
                if v is None:
                    continue
                feature_counts[k][str(v)] += 1
    return {
        "top_growth_posts": top_growth,
        "patterns_in_top_growth": {k: dict(v) for k, v in feature_counts.items()},
    }


def _cohort_delta(parsed: list[tuple[float, dict]], baseline: dict) -> dict:
    """For each categorical/boolean key, compare the creator's distribution
    against the cohort baseline. Flag values where the gap exceeds
    OVER_INDEX_THRESHOLD."""
    cohort_dist = baseline.get("categorical_distribution", {})
    cat_keys = stats_mod.CATEGORICAL_KEYS | stats_mod.BOOLEAN_KEYS

    out: dict[str, dict] = {}
    for k in cat_keys:
        their_counts: dict[str, int] = defaultdict(int)
        total = 0
        for _, feats in parsed:
            v = feats.get(k)
            if v is None:
                continue
            their_counts[str(v)] += 1
            total += 1
        if total == 0:
            continue
        their_dist = {val: count / total for val, count in their_counts.items()}
        cohort_for_key = cohort_dist.get(k, {})
        over: list[str] = []
        under: list[str] = []
        # Check every value seen by either the creator or the cohort.
        all_vals = set(their_dist) | set(cohort_for_key)
        for val in all_vals:
            their_pct = their_dist.get(val, 0.0)
            cohort_pct = cohort_for_key.get(val, 0.0)
            if their_pct - cohort_pct >= OVER_INDEX_THRESHOLD:
                over.append(val)
            elif cohort_pct - their_pct >= OVER_INDEX_THRESHOLD:
                under.append(val)
        out[k] = {
            "their": their_dist,
            "cohort": cohort_for_key,
            "over_indexed": sorted(over),
            "under_indexed": sorted(under),
        }
    return out


# ----------------------------------------------------------------------
# Tiny utilities
# ----------------------------------------------------------------------


def _safe_loads(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        v = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return v if isinstance(v, dict) else None


def _median(xs: list[float]) -> float | None:
    return _quantile(xs, 0.5)


def _quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    idx = int(round(q * (len(s) - 1)))
    return s[idx]
