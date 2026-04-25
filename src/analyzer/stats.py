"""Correlation analysis on extracted post_features. Pure Python — no NumPy."""
from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from pathlib import Path

from src import storage
from src.config import AppConfig

log = logging.getLogger(__name__)

# Keys we know are categorical vs numeric in the feature schema.
CATEGORICAL_KEYS = {
    "hook_type",
    "paragraph_style",
    "list_style",
    "cta_type",
    "emotional_register",
    "narrative_arc",
    "opens_with_pronoun",
    "ends_with",
    "topic_category",
}
NUMERIC_KEYS = {
    "opening_word_count",
    "total_word_count",
    "line_break_count",
    "emoji_count",
    "specificity_score",
    "controversy_score",
}
BOOLEAN_KEYS = {"uses_list", "uses_emojis"}

# Topics with fewer than this many posts are not stratified — too noisy to
# warrant a per-topic table in the synthesizer.
MIN_POSTS_PER_TOPIC = 8


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _quartile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = int(q * (len(sorted_vals) - 1))
    return sorted_vals[idx]


def _stats_for_subset(
    parsed: list[tuple[float, dict]],
    *,
    categorical_keys: set[str] | None = None,
    numeric_keys: set[str] | None = None,
    boolean_keys: set[str] | None = None,
) -> dict:
    """Compute the categorical/numeric/proof breakdown for a subset of items.

    `parsed` is an already-sorted-desc list of (score, features_dict). The
    score is engagement_score for posts, growth_rate_per_week for profiles —
    same math, different key sets.

    Optional key-set kwargs let the profile axis (and any future axis) reuse
    this aggregation without copy-pasting it. Defaults match the post-feature
    schema for backward compatibility.
    """
    cat_keys = categorical_keys if categorical_keys is not None else CATEGORICAL_KEYS
    num_keys = numeric_keys if numeric_keys is not None else NUMERIC_KEYS
    bool_keys = boolean_keys if boolean_keys is not None else BOOLEAN_KEYS

    if not parsed:
        return {
            "n_posts_analyzed": 0,
            "top_quartile_n": 0,
            "bottom_quartile_n": 0,
            "categorical_features": {},
            "numeric_features": {},
            "proof_elements_frequency": {"top_quartile": {}, "bottom_quartile": {}},
        }

    n = len(parsed)
    cutoff = max(1, int(n * 0.5))
    top_q = parsed[:cutoff]
    bottom_q = parsed[cutoff:]

    # Categorical features
    categorical: dict[str, dict] = {}
    for key in cat_keys | bool_keys:
        buckets: dict[str, list[float]] = defaultdict(list)
        for score, feats in parsed:
            v = feats.get(key)
            if v is None:
                continue
            buckets[str(v)].append(score)
        if not buckets:
            continue
        per_cat = {
            val: {"n": len(scores), "mean_engagement": sum(scores) / len(scores)}
            for val, scores in buckets.items()
        }
        ranked = sorted(per_cat.items(), key=lambda kv: kv[1]["mean_engagement"], reverse=True)
        for rank, (val, _) in enumerate(ranked, start=1):
            per_cat[val]["rank"] = rank
        categorical[key] = per_cat

    # Numeric features
    numeric: dict[str, dict] = {}
    for key in num_keys:
        xs: list[float] = []
        ys: list[float] = []
        top_vals: list[float] = []
        bot_vals: list[float] = []
        for score, feats in parsed:
            v = feats.get(key)
            if not isinstance(v, (int, float)):
                continue
            xs.append(float(v))
            ys.append(score)
        for score, feats in top_q:
            v = feats.get(key)
            if isinstance(v, (int, float)):
                top_vals.append(float(v))
        for score, feats in bottom_q:
            v = feats.get(key)
            if isinstance(v, (int, float)):
                bot_vals.append(float(v))
        if not xs:
            continue
        numeric[key] = {
            "correlation": _pearson(xs, ys),
            "top_quartile_mean": sum(top_vals) / len(top_vals) if top_vals else None,
            "bottom_quartile_mean": sum(bot_vals) / len(bot_vals) if bot_vals else None,
            "n": len(xs),
        }

    # Array features (proof_elements) — count frequency in top vs bottom
    proof_top: dict[str, int] = defaultdict(int)
    proof_bot: dict[str, int] = defaultdict(int)
    for score, feats in top_q:
        vals = feats.get("proof_elements") or []
        if isinstance(vals, list):
            for v in vals:
                proof_top[str(v)] += 1
    for score, feats in bottom_q:
        vals = feats.get("proof_elements") or []
        if isinstance(vals, list):
            for v in vals:
                proof_bot[str(v)] += 1

    return {
        "n_posts_analyzed": n,
        "top_quartile_n": len(top_q),
        "bottom_quartile_n": len(bottom_q),
        "categorical_features": categorical,
        "numeric_features": numeric,
        "proof_elements_frequency": {
            "top_quartile": dict(proof_top),
            "bottom_quartile": dict(proof_bot),
        },
    }


