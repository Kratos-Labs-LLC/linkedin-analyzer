"""Tests for src/analyzer/intelligence_synth.py.

Anthropic is fully mocked. We assert:
  - successful synthesis returns the model's text
  - sanity-check fallback fires when required headers are missing
  - cached static block is sent (cache_control + type=ephemeral)
  - token usage log line is emitted
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.analyzer import intelligence_synth


# --- Shared mock helpers ----------------------------------------------


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
    """A synthesis output that satisfies the sanity check."""
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
    return "\n\n".join([f"{h}\n\n{body}" for h in sections])


def _pack() -> dict:
    return {
        "creator": {"id": 1, "display_name": "Test Creator"},
        "profile": {"headline": "..."},
        "posts": {"n_total": 10},
        "self_topquartile_vs_bottomquartile": {},
        "cohort_delta": {},
        "growth_correlation": {"top_growth_posts": [], "patterns_in_top_growth": {}},
        "top_5_posts": [],
        "bottom_3_posts": [],
    }


def _baseline() -> dict:
    return {
        "n_creators_in_baseline": 25,
        "n_posts_in_baseline": 500,
        "categorical_distribution": {"hook_type": {"story": 0.4, "bold_claim": 0.3}},
        "engagement_median": 0.029,
    }


# --- Tests -------------------------------------------------------------


def test_successful_synthesis_returns_model_text():
    client = MagicMock()
    client.messages.create.return_value = _Resp(_good_doc())
    out = intelligence_synth.synthesize_intelligence(
        _pack(), _baseline(), client=client
    )
    # Output is the model's text — every required section is present.
    for header in intelligence_synth.REQUIRED_SECTIONS:
        assert header in out


def test_sanity_check_fallback_when_required_section_missing():
    """If the model output lacks one of the required `##` sections, we
    return a stub doc instead of the broken output. The pack JSON is
    embedded inside the stub for inspection."""
    incomplete = _good_doc().replace("## Replicable plays", "## Wrong header")
    client = MagicMock()
    client.messages.create.return_value = _Resp(incomplete)
    out = intelligence_synth.synthesize_intelligence(
        _pack(), _baseline(), client=client
    )
    assert "auto-stub" in out
    assert "synthesizer fallback" in out.lower()
    # The full pack JSON should be embedded in the stub for debugging.
    assert '"display_name": "Test Creator"' in out


def test_sanity_check_fallback_when_doc_too_short():
    client = MagicMock()
    client.messages.create.return_value = _Resp("## TL;DR\n\nshort")
    out = intelligence_synth.synthesize_intelligence(
        _pack(), _baseline(), client=client
    )
    assert "auto-stub" in out


def test_uses_cached_static_block():
    client = MagicMock()
    client.messages.create.return_value = _Resp(_good_doc())
    intelligence_synth.synthesize_intelligence(_pack(), _baseline(), client=client)
    call = client.messages.create.call_args
    system_blocks = call.kwargs["system"]
    assert isinstance(system_blocks, list)
    assert len(system_blocks) == 1
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert system_blocks[0]["type"] == "text"
    # The cohort baseline JSON shows up in the static text — that's why
    # caching it across creators is meaningful.
    assert "cohort_baseline" in system_blocks[0]["text"]


def test_token_usage_logged(caplog):
    import logging

    client = MagicMock()
    client.messages.create.return_value = _Resp(_good_doc())
    with caplog.at_level(logging.INFO, logger="src.analyzer.intelligence_synth"):
        intelligence_synth.synthesize_intelligence(
            _pack(), _baseline(), client=client
        )
    # At least one log line that mentions input/output tokens.
    assert any(
        "intelligence_synth" in r.message and "usage" in r.message
        for r in caplog.records
    )


def test_pack_json_appears_in_user_message():
    """Sanity: the per-creator user message carries the pack so the model
    has data to synthesize from."""
    client = MagicMock()
    client.messages.create.return_value = _Resp(_good_doc())
    intelligence_synth.synthesize_intelligence(_pack(), _baseline(), client=client)
    call = client.messages.create.call_args
    user_msg = call.kwargs["messages"][0]["content"]
    assert "intelligence_pack" in user_msg
    assert "Test Creator" in user_msg


def test_requires_client_or_api_key():
    with pytest.raises(RuntimeError, match="client or an api_key"):
        intelligence_synth.synthesize_intelligence(_pack(), _baseline())
