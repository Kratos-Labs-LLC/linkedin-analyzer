import json
from pathlib import Path

import pytest

from src import storage
from src.analyzer import stats
from src.config import AppConfig, CreatorConfig


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        anthropic_api_key=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        headless=True,
        creators=[],
        db_path=tmp_path / "a.db",
        chrome_profile_dir=tmp_path / "prof",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "out",
    )


def _seed_posts_with_features(db_path: Path, n: int = 20) -> None:
    """Seed n posts spanning a clean engagement gradient with matching features."""
    storage.init_db(db_path)
    with storage.connect(db_path) as conn:
        storage.sync_creators(conn, [CreatorConfig("https://linkedin.com/in/a", "A", 1.0)])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        for i in range(n):
            storage.insert_post(
                conn,
                post_urn=f"urn:li:activity:{i}",
                creator_id=cid,
                post_url=None,
                post_text=f"post {i}",
                reactions=i * 10,
                comments=i,
                reshares=0,
                follower_count_at_collection=1_000,
                post_date=None,
            )
            # Higher-engagement posts use "bold_claim" hook; lower use "question".
            features = {
                "hook_type": "bold_claim" if i >= n // 2 else "question",
                "hook_text": "hook",
                "opening_word_count": 10 + i,
                "total_word_count": 100 + i * 10,
                "line_break_count": 3,
                "paragraph_style": "short_chunks",
                "uses_list": False,
                "list_style": "none",
                "uses_emojis": False,
                "emoji_count": 0,
                "cta_type": "question_prompt",
                "cta_text": None,
                "emotional_register": "practical",
                "specificity_score": 5,
                "proof_elements": ["none"] if i < n // 2 else ["client_names"],
                "narrative_arc": "none",
                "opens_with_pronoun": "i",
                "ends_with": "statement",
                "topic_category": "agency_ops",
                "controversy_score": 3,
            }
            row = conn.execute(
                "SELECT id FROM posts WHERE post_urn = ?", (f"urn:li:activity:{i}",)
            ).fetchone()
            conn.execute(
                "INSERT INTO post_features (post_id, features_json) VALUES (?, ?)",
                (row["id"], json.dumps(features)),
            )


def test_compute_stats_shape(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_posts_with_features(cfg.db_path, n=20)

    result = stats.compute_stats(cfg)
    assert result["n_posts_analyzed"] == 20
    assert result["top_quartile_n"] + result["bottom_quartile_n"] == 20
    assert "categorical_features" in result
    assert "numeric_features" in result

    # bold_claim should rank higher than question given our seeding
    hook = result["categorical_features"]["hook_type"]
    assert hook["bold_claim"]["rank"] == 1
    assert hook["question"]["rank"] == 2

    # total_word_count should correlate positively with engagement in this seeding
    assert result["numeric_features"]["total_word_count"]["correlation"] > 0


def test_write_stats_and_markdown(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_posts_with_features(cfg.db_path, n=10)

    result = stats.compute_stats(cfg)
    path = stats.write_stats_json(result, cfg.output_dir)
    assert path.exists()
    parsed = json.loads(path.read_text())
    assert parsed["n_posts_analyzed"] == 10

    top, bot = stats.write_top_bottom_markdown(cfg, cfg.output_dir, limit=5)
    assert top.exists() and bot.exists()
    assert "Top posts" in top.read_text()
    assert "Bottom posts" in bot.read_text()


def _seed_two_topics(db_path: Path, per_topic: int = 10) -> None:
    """Seed equal numbers of posts in `ai` and `personal_brand`. The `ai`
    topic prefers `bold_claim` hooks; `personal_brand` prefers `question`.
    Pooled stats will average them out — by_topic will reveal the difference."""
    storage.init_db(db_path)
    with storage.connect(db_path) as conn:
        storage.sync_creators(conn, [CreatorConfig("https://linkedin.com/in/a", "A", 1.0)])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        i = 0
        for topic, winning_hook, losing_hook in [
            ("ai", "bold_claim", "question"),
            ("personal_brand", "question", "bold_claim"),
        ]:
            for j in range(per_topic):
                hook = winning_hook if j >= per_topic // 2 else losing_hook
                storage.insert_post(
                    conn,
                    post_urn=f"urn:li:activity:{i}",
                    creator_id=cid,
                    post_url=None,
                    post_text=f"post {i}",
                    reactions=10 + j * 5,  # higher-j -> higher engagement
                    comments=j,
                    reshares=0,
                    follower_count_at_collection=1_000,
                    post_date=None,
                )
                row = conn.execute(
                    "SELECT id FROM posts WHERE post_urn = ?", (f"urn:li:activity:{i}",)
                ).fetchone()
                features = {
                    "hook_type": hook,
                    "topic_category": topic,
                    "total_word_count": 100,
                    "line_break_count": 3,
                    "paragraph_style": "short_chunks",
                    "uses_list": False,
                    "list_style": "none",
                    "uses_emojis": False,
                    "emoji_count": 0,
                    "cta_type": "question_prompt",
                    "cta_text": None,
                    "emotional_register": "practical",
                    "specificity_score": 5,
                    "proof_elements": ["none"],
                    "narrative_arc": "none",
                    "opens_with_pronoun": "i",
                    "ends_with": "statement",
                    "controversy_score": 3,
                    "opening_word_count": 10,
                }
                conn.execute(
                    "INSERT INTO post_features (post_id, features_json) VALUES (?, ?)",
                    (row["id"], json.dumps(features)),
                )
                i += 1


def test_by_topic_stratification_surfaces_topic_specific_winners(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_two_topics(cfg.db_path, per_topic=10)

    result = stats.compute_stats(cfg, min_posts_per_topic=4)
    assert "by_topic" in result
    assert "ai" in result["by_topic"]
    assert "personal_brand" in result["by_topic"]

    ai_hooks = result["by_topic"]["ai"]["categorical_features"]["hook_type"]
    pb_hooks = result["by_topic"]["personal_brand"]["categorical_features"]["hook_type"]

    # The seeding rigs each topic so a different hook wins.
    assert ai_hooks["bold_claim"]["rank"] == 1
    assert pb_hooks["question"]["rank"] == 1


def test_by_topic_skips_topics_below_threshold(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_two_topics(cfg.db_path, per_topic=5)  # 5 each — below default 8

    result = stats.compute_stats(cfg, min_posts_per_topic=8)
    assert result["by_topic"] == {}
    assert result["topics_skipped_min_n"] == {"ai": 5, "personal_brand": 5}


def test_pooled_shape_unchanged_by_stratification(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_posts_with_features(cfg.db_path, n=10)
    result = stats.compute_stats(cfg)
    # Pooled fields still at the top level so existing consumers keep working.
    for key in (
        "n_posts_analyzed",
        "top_quartile_n",
        "bottom_quartile_n",
        "categorical_features",
        "numeric_features",
        "proof_elements_frequency",
    ):
        assert key in result


def test_compute_stats_empty_db_returns_zero_n(tmp_path: Path):
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    result = stats.compute_stats(cfg)
    assert result == {"n_posts_analyzed": 0}