def compute_stats(cfg: AppConfig, min_posts_per_topic: int = MIN_POSTS_PER_TOPIC) -> dict:
    with storage.connect(cfg.db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.engagement_score, pf.features_json
            FROM posts p JOIN post_features pf ON pf.post_id = p.id
            WHERE p.engagement_score IS NOT NULL
            """
        ).fetchall()

    if not rows:
        log.warning("No rows with features + engagement scores.")
        return {"n_posts_analyzed": 0}

    parsed: list[tuple[float, dict]] = []
    for r in rows:
        try:
            feats = json.loads(r["features_json"])
        except json.JSONDecodeError:
            continue
        parsed.append((r["engagement_score"], feats))

    parsed.sort(key=lambda t: t[0], reverse=True)

    pooled = _stats_for_subset(parsed)

    # Per-topic stratification — pooled findings can be misleading when hooks
    # that win for `ai` lose for `personal_brand`. Synthesizer uses these to
    # author per-topic hook tables.
    by_topic_groups: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for score, feats in parsed:
        topic = feats.get("topic_category") or "other"
        by_topic_groups[str(topic)].append((score, feats))

    by_topic: dict[str, dict] = {}
    skipped_topics: dict[str, int] = {}
    for topic, group in by_topic_groups.items():
        if len(group) < min_posts_per_topic:
            skipped_topics[topic] = len(group)
            continue
        # Already sorted: each group preserves the parent sort order.
        by_topic[topic] = _stats_for_subset(group)

    return {
        **pooled,
        "by_topic": by_topic,
        "topics_skipped_min_n": skipped_topics,
        "min_posts_per_topic": min_posts_per_topic,
        "note": (
            "by_topic[<topic>].top_quartile_mean / bottom_quartile_mean are "
            "computed within each topic's own subset, not against the global "
            "engagement distribution. Treat per-topic numbers as relative "
            "rankings inside that topic, not as absolute thresholds."
        ),
    }


def write_stats_json(stats: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "stats.json"
    path.write_text(json.dumps(stats, indent=2))
    return path


def write_top_bottom_markdown(cfg: AppConfig, output_dir: Path, limit: int = 50) -> tuple[Path, Path]:
    """Emits top_posts.md and bottom_posts.md — up to `limit` posts each."""
    output_dir.mkdir(parents=True, exist_ok=True)

    with storage.connect(cfg.db_path) as conn:
        top = conn.execute(
            """
            SELECT p.*, c.display_name AS creator_name, c.linkedin_url AS creator_url
            FROM posts p JOIN creators c ON c.id = p.creator_id
            WHERE p.engagement_score IS NOT NULL
            ORDER BY p.engagement_score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        bottom = conn.execute(
            """
            SELECT p.*, c.display_name AS creator_name, c.linkedin_url AS creator_url
            FROM posts p JOIN creators c ON c.id = p.creator_id
            WHERE p.engagement_score IS NOT NULL
            ORDER BY p.engagement_score ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def render(rows, title: str) -> str:
        out = [f"# {title}", ""]
        for r in rows:
            out.append(
                f"## {r['creator_name']} — score {r['engagement_score']:.4f}"
            )
            out.append(
                f"reactions={r['reactions']} comments={r['comments']} reshares={r['reshares']} "
                f"followers={r['follower_count_at_collection']}"
            )
            out.append("")
            out.append(r["post_text"])
            out.append("")
            out.append("---")
            out.append("")
        return "\n".join(out)

    top_path = output_dir / "top_posts.md"
    bot_path = output_dir / "bottom_posts.md"
    top_path.write_text(render(top, "Top posts by normalized engagement"))
    bot_path.write_text(render(bottom, "Bottom posts by normalized engagement"))
    return top_path, bot_path


def write_top_growth_markdown(
    cfg: AppConfig, output_dir: Path, *, limit: int = 30
) -> Path:
    """Top posts by growth_7d (follower-pull proxy). Used by the post
    synthesizer's 'patterns that pull readers to the profile' section.

    Returns a path even if no posts qualify — the file just contains a
    note. The synthesizer can detect emptiness and skip the section.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    with storage.connect(cfg.db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.*, c.display_name AS creator_name, c.linkedin_url AS creator_url
            FROM posts p JOIN creators c ON c.id = p.creator_id
            WHERE p.growth_7d IS NOT NULL
            ORDER BY p.growth_7d DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    path = output_dir / "top_growth_posts.md"
    if not rows:
        path.write_text(
            "# Top posts by 7-day follower growth\n\n"
            "_No posts with growth_7d yet — needs at least 2 profile snapshots "
            "per creator and posts with parseable post_date._\n"
        )
        return path

    out = ["# Top posts by 7-day follower growth", ""]
    for r in rows:
        score_str = (
            f"{r['engagement_score']:.4f}"
            if r["engagement_score"] is not None
            else "—"
        )
        out.append(
            f"## {r['creator_name']} — +{r['growth_7d']} followers in 7d "
            f"(engagement {score_str})"
        )
        out.append(
            f"reactions={r['reactions']} comments={r['comments']} "
            f"reshares={r['reshares']} followers={r['follower_count_at_collection']}"
        )
        out.append("")
        out.append(r["post_text"])
        out.append("")
        out.append("---")
        out.append("")
    path.write_text("\n".join(out))
    return path
