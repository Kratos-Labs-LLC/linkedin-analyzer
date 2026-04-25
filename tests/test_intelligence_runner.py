"""Tests for src/analyzer/intelligence_runner.py.

Anthropic is mocked. We assert:
  - Runs over all eligible creators when no target is specified.
  - target_creator_url filters to one creator.
  - Skips silently when no creators pass the eligibility gate.
  - Writes both markdown and pack.json per creator.
  - Slug is stable + matches the algorithm.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src import storage
from src.analyzer import intelligence_runner
from src.config import AppConfig


# --- Fakes ----------------------------------------------------------


class _Block:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text: str):
        self.content = [_Block(text)]
        self.usage = MagicMock(
            input_tokens=100,
            output_tokens=4_000,
            cache_creation_input_tokens=2_000,
            cache_read_input_tokens=0,
        )


def _good_doc() -> str:
    body = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 60
    sections = [
        "## TL;DR",
        "## Identity & positioning",
        "## What they post",
        "## What works for them",
        "## What doesn't work",
        "## Why followers grow",
        "## Profile-post coherence",
        "## Replicable plays",
        "## Appendix",
    ]
    return "\n\n".join(f"{h}\n\n{body}" for h in sections)


def _features() -> dict:
    return {
        "hook_type": "bold_claim",
        "paragraph_style": "short_chunks",
        "list_style": "none",
        "uses_list": False,
        "uses_emojis": False,
        "cta_type": "question_prompt",
        "emotional_register": "practical",
        "narrative_arc": "none",
        "opens_with_pronoun": "i",
        "ends_with": "statement",
        "topic_category": "agency_ops",
        "opening_word_count": 8,
        "total_word_count": 80,
        "line_break_count": 2,
        "emoji_count": 0,
        "specificity_score": 5,
        "controversy_score": 3,
        "proof_elements": ["none"],
    }


def _seed_creator_with_posts(conn, *, url, name, n_posts, base_reactions=10):
    conn.execute(
        "INSERT INTO creators (linkedin_url, display_name, weight, active) "
        "VALUES (?, ?, ?, 1)",
        (url, name, 1.0),
    )
    cid = conn.execute(
        "SELECT id FROM creators WHERE linkedin_url = ?", (url,)
    ).fetchone()["id"]
    for i in range(n_posts):
        storage.insert_post(
            conn,
            post_urn=f"urn:li:{name}:{i}",
            creator_id=cid,
            post_url=None,
            post_text=f"text {name} {i}",
            reactions=base_reactions + i,
            comments=2,
            reshares=1,
            follower_count_at_collection=1000,
            post_date="2026-04-10T00:00:00Z",
        )
        pid = conn.execute(
            "SELECT id FROM posts WHERE post_urn = ?", (f"urn:li:{name}:{i}",)
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR REPLACE INTO post_features (post_id, features_json) VALUES (?, ?)",
            (pid, json.dumps(_features())),
        )
    return cid


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    cfg = AppConfig(
        anthropic_api_key="fake",
        telegram_bot_token=None,
        telegram_chat_id=None,
        headless=True,
        creators=[],
        db_path=tmp_path / "db" / "test.db",
        chrome_profile_dir=tmp_path / "chrome",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "output",
    )
    storage.init_db(cfg.db_path)
    return cfg


def test_runs_for_all_eligible_creators(cfg: AppConfig):
    with storage.connect(cfg.db_path) as conn:
        _seed_creator_with_posts(conn, url="https://linkedin.com/in/a", name="A", n_posts=6)
        _seed_creator_with_posts(conn, url="https://linkedin.com/in/b", name="B", n_posts=6)
        _seed_creator_with_posts(conn, url="https://linkedin.com/in/c", name="C", n_posts=2)

    client = MagicMock()
    client.messages.create.return_value = _Resp(_good_doc())

    counters = intelligence_runner.run_intelligence(cfg, client=client)
    assert counters["eligible"] == 2
    assert counters["synthesized"] == 2
    assert counters["failed"] == 0

    out_dir = intelligence_runner.intelligence_dir(cfg)
    md_files = sorted(p.name for p in out_dir.glob("*.md"))
    json_files = sorted(p.name for p in out_dir.glob("*.pack.json"))
    assert len(md_files) == 2
    assert len(json_files) == 2


def test_target_creator_url_filters_to_one(cfg: AppConfig):
    with storage.connect(cfg.db_path) as conn:
        _seed_creator_with_posts(conn, url="https://linkedin.com/in/a", name="A", n_posts=6)
        _seed_creator_with_posts(conn, url="https://linkedin.com/in/b", name="B", n_posts=6)

    client = MagicMock()
    client.messages.create.return_value = _Resp(_good_doc())
    counters = intelligence_runner.run_intelligence(
        cfg, target_creator_url="https://linkedin.com/in/a", client=client
    )
    assert counters["synthesized"] == 1
    out_dir = intelligence_runner.intelligence_dir(cfg)
    md_files = sorted(p.name for p in out_dir.glob("*.md"))
    assert len(md_files) == 1
    assert md_files[0].startswith("a-")


def test_skips_when_no_eligible_creators(cfg: AppConfig):
    with storage.connect(cfg.db_path) as conn:
        _seed_creator_with_posts(conn, url="https://linkedin.com/in/a", name="A", n_posts=2)

    client = MagicMock()
    client.messages.create.return_value = _Resp(_good_doc())
    counters = intelligence_runner.run_intelligence(cfg, client=client)
    assert counters["eligible"] == 0
    assert counters["synthesized"] == 0
    out_dir = intelligence_runner.intelligence_dir(cfg)
    assert not list(out_dir.glob("*.md"))
    # No Anthropic call should fire when no one is eligible.
    client.messages.create.assert_not_called()


def test_target_url_not_found(cfg: AppConfig):
    with storage.connect(cfg.db_path) as conn:
        _seed_creator_with_posts(conn, url="https://linkedin.com/in/a", name="A", n_posts=6)

    client = MagicMock()
    counters = intelligence_runner.run_intelligence(
        cfg,
        target_creator_url="https://linkedin.com/in/nonexistent",
        client=client,
    )
    assert counters["synthesized"] == 0


def test_creator_slug_stable_and_safe():
    s1 = intelligence_runner.creator_slug(display_name="Alice Smith!!", creator_id=7)
    s2 = intelligence_runner.creator_slug(display_name="alice  smith", creator_id=7)
    assert s1 == "alice-smith-7"
    # Idempotent on collapsing whitespace + special chars.
    assert s2 == "alice-smith-7"
    # Empty name falls back to creator-<id>.
    assert intelligence_runner.creator_slug(display_name=None, creator_id=12) == "creator-12"
    assert intelligence_runner.creator_slug(display_name="!!!", creator_id=12) == "creator-12"


def test_pack_json_written_alongside_md(cfg: AppConfig):
    with storage.connect(cfg.db_path) as conn:
        _seed_creator_with_posts(conn, url="https://linkedin.com/in/dugg", name="Dugg", n_posts=6)

    client = MagicMock()
    client.messages.create.return_value = _Resp(_good_doc())
    intelligence_runner.run_intelligence(cfg, client=client)

    out_dir = intelligence_runner.intelligence_dir(cfg)
    md = next(out_dir.glob("*.md"))
    pack_json = next(out_dir.glob("*.pack.json"))
    assert md.stem.split(".")[0] == pack_json.name.split(".pack.json")[0]
    pack = json.loads(pack_json.read_text())
    assert pack["creator"]["display_name"] == "Dugg"
    assert pack["posts"]["n_with_features"] == 6
