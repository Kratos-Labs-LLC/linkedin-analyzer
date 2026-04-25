import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src import storage
from src.analyzer import profile_synthesizer
from src.config import AppConfig, CreatorConfig


def _cfg(tmp_path: Path, *, api_key: str | None = "k") -> AppConfig:
    cfg = AppConfig(
        anthropic_api_key=api_key,
        telegram_bot_token=None,
        telegram_chat_id=None,
        headless=True,
        creators=[],
        db_path=tmp_path / "db.sqlite",
        chrome_profile_dir=tmp_path / "prof",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "out",
    )
    cfg.output_dir.mkdir()
    # Seed stats.json with a profile_axis so synthesize() can read it.
    (cfg.output_dir / "stats.json").write_text(
        json.dumps(
            {
                "profile_axis": {
                    "n_creators_analyzed": 5,
                    "score_field": "growth_rate_per_week",
                    "categorical_features": {
                        "headline_style": {
                            "benefit-led": {"n": 3, "mean_engagement": 100, "rank": 1},
                            "role-only": {"n": 2, "mean_engagement": 5, "rank": 2},
                        }
                    },
                }
            }
        )
    )
    # Seed at least one creator with a snapshot so _render_top_bottom_profiles
    # has something to render.
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(
            conn, [CreatorConfig("https://linkedin.com/in/a", "Alice", 1.0)]
        )
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        for days, fc in [(0, 1000), (7, 1200)]:
            storage.insert_profile_snapshot(
                conn,
                creator_id=cid,
                follower_count=fc,
                headline="Founder helping agencies",
                about_text="I help agency founders.",
                snapshot_at=f"2026-04-{1 + days:02d}T00:00:00Z",
            )
    return cfg


# --- Sanity guard --------------------------------------------------------


def test_looks_like_profile_skill_requires_frontmatter_and_name():
    good = (
        "---\nname: linkedin-profile-optimizer\n---\n\n"
        "# linkedin-profile-optimizer\n\nbody " * 50  # >MIN_REVISE_LENGTH
    )
    assert profile_synthesizer._looks_like_profile_skill(good) is True

    short = "---\nname: linkedin-profile-optimizer\n---\n# tiny"
    assert profile_synthesizer._looks_like_profile_skill(short) is False

    no_frontmatter = "linkedin-profile-optimizer body" + "x" * 1000
    assert profile_synthesizer._looks_like_profile_skill(no_frontmatter) is False

    no_name = "---\nname: something-else\n---\n\n# different\n\nbody " * 50
    assert profile_synthesizer._looks_like_profile_skill(no_name) is False


def test_render_top_bottom_profiles_emits_named_creators(tmp_path: Path):
    cfg = _cfg(tmp_path)
    top, bot = profile_synthesizer._render_top_bottom_profiles(cfg)
    assert "Alice" in top
    assert "Founder helping agencies" in top
    # Only one creator → bottom is fallback string
    assert "no low-growth profiles" in bot or "Alice" in bot


# --- End-to-end with mocked Anthropic ------------------------------------


_GOOD_DRAFT = (
    "---\nname: linkedin-profile-optimizer\n"
    "description: draft of profile optimizer\n---\n\n"
    "# linkedin-profile-optimizer\n\nDraft body.\n"
    + "filler line for length sanity.\n" * 30
)

_GOOD_REVISED = (
    "<critique>\n- frontmatter ok\n- voice not applied\n</critique>\n\n"
    "---\nname: linkedin-profile-optimizer\n"
    "description: empirically-grounded profile optimizer\n---\n\n"
    "# linkedin-profile-optimizer\n\nRevised body.\n"
    + "filler line for length sanity.\n" * 30
)


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeAnthropic:
    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.calls)
        return _Resp(_GOOD_DRAFT if n == 1 else _GOOD_REVISED)


def test_synthesize_writes_skill_and_preserves_draft(tmp_path: Path):
    cfg = _cfg(tmp_path)
    fake = _FakeAnthropic()
    with patch.object(profile_synthesizer, "Anthropic", lambda **kw: fake):
        out = profile_synthesizer.synthesize(cfg)

    assert len(fake.calls) == 2
    body = out.read_text()
    assert "<critique>" not in body
    assert "linkedin-profile-optimizer" in body
    assert "Revised body" in body
    assert (cfg.output_dir / "profile_synthesis_draft.md").exists()


