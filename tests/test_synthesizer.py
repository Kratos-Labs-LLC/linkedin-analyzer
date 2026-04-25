import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.analyzer import synthesizer
from src.config import AppConfig


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
    (cfg.output_dir / "stats.json").write_text(json.dumps({"n_posts_analyzed": 200}))
    (cfg.output_dir / "top_posts.md").write_text("# top\n\nstub\n")
    (cfg.output_dir / "bottom_posts.md").write_text("# bottom\n\nstub\n")
    return cfg


# --- pure helpers ---------------------------------------------------------


def test_strip_critique_block_removes_leading_block():
    text = "<critique>\nfoo\n</critique>\n\n# Final SKILL.md\nbody"
    assert synthesizer._strip_critique_block(text).startswith("# Final SKILL.md")


def test_strip_critique_block_handles_uppercase_tag():
    text = "<CRITIQUE>foo</CRITIQUE>\n# Final"
    assert synthesizer._strip_critique_block(text) == "# Final"


def test_strip_critique_block_passes_through_when_absent():
    text = "# Just SKILL.md\nbody"
    assert synthesizer._strip_critique_block(text) == text


def test_strip_critique_block_unclosed_tag_preserves_input():
    text = "<critique>oops never closed\n# Skill"
    # Unclosed tag — we don't know where critique ends, so we keep the input as-is.
    assert synthesizer._strip_critique_block(text) == text


def test_static_inputs_includes_all_sections():
    s = synthesizer._static_inputs(
        stats_json='{"n":1}',
        top_posts_md="TOP",
        bottom_posts_md="BOTTOM",
        leadmagnet_skill="LEAD",
    )
    assert "<statistical_findings>" in s
    assert "TOP" in s
    assert "BOTTOM" in s
    assert "LEAD" in s
    assert "dugg_voice_profile" in s


# --- end-to-end with mocked Anthropic -------------------------------------


class _Block:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text: str):
        self.content = [_Block(text)]


class _FakeAnthropic:
    """Records calls; returns canned draft on first call, revised on second."""

    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.calls)
        if n == 1:
            return _Resp("DRAFT BODY (frontmatter missing on purpose)")
        # Second call returns critique + revised body
        return _Resp(
            "<critique>\n- frontmatter missing\n- voice not applied\n</critique>\n\n"
            "---\nname: linkedin-high-engagement-writer\n---\n\n"
            "# Final SKILL.md\n\nbody after revision"
        )


def test_synthesize_runs_two_passes_and_writes_files(tmp_path: Path):
    cfg = _cfg(tmp_path)
    leadmagnet = tmp_path / "leadmagnet.md"
    leadmagnet.write_text("EXISTING LEADMAGNET SKILL")

    fake = _FakeAnthropic()
    with patch.object(synthesizer, "Anthropic", lambda **kw: fake):
        out = synthesizer.synthesize(cfg, leadmagnet)

    # Two calls: draft, then critique-and-revise.
    assert len(fake.calls) == 2

    # Final SKILL.md present and stripped of <critique>.
    body = out.read_text()
    assert "<critique>" not in body
    assert "linkedin-high-engagement-writer" in body
    assert "body after revision" in body

    # Draft preserved for inspection.
    draft_path = cfg.output_dir / "synthesis_draft.md"
    assert draft_path.exists()
    assert "DRAFT BODY" in draft_path.read_text()


def test_synthesize_uses_prompt_caching_on_static_block(tmp_path: Path):
    cfg = _cfg(tmp_path)
    leadmagnet = tmp_path / "leadmagnet.md"
    leadmagnet.write_text("EXISTING LEADMAGNET SKILL")

    fake = _FakeAnthropic()
    with patch.object(synthesizer, "Anthropic", lambda **kw: fake):
        synthesizer.synthesize(cfg, leadmagnet)

    for call in fake.calls:
        system = call["system"]
        assert isinstance(system, list) and len(system) >= 2
        # First system block must be the cache-controlled static inputs.
        assert system[0].get("cache_control") == {"type": "ephemeral"}
        assert "statistical_findings" in system[0]["text"]

    # The two calls must share the SAME static block bytes (otherwise no
    # cache hit). The instruction blocks (system[1]) differ — draft vs critique.
    assert fake.calls[0]["system"][0]["text"] == fake.calls[1]["system"][0]["text"]
    assert fake.calls[0]["system"][1]["text"] != fake.calls[1]["system"][1]["text"]


def test_synthesize_critique_pass_sees_the_draft(tmp_path: Path):
    cfg = _cfg(tmp_path)
    leadmagnet = tmp_path / "leadmagnet.md"
    leadmagnet.write_text("LEAD")

    fake = _FakeAnthropic()
    with patch.object(synthesizer, "Anthropic", lambda **kw: fake):
        synthesizer.synthesize(cfg, leadmagnet)

    second_user = fake.calls[1]["messages"][0]["content"]
    assert "<draft>" in second_user
    assert "DRAFT BODY" in second_user


def test_synthesize_requires_api_key(tmp_path: Path):
    cfg = _cfg(tmp_path, api_key=None)
    leadmagnet = tmp_path / "leadmagnet.md"
    leadmagnet.write_text("LEAD")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        synthesizer.synthesize(cfg, leadmagnet)


def test_synthesize_requires_leadmagnet_file(tmp_path: Path):
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        synthesizer.synthesize(cfg, tmp_path / "missing.md")
