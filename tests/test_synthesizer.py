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
        top_growth_posts_md="GROWTH",
        leadmagnet_skill="LEAD",
    )
    assert "<statistical_findings>" in s
    assert "TOP" in s
    assert "BOTTOM" in s
    assert "GROWTH" in s
    assert "<posts_that_pulled_readers_to_profile>" in s
    assert "LEAD" in s
    assert "dugg_voice_profile" in s


def test_synthesize_falls_back_when_top_growth_file_missing(tmp_path: Path):
    """If top_growth_posts.md hasn't been written yet (profile pipeline
    skipped), synthesize() supplies a 'not generated yet' placeholder
    rather than raising."""
    cfg = _cfg(tmp_path)
    leadmagnet = tmp_path / "leadmagnet.md"
    leadmagnet.write_text("LEAD")

    fake = _FakeAnthropic()
    with patch.object(synthesizer, "Anthropic", lambda **kw: fake):
        synthesizer.synthesize(cfg, leadmagnet)

    static = fake.calls[0]["system"][0]["text"]
    assert "Not generated yet" in static or "not yet" in static.lower()


def test_synthesize_reads_top_growth_file_when_present(tmp_path: Path):
    cfg = _cfg(tmp_path)
    leadmagnet = tmp_path / "leadmagnet.md"
    leadmagnet.write_text("LEAD")
    (cfg.output_dir / "top_growth_posts.md").write_text(
        "# Top growth\n\n## Alice — +50 followers\n\nbody\n"
    )

    fake = _FakeAnthropic()
    with patch.object(synthesizer, "Anthropic", lambda **kw: fake):
        synthesizer.synthesize(cfg, leadmagnet)

    static = fake.calls[0]["system"][0]["text"]
    assert "+50 followers" in static


# --- end-to-end with mocked Anthropic -------------------------------------


class _Block:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text: str):
        self.content = [_Block(text)]


_GOOD_REVISED = (
    "<critique>\n- frontmatter missing\n- voice not applied\n</critique>\n\n"
    "---\nname: linkedin-high-engagement-writer\n"
    "description: Author empirically-grounded LinkedIn thought-leadership posts.\n"
    "---\n\n"
    "# linkedin-high-engagement-writer\n\nbody after revision\n\n"
    + "filler line for length sanity check.\n" * 30
)

_GOOD_DRAFT = (
    "---\nname: linkedin-high-engagement-writer\n"
    "description: draft\n---\n\n"
    "# DRAFT BODY (intentionally minimal)\n\n"
    + "filler line for length sanity check.\n" * 30
)


class _FakeAnthropic:
    """Records calls; first call returns a draft, second returns critique + revised body."""

    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.calls)
        if n == 1:
            return _Resp(_GOOD_DRAFT)
        return _Resp(_GOOD_REVISED)


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


# --- S1 fallback ---------------------------------------------------------


class _BadReviseFakeAnthropic:
    """Pass 2 returns just the critique, no SKILL.md body — should trigger fallback."""

    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.calls)
        if n == 1:
            return _Resp(_GOOD_DRAFT)
        return _Resp("<critique>\n- everything is wrong\n</critique>")


def test_synthesize_falls_back_to_draft_when_revise_is_malformed(tmp_path: Path):
    cfg = _cfg(tmp_path)
    leadmagnet = tmp_path / "leadmagnet.md"
    leadmagnet.write_text("LEAD")

    fake = _BadReviseFakeAnthropic()
    with patch.object(synthesizer, "Anthropic", lambda **kw: fake):
        out = synthesizer.synthesize(cfg, leadmagnet)

    body = out.read_text()
    # Falls back to draft
    assert "DRAFT BODY" in body
    # Draft preserved separately too
    assert (cfg.output_dir / "synthesis_draft.md").exists()


class _ShortReviseFakeAnthropic:
    """Pass 2 returns frontmatter but body is too short — should fall back."""

    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.calls)
        if n == 1:
            return _Resp(_GOOD_DRAFT)
        return _Resp("---\nname: linkedin-high-engagement-writer\n---\n# tiny")


def test_synthesize_falls_back_when_revise_is_too_short(tmp_path: Path):
    cfg = _cfg(tmp_path)
    leadmagnet = tmp_path / "leadmagnet.md"
    leadmagnet.write_text("LEAD")

    fake = _ShortReviseFakeAnthropic()
    with patch.object(synthesizer, "Anthropic", lambda **kw: fake):
        out = synthesizer.synthesize(cfg, leadmagnet)

    assert "DRAFT BODY" in out.read_text()


# --- S4 strip safety -----------------------------------------------------


def test_strip_critique_block_does_not_strip_when_critique_is_not_at_start():
    """A </critique> embedded later in the body shouldn't trigger over-strip."""
    text = (
        "---\nname: skill\n---\n\n"
        "# Skill\n\nThe judge expects `<critique>foo</critique>` style output."
    )
    assert synthesizer._strip_critique_block(text) == text


def test_strip_critique_block_handles_leading_whitespace():
    text = "  \n\n<critique>foo</critique>\n# Final"
    assert synthesizer._strip_critique_block(text) == "# Final"


# --- S2 / S3 explicit assertions on call kwargs ---------------------------


def test_synthesize_sets_temperature_and_distinct_max_tokens(tmp_path: Path):
    cfg = _cfg(tmp_path)
    leadmagnet = tmp_path / "leadmagnet.md"
    leadmagnet.write_text("LEAD")

    fake = _FakeAnthropic()
    with patch.object(synthesizer, "Anthropic", lambda **kw: fake):
        synthesizer.synthesize(cfg, leadmagnet)

    draft_call, revise_call = fake.calls
    # Temperature pinned for reproducibility
    assert draft_call["temperature"] == synthesizer.TEMPERATURE
    assert revise_call["temperature"] == synthesizer.TEMPERATURE
    # Revise gets more headroom than draft
    assert revise_call["max_tokens"] > draft_call["max_tokens"]
