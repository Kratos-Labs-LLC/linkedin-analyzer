"""End-to-end test for scripts/run_analysis.py.

Mocks Anthropic at every analyzer module's call site. Seeds a populated DB.
Runs main() with --force --skip-validate and asserts:
  - the pipeline executes the expected steps in the expected order
  - every output artifact lands at the expected path
  - metrics.json captures the resulting state

Catches regressions where the pipeline's internal ordering swaps (e.g. if
profile pipeline accidentally runs after post synth instead of before).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src import storage
from src.config import AppConfig, CreatorConfig

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- Fake Anthropic that records every call -----------------------------


class _FakeBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResp:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


# A globally-shared call log so every mocked Anthropic site appends to it,
# preserving cross-module order.
_CALL_LOG: list[str] = []


def _classify(kwargs: dict) -> str:
    """Return a short tag identifying which call site this is."""
    model = kwargs.get("model", "")
    system = kwargs.get("system") or []
    sys_text = ""
    if isinstance(system, list) and system:
        sys_text = system[0].get("text", "") if isinstance(system[0], dict) else ""
    elif isinstance(system, str):
        sys_text = system

    user_msg = ""
    msgs = kwargs.get("messages") or []
    if msgs and isinstance(msgs[0], dict):
        user_msg = str(msgs[0].get("content", ""))

    if "evaluating two LinkedIn posts" in sys_text:
        return "validator-judge"
    if "engagement profile" in sys_text or "evaluating two" in sys_text:
        return "validator-judge"
    if "auditing a single LinkedIn profile" in sys_text:
        return "audit-opus"
    if "competitive-intelligence brief" in sys_text:
        return "intelligence-synth"
    if "linkedin-profile-optimizer" in sys_text:
        if "draft" in user_msg.lower() and "<draft>" in user_msg:
            return "profile-synth-revise"
        return "profile-synth-draft"
    if "linkedin-high-engagement-writer" in sys_text:
        if "<draft>" in user_msg:
            return "post-synth-revise"
        return "post-synth-draft"
    if "analyzing a LinkedIn profile" in sys_text:
        return "profile-extract"
    if "analyzing a LinkedIn post" in sys_text:
        return "post-extract"
    if "claude-sonnet" in model and "POST:" in user_msg:
        return "post-extract"
    if "claude-sonnet" in model and "PROFILE:" in user_msg:
        return "profile-extract"
    return f"unknown:{model[:20]}"


def _make_canned_response(tag: str) -> _FakeResp:
    """Tag-aware canned response so each call site gets validly-shaped output."""
    if tag == "post-extract":
        return _FakeResp(
            json.dumps(
                {
                    "hook_type": "bold_claim",
                    "hook_text": "x",
                    "opening_word_count": 8,
                    "total_word_count": 80,
                    "line_break_count": 2,
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
                    "topic_category": "agency_ops",
                    "controversy_score": 3,
                }
            )
        )
    if tag == "profile-extract":
        return _FakeResp(
            json.dumps(
                {
                    "headline_style": "benefit-led",
                    "headline_specificity": 7,
                    "about_voice": "practical",
                    "about_promise_clarity": 8,
                    "about_proof_density": 6,
                    "niche_breadth": "narrow",
                    "audience_alignment": "founders",
                    "cta_in_about": True,
                    "featured_section_type": "lead_magnets",
                    "post_to_profile_match_score": 9,
                }
            )
        )
    if tag in ("post-synth-draft", "post-synth-revise"):
        body = (
            "---\nname: linkedin-high-engagement-writer\n"
            "description: empirically-grounded post writer\n---\n\n"
            "# linkedin-high-engagement-writer\n\nbody " * 40
        )
        if tag == "post-synth-revise":
            return _FakeResp(
                "<critique>fix typo</critique>\n\n" + body
            )
        return _FakeResp(body)
    if tag in ("profile-synth-draft", "profile-synth-revise"):
        body = (
            "---\nname: linkedin-profile-optimizer\n"
            "description: empirically-grounded profile optimizer\n---\n\n"
            "# linkedin-profile-optimizer\n\nbody " * 40
        )
        if tag == "profile-synth-revise":
            return _FakeResp("<critique>ok</critique>\n\n" + body)
        return _FakeResp(body)
    if tag == "validator-judge":
        return _FakeResp(json.dumps({"winner": "A", "reasoning": "ok"}))
    if tag == "audit-opus":
        return _FakeResp("# Verdict\n\nMid pull.\n\n# Top 3 fixes\n\n1. x\n2. y\n3. z")
    if tag == "intelligence-synth":
        body = "Lorem ipsum. " * 80
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
        return _FakeResp("\n\n".join(f"{h}\n\n{body}" for h in sections))
    return _FakeResp("UNEXPECTED")


class _FakeAnthropic:
    def __init__(self, *args, **kwargs):
        self.messages = self

    def create(self, **kwargs):
        tag = _classify(kwargs)
        _CALL_LOG.append(tag)
        return _make_canned_response(tag)


# --- Test setup ---------------------------------------------------------


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        anthropic_api_key="fake-key",
        telegram_bot_token=None,
        telegram_chat_id=None,
        headless=True,
        creators=[],
        db_path=tmp_path / "db" / "analyzer.db",
        chrome_profile_dir=tmp_path / "chrome_profile",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "output",
    )


def _seed_realistic_db(cfg: AppConfig) -> None:
    """Populate the DB with the minimum data that lets every step do work."""
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        # 6 creators, 6 posts each, 3 snapshots each across 14 days.
        # Profile synth requires >= 5 creators with features + growth-rate.
        creators = [
            CreatorConfig(f"https://linkedin.com/in/c{i}", f"Creator {i}", 1.0)
            for i in range(6)
        ]
        storage.sync_creators(conn, creators)

        for ci, c in enumerate(creators):
            cid = storage.get_creator_id_by_url(conn, c.linkedin_url)

            # Snapshots: 1000 -> 1100 -> 1200 over days 0/7/14.
            for day, fc in [(0, 1000), (7, 1100), (14, 1200)]:
                storage.insert_profile_snapshot(
                    conn,
                    creator_id=cid,
                    follower_count=fc,
                    headline=f"headline {ci}",
                    about_text=f"about {ci}",
                    snapshot_at=f"2026-04-{1 + day:02d}T00:00:00Z",
                )

            for pi in range(6):
                storage.insert_post(
                    conn,
                    post_urn=f"urn:li:activity:{ci}-{pi}",
                    creator_id=cid,
                    post_url=None,
                    post_text=f"post {ci}-{pi} body, with enough text to feel real",
                    reactions=100 + ci * 10 + pi,
                    comments=5 + pi,
                    reshares=pi,
                    follower_count_at_collection=1100,
                    post_date=f"2026-04-{2 + pi:02d}T00:00:00Z",
                )
                # Pre-seed post_features for every post so the per-creator
                # intelligence step has enough featured posts (>=5/creator)
                # without depending on the live extractor running on the full
                # corpus (it only processes top+bottom quartile by default).
                pid = conn.execute(
                    "SELECT id FROM posts WHERE post_urn = ?",
                    (f"urn:li:activity:{ci}-{pi}",),
                ).fetchone()["id"]
                conn.execute(
                    "INSERT OR REPLACE INTO post_features (post_id, features_json) "
                    "VALUES (?, ?)",
                    (
                        pid,
                        json.dumps(
                            {
                                "hook_type": "bold_claim" if pi % 2 == 0 else "story",
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
                        ),
                    ),
                )


# --- The test -----------------------------------------------------------


def test_run_analysis_pipeline_orders_steps_correctly(cfg, monkeypatch):
    _CALL_LOG.clear()
    _seed_realistic_db(cfg)

    # Don't actually call MY_PROFILE_URL audit — leave env unset.
    monkeypatch.delenv("MY_PROFILE_URL", raising=False)

    # Patch every Anthropic constructor used by run_analysis's submodules.
    # The same _FakeAnthropic appends to the shared _CALL_LOG so we can
    # assert cross-module ordering.
    factory = lambda **kw: _FakeAnthropic()  # noqa: E731

    # Speed: skip the rate-limit sleeps in extractors.
    monkeypatch.setattr(
        "src.analyzer.extractor.time.sleep", lambda *_: None
    )
    monkeypatch.setattr(
        "src.analyzer.profile_extractor.time.sleep", lambda *_: None
    )

    # Provide a leadmagnet skill file.
    leadmagnet = cfg.output_dir / "leadmagnet.md"
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    leadmagnet.write_text(
        "---\nname: leadmagnet-post-writer\n---\n# leadmagnet body"
    )

    # Build the argv that scripts/run_analysis.py expects.
    argv = [
        "run_analysis.py",
        "--force",
        "--leadmagnet-skill-path",
        str(leadmagnet),
        "--skip-validate",  # too noisy for E2E ordering check
    ]

    with patch.object(sys, "argv", argv), \
         patch("src.config.load_config", return_value=cfg), \
         patch("src.analyzer.extractor.Anthropic", factory), \
         patch("src.analyzer.profile_extractor.Anthropic", factory), \
         patch("src.analyzer.synthesizer.Anthropic", factory), \
         patch("src.analyzer.profile_synthesizer.Anthropic", factory), \
         patch("src.analyzer.intelligence_runner.Anthropic", factory):
        # Import lazily so the patch is in place before the script's main runs.
        import importlib

        spec = importlib.util.spec_from_file_location(
            "scripts.run_analysis", REPO_ROOT / "scripts" / "run_analysis.py"
        )
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        rc = module.main()
    assert rc == 0

    # --- Step ordering ----------------------------------------------
    # The expected sequence (approximate; we check key relative orderings):
    #   1. post-extract calls (300+ in a real run; 18 in our seed)
    #   2. profile-extract calls (one per creator = 3)
    #   3. profile-synth-draft, profile-synth-revise OR
    #      post-synth-draft, post-synth-revise
    #   The pipeline runs profile feature extraction BEFORE post synthesis
    #   so post synth has the profile_axis. Both synths run after extracts.

    # All post-extract calls finish before any post-synth call.
    last_post_extract = max(
        (i for i, t in enumerate(_CALL_LOG) if t == "post-extract"), default=-1
    )
    first_post_synth = min(
        (i for i, t in enumerate(_CALL_LOG) if t.startswith("post-synth")),
        default=10**9,
    )
    assert last_post_extract < first_post_synth, (
        f"post-extract not finished before post-synth: log={_CALL_LOG}"
    )

    # Profile feature extraction runs before BOTH synths (post + profile),
    # so the post-writer can read the profile axis findings.
    last_profile_extract = max(
        (i for i, t in enumerate(_CALL_LOG) if t == "profile-extract"), default=-1
    )
    first_post_synth = min(
        (i for i, t in enumerate(_CALL_LOG) if t.startswith("post-synth")),
        default=10**9,
    )
    assert last_profile_extract < first_post_synth, (
        f"profile-extract not finished before post-synth: log={_CALL_LOG}"
    )

    # Both synths each run TWO calls (draft + revise).
    assert _CALL_LOG.count("post-synth-draft") == 1
    assert _CALL_LOG.count("post-synth-revise") == 1
    assert _CALL_LOG.count("profile-synth-draft") == 1
    assert _CALL_LOG.count("profile-synth-revise") == 1

    # --- Output artifacts ---
    out = cfg.output_dir
    expected = [
        out / "stats.json",
        out / "top_posts.md",
        out / "bottom_posts.md",
        out / "top_growth_posts.md",
        out / "linkedin-high-engagement-writer" / "SKILL.md",
        out / "linkedin-profile-optimizer" / "SKILL.md",
        out / "metrics.json",
    ]
    for p in expected:
        assert p.exists(), f"expected output missing: {p}"

    # Per-creator intelligence briefs: one .md + one .pack.json per
    # eligible creator. The seed has 6 creators with 6 posts each, all
    # featured -> all 6 are eligible.
    intel_dir = out / "intelligence"
    assert intel_dir.exists(), "intelligence/ directory missing"
    md_files = sorted(intel_dir.glob("*.md"))
    pack_files = sorted(intel_dir.glob("*.pack.json"))
    assert len(md_files) == 6, f"expected 6 intelligence .md files, got {len(md_files)}"
    assert len(pack_files) == 6, f"expected 6 .pack.json files, got {len(pack_files)}"
    # Stub guard: no stub doc should appear in the happy path.
    for p in md_files:
        assert "auto-stub" not in p.read_text()

    # stats.json includes both axes
    stats = json.loads((out / "stats.json").read_text())
    assert "categorical_features" in stats
    assert "profile_axis" in stats

    # metrics.json has the headline numbers
    m = json.loads((out / "metrics.json").read_text())
    assert m["collection"]["total_posts"] == 36
    assert m["collection"]["active_creators"] == 6
    assert m["coverage"]["posts_with_features"] > 0