def test_synthesize_uses_cached_static_block_across_passes(tmp_path: Path):
    cfg = _cfg(tmp_path)
    fake = _FakeAnthropic()
    with patch.object(profile_synthesizer, "Anthropic", lambda **kw: fake):
        profile_synthesizer.synthesize(cfg)

    a, b = fake.calls
    # System block 0 is the static input — must be byte-identical for cache hit.
    assert a["system"][0]["text"] == b["system"][0]["text"]
    assert a["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Block 1 differs (draft vs critique).
    assert a["system"][1]["text"] != b["system"][1]["text"]


class _BadReviseFakeAnthropic:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(_GOOD_DRAFT) if len(self.calls) == 1 else _Resp(
            "<critique>everything broken</critique>"
        )


def test_synthesize_falls_back_to_draft_on_bad_revise(tmp_path: Path):
    cfg = _cfg(tmp_path)
    fake = _BadReviseFakeAnthropic()
    with patch.object(profile_synthesizer, "Anthropic", lambda **kw: fake):
        out = profile_synthesizer.synthesize(cfg)
    assert "Draft body" in out.read_text()


def test_synthesize_requires_api_key(tmp_path: Path):
    cfg = _cfg(tmp_path, api_key=None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        profile_synthesizer.synthesize(cfg)


def test_synthesize_requires_profile_axis_in_stats_json(tmp_path: Path):
    cfg = _cfg(tmp_path)
    # Wipe profile_axis from stats.json
    (cfg.output_dir / "stats.json").write_text(json.dumps({"n_posts_analyzed": 100}))
    fake = _FakeAnthropic()
    with patch.object(profile_synthesizer, "Anthropic", lambda **kw: fake):
        with pytest.raises(RuntimeError, match="profile_axis"):
            profile_synthesizer.synthesize(cfg)


def test_synthesize_requires_stats_json_to_exist(tmp_path: Path):
    cfg = _cfg(tmp_path)
    (cfg.output_dir / "stats.json").unlink()
    fake = _FakeAnthropic()
    with patch.object(profile_synthesizer, "Anthropic", lambda **kw: fake):
        with pytest.raises(FileNotFoundError):
            profile_synthesizer.synthesize(cfg)


def test_synthesize_returns_none_below_min_creators_threshold(tmp_path: Path):
    """B2: when profile_axis.n_creators_analyzed < MIN_CREATORS_FOR_SYNTH the
    synth refuses to run — saves ~$1.30 of Opus on a no-data run."""
    cfg = _cfg(tmp_path)
    # Override stats.json so n_creators_analyzed is below threshold.
    (cfg.output_dir / "stats.json").write_text(
        json.dumps(
            {
                "profile_axis": {
                    "n_creators_analyzed": 2,  # below MIN_CREATORS_FOR_SYNTH=5
                    "score_field": "growth_rate_per_week",
                    "categorical_features": {},
                }
            }
        )
    )
    fake = _FakeAnthropic()
    with patch.object(profile_synthesizer, "Anthropic", lambda **kw: fake):
        result = profile_synthesizer.synthesize(cfg)

    assert result is None
    # No Anthropic calls — burn-no-spend guarantee
    assert len(fake.calls) == 0
    # No SKILL.md / draft files emitted
    assert not (cfg.output_dir / "linkedin-profile-optimizer" / "SKILL.md").exists()
    assert not (cfg.output_dir / "profile_synthesis_draft.md").exists()


def test_synthesize_runs_at_threshold(tmp_path: Path):
    """Boundary: n == MIN_CREATORS_FOR_SYNTH still runs."""
    cfg = _cfg(tmp_path)
    (cfg.output_dir / "stats.json").write_text(
        json.dumps(
            {
                "profile_axis": {
                    "n_creators_analyzed": profile_synthesizer.MIN_CREATORS_FOR_SYNTH,
                    "score_field": "growth_rate_per_week",
                    "categorical_features": {},
                }
            }
        )
    )
    fake = _FakeAnthropic()
    with patch.object(profile_synthesizer, "Anthropic", lambda **kw: fake):
        result = profile_synthesizer.synthesize(cfg)

    assert result is not None
    assert result.exists()
