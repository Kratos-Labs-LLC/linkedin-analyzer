"""Tests for src/analyzer/intelligence.py — the per-creator pack builder.

Seeds a small DB with multiple creators of varying post volumes and feature
distributions, then asserts:
  - eligibility filters out under-data creators
  - quantiles match a manual compute
  - cohort_delta flags over/under-indexed values correctly
  - top_5/bottom_3 sample shape is correct
  - growth_correlation surfaces the right top-growth posts
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import storage
from src.analyzer import intelligence
from src.config import CreatorConfig


def _seed_creator(conn, url: str, name: str, weight: float = 1.0) -> int:
    """Direct insert — avoids sync_creators' side effect of deactivating
    every other creator not present in the call's input list."""
    conn.execute(
        """
        INSERT INTO creators (linkedin_url, display_name, weight, active)
        VALUES (?, ?, ?, 1)
        """,
        (url, name, weight),
    )
    cid_row = conn.execute(
        "SELECT id FROM creators WHERE linkedin_url = ?", (url,)
    ).fetchone()
    return int(cid_row["id"])


def _add_post(
    conn,
    *,
    creator_id: int,
    post_urn: str,
    reactions: int,
    features: dict,
    post_date: str = "2026-04-10T00:00:00Z",
    follower_count: int = 1000,
    growth_7d: int | None = None,
) -> int:
    storage.insert_post(
        conn,
        post_urn=post_urn,
        creator_id=creator_id,
        post_url=None,
        post_text=f"text for {post_urn}",
        reactions=reactions,
        comments=2,
        reshares=1,
        follower_count_at_collection=follower_count,
        post_date=post_date,
    )
    pid = conn.execute(
        "SELECT id FROM posts WHERE post_urn = ?", (post_urn,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT OR REPLACE INTO post_features (post_id, features_json) VALUES (?, ?)",
        (pid, json.dumps(features)),
    )
    if growth_7d is not None:
        conn.execute("UPDATE posts SET growth_7d = ? WHERE id = ?", (growth_7d, pid))
    return pid


def _features(
    *,
    hook: str = "bold_claim",
    paragraph: str = "short_chunks",
    cta: str = "question_prompt",
    register: str = "practical",
    arc: str = "none",
    pronoun: str = "i",
    ends: str = "statement",
    topic: str = "agency_ops",
    list_style: str = "none",
    uses_list: bool = False,
    uses_emojis: bool = False,
    opening_word_count: int = 8,
    total_word_count: int = 80,
    line_break_count: int = 2,
    emoji_count: int = 0,
    specificity: int = 5,
    controversy: int = 3,
    proof: list[str] | None = None,
) -> dict:
    return {
        "hook_type": hook,
        "paragraph_style": paragraph,
        "cta_type": cta,
        "emotional_register": register,
        "narrative_arc": arc,
        "opens_with_pronoun": pronoun,
        "ends_with": ends,
        "topic_category": topic,
        "list_style": list_style,
        "uses_list": uses_list,
        "uses_emojis": uses_emojis,
        "opening_word_count": opening_word_count,
        "total_word_count": total_word_count,
        "line_break_count": line_break_count,
        "emoji_count": emoji_count,
        "specificity_score": specificity,
        "controversy_score": controversy,
        "proof_elements": proof or ["none"],
    }


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    storage.init_db(path)
    return path


def test_eligibility_excludes_creators_with_few_features(db: Path):
    """Below MIN_FEATURED_POSTS, the creator is ineligible — packs return
    None and the eligible_creator_ids list omits them."""
    with storage.connect(db) as conn:
        light = _seed_creator(conn, "https://linkedin.com/in/light", "Light")
        heavy = _seed_creator(conn, "https://linkedin.com/in/heavy", "Heavy")
        # Light creator: 3 posts (under threshold)
        for i in range(3):
            _add_post(
                conn,
                creator_id=light,
                post_urn=f"urn:li:l:{i}",
                reactions=10,
                features=_features(),
            )
        # Heavy creator: 6 posts (above threshold)
        for i in range(6):
            _add_post(
                conn,
                creator_id=heavy,
                post_urn=f"urn:li:h:{i}",
                reactions=10 + i,
                features=_features(),
            )
        eligible = intelligence.eligible_creator_ids(conn)
        assert heavy in eligible
        assert light not in eligible

        baseline = intelligence.build_cohort_baseline(conn)
        assert intelligence.build_intelligence_pack(conn, light, baseline) is None
        assert intelligence.build_intelligence_pack(conn, heavy, baseline) is not None


def test_pack_engagement_quantiles_match_manual_compute(db: Path):
    with storage.connect(db) as conn:
        cid = _seed_creator(conn, "https://linkedin.com/in/c", "C")
        # 5 posts with engagement scores spread over a known range.
        # reactions -> engagement_score = (reactions + 5*2 + 10*1) / 1000
        # = (reactions + 20) / 1000
        for i, reactions in enumerate([10, 30, 50, 70, 90]):
            _add_post(
                conn,
                creator_id=cid,
                post_urn=f"urn:li:c:{i}",
                reactions=reactions,
                features=_features(),
            )
        baseline = intelligence.build_cohort_baseline(conn)
        pack = intelligence.build_intelligence_pack(conn, cid, baseline)
        assert pack is not None
        eng = pack["posts"]["engagement"]
        # Median = middle = (50+20)/1000 = 0.07
        assert eng["median"] == pytest.approx(0.07)
        # p25 = (30+20)/1000 = 0.05
        assert eng["p25"] == pytest.approx(0.05)
        # p75 = (70+20)/1000 = 0.09
        assert eng["p75"] == pytest.approx(0.09)


def test_pack_cohort_delta_flags_over_indexing(db: Path):
    with storage.connect(db) as conn:
        # Cohort baseline: 5 creators × 5 posts each = 25 posts. Most use
        # 'story' hooks. One target creator uses 'bold_claim' on every post —
        # should be flagged as over-indexed.
        cohort_ids = [
            _seed_creator(conn, f"https://linkedin.com/in/cohort{i}", f"Cohort {i}")
            for i in range(5)
        ]
        for ci, cid in enumerate(cohort_ids):
            for i in range(5):
                _add_post(
                    conn,
                    creator_id=cid,
                    post_urn=f"urn:li:cohort{ci}:{i}",
                    reactions=20,
                    features=_features(hook="story"),
                )
        target = _seed_creator(conn, "https://linkedin.com/in/target", "Target")
        for i in range(5):
            _add_post(
                conn,
                creator_id=target,
                post_urn=f"urn:li:target:{i}",
                reactions=20 + i,
                features=_features(hook="bold_claim"),
            )
        baseline = intelligence.build_cohort_baseline(conn)
        pack = intelligence.build_intelligence_pack(conn, target, baseline)
        assert pack is not None
        delta = pack["cohort_delta"]
        assert "hook_type" in delta
        assert "bold_claim" in delta["hook_type"]["over_indexed"]
        assert "story" in delta["hook_type"]["under_indexed"]


def test_pack_top5_bottom3_ordering(db: Path):
    with storage.connect(db) as conn:
        cid = _seed_creator(conn, "https://linkedin.com/in/c", "C")
        # 8 posts, reactions ascending. Top 5 should be the 5 highest, in
        # descending order. Bottom 3 should be the 3 lowest, in ascending.
        for i, reactions in enumerate([5, 15, 25, 35, 45, 55, 65, 75]):
            _add_post(
                conn,
                creator_id=cid,
                post_urn=f"urn:li:c:{i}",
                reactions=reactions,
                features=_features(),
            )
        baseline = intelligence.build_cohort_baseline(conn)
        pack = intelligence.build_intelligence_pack(conn, cid, baseline)
        assert pack is not None
        top5 = pack["top_5_posts"]
        assert len(top5) == 5
        # First element = highest score
        assert top5[0]["reactions"] == 75
        assert top5[-1]["reactions"] == 35
        bot3 = pack["bottom_3_posts"]
        assert len(bot3) == 3
        assert bot3[0]["reactions"] == 5
        assert bot3[-1]["reactions"] == 25


def test_pack_growth_correlation_surfaces_top_growth_posts(db: Path):
    with storage.connect(db) as conn:
        cid = _seed_creator(conn, "https://linkedin.com/in/c", "C")
        for i, growth in enumerate([5, 50, 20, 100, 1, 75]):
            _add_post(
                conn,
                creator_id=cid,
                post_urn=f"urn:li:c:{i}",
                reactions=20,
                features=_features(hook="bold_claim" if growth >= 50 else "story"),
                growth_7d=growth,
            )
        baseline = intelligence.build_cohort_baseline(conn)
        pack = intelligence.build_intelligence_pack(conn, cid, baseline)
        assert pack is not None
        top_growth = pack["growth_correlation"]["top_growth_posts"]
        assert [p["growth_7d"] for p in top_growth] == [100, 75, 50]
        # All three top-growth posts used bold_claim — pattern count should reflect that.
        patterns = pack["growth_correlation"]["patterns_in_top_growth"]
        assert patterns["hook_type"]["bold_claim"] == 3


def test_pack_skip_returns_none_when_no_features(db: Path):
    """A creator with posts but no extracted features is not eligible."""
    with storage.connect(db) as conn:
        cid = _seed_creator(conn, "https://linkedin.com/in/empty", "Empty")
        # Posts but no post_features rows.
        for i in range(10):
            storage.insert_post(
                conn,
                post_urn=f"urn:li:e:{i}",
                creator_id=cid,
                post_url=None,
                post_text="x",
                reactions=10,
                comments=0,
                reshares=0,
                follower_count_at_collection=1000,
                post_date="2026-04-10T00:00:00Z",
            )
        baseline = intelligence.build_cohort_baseline(conn)
        assert intelligence.build_intelligence_pack(conn, cid, baseline) is None


def test_baseline_n_counts_only_eligible_creators(db: Path):
    with storage.connect(db) as conn:
        # 1 eligible (5 posts), 1 ineligible (2 posts)
        eligible_id = _seed_creator(conn, "https://linkedin.com/in/e", "E")
        for i in range(5):
            _add_post(
                conn,
                creator_id=eligible_id,
                post_urn=f"urn:li:e:{i}",
                reactions=10,
                features=_features(),
            )
        ineligible_id = _seed_creator(conn, "https://linkedin.com/in/i", "I")
        for i in range(2):
            _add_post(
                conn,
                creator_id=ineligible_id,
                post_urn=f"urn:li:i:{i}",
                reactions=10,
                features=_features(),
            )
        baseline = intelligence.build_cohort_baseline(conn)
        # Baseline only includes the eligible creator's 5 posts.
        assert baseline["n_creators_in_baseline"] == 1
        assert baseline["n_posts_in_baseline"] == 5
        assert "hook_type" in baseline["categorical_distribution"]
