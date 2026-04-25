"""Selector-rotation canaries.

These fixtures live at tests/fixtures/*.html and mirror the production DOM
shape the parsers target. When LinkedIn rotates class names, edit the
fixtures FIRST to match the new structure, then update the parser. If the
fixtures match production but the parser doesn't, these tests fail loudly
before the live scrape silently degrades to empty rows.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.parser import parse_post_html, passes_inclusion_rules
from src.profile_parser import parse_profile_attributes

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- Posts ---------------------------------------------------------------


def test_post_full_fixture_parses_all_fields():
    cand = parse_post_html(_fixture("post_full.html"))
    assert cand is not None
    assert cand.post_urn == "urn:li:activity:7000000000000000001"
    assert cand.reactions == 1234
    assert cand.comments == 56
    assert cand.reshares == 7
    assert cand.is_repost is False
    assert cand.is_sponsored is False
    assert cand.has_text is True
    assert "fake post body" in cand.post_text
    assert cand.post_date == "2026-04-15T09:30:00Z"
    assert cand.raw_html  # non-empty


def test_post_repost_fixture_flags_repost():
    cand = parse_post_html(_fixture("post_repost.html"))
    assert cand is not None
    assert cand.is_repost is True
    # Without commentary >= 50 words, qualifies_as_original is False.
    assert cand.qualifies_as_original is False


def test_post_promoted_fixture_flags_sponsored():
    cand = parse_post_html(_fixture("post_promoted.html"))
    assert cand is not None
    assert cand.is_sponsored is True


def test_post_full_passes_inclusion_rules_after_age_check():
    """The fixture's post_date is 2026-04-15. Inclusion rule requires age in
    [48h, 30d]. Pin "now" to 5 days later so the post lands inside the
    window."""
    from datetime import datetime, timezone

    cand = parse_post_html(_fixture("post_full.html"))
    assert cand is not None
    now = datetime(2026, 4, 20, 9, 30, 0, tzinfo=timezone.utc)
    assert passes_inclusion_rules(cand, now=now) is True


def test_post_repost_rejected_by_inclusion_rules():
    """Repost without commentary is rejected even if recency matches."""
    from datetime import datetime, timezone

    cand = parse_post_html(_fixture("post_repost.html"))
    assert cand is not None
    now = datetime(2026, 4, 13, 8, 0, 0, tzinfo=timezone.utc)
    assert passes_inclusion_rules(cand, now=now) is False


def test_post_promoted_rejected_by_inclusion_rules():
    from datetime import datetime, timezone

    cand = parse_post_html(_fixture("post_promoted.html"))
    assert cand is not None
    # Even with valid age, sponsored posts are filtered out.
    now = datetime(2026, 4, 20, 0, 0, 0, tzinfo=timezone.utc)
    assert passes_inclusion_rules(cand, now=now) is False


# --- Profile -------------------------------------------------------------


def test_profile_full_fixture_parses_follower_count():
    snap = parse_profile_attributes(_fixture("profile_full.html"))
    assert snap.follower_count == 12_345


def test_profile_full_fixture_parses_headline():
    snap = parse_profile_attributes(_fixture("profile_full.html"))
    assert snap.headline is not None
    assert "Synthetic founder" in snap.headline
    assert "retainer trap" in snap.headline


def test_profile_full_fixture_parses_about():
    snap = parse_profile_attributes(_fixture("profile_full.html"))
    assert snap.about_text is not None
    assert "ship better content" in snap.about_text


def test_profile_full_fixture_parses_role_and_company():
    snap = parse_profile_attributes(_fixture("profile_full.html"))
    assert snap.current_role == "Founder"
    assert snap.current_company == "Synthetic Labs"


def test_profile_full_fixture_parses_location():
    snap = parse_profile_attributes(_fixture("profile_full.html"))
    assert snap.location == "London, United Kingdom"


def test_profile_full_fixture_detects_photo_and_banner():
    snap = parse_profile_attributes(_fixture("profile_full.html"))
    assert snap.has_profile_photo is True
    assert snap.has_banner is True


def test_profile_full_fixture_counts_featured_items():
    snap = parse_profile_attributes(_fixture("profile_full.html"))
    assert snap.featured_count == 3
