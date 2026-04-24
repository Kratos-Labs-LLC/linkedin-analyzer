from datetime import datetime, timedelta, timezone

import pytest

from src import parser


def test_parse_number_with_suffix():
    assert parser.parse_number_with_suffix("12,345") == 12_345
    assert parser.parse_number_with_suffix("12K") == 12_000
    assert parser.parse_number_with_suffix("1.2M") == 1_200_000
    assert parser.parse_number_with_suffix("42 reactions") == 42
    assert parser.parse_number_with_suffix("") is None
    assert parser.parse_number_with_suffix("no numbers here") is None


def test_parse_reaction_count_handles_aria_labels():
    assert parser.parse_reaction_count("12,543 reactions") == 12_543
    assert parser.parse_reaction_count("1.2K comments") == 1_200
    assert parser.parse_reaction_count(None) == 0


def test_parse_follower_count_variants():
    assert parser.parse_follower_count("12,345 followers") == 12_345
    assert parser.parse_follower_count("12K followers") == 12_000
    assert parser.parse_follower_count("1.2M followers") == 1_200_000
    assert parser.parse_follower_count("no followers here and nothing else") is None


def test_parse_relative_post_date():
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    iso = parser.parse_relative_post_date("3 days ago", now=now)
    assert iso is not None
    parsed = datetime.fromisoformat(iso)
    assert (now - parsed).days == 3

    iso_short = parser.parse_relative_post_date("2w", now=now)
    parsed_short = datetime.fromisoformat(iso_short)
    assert (now - parsed_short).days == 14


def test_post_age_hours():
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    past = (now - timedelta(hours=72)).isoformat()
    assert parser.post_age_hours(past, now=now) == pytest.approx(72.0)
    assert parser.post_age_hours(None) is None


def test_extract_post_urn():
    assert parser.extract_post_urn("urn:li:activity:123") == "urn:li:activity:123"
    assert parser.extract_post_urn("prefix urn:li:ugcPost:456 suffix") == "urn:li:ugcPost:456"
    assert parser.extract_post_urn("no urn") is None


def _make_candidate(**kwargs):
    defaults = dict(
        post_urn="urn:li:activity:1",
        post_url=None,
        post_text="a" * 200,
        reactions=100,
        comments=10,
        reshares=5,
        post_date=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
        is_repost=False,
        repost_commentary_word_count=0,
        is_sponsored=False,
        has_text=True,
    )
    defaults.update(kwargs)
    return parser.PostCandidate(**defaults)


def test_passes_inclusion_rules_ok():
    assert parser.passes_inclusion_rules(_make_candidate()) is True


def test_passes_inclusion_rules_rejects_sponsored():
    assert parser.passes_inclusion_rules(_make_candidate(is_sponsored=True)) is False


def test_passes_inclusion_rules_rejects_too_young():
    recent = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    assert parser.passes_inclusion_rules(_make_candidate(post_date=recent)) is False


def test_passes_inclusion_rules_rejects_too_old():
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    assert parser.passes_inclusion_rules(_make_candidate(post_date=old)) is False


def test_passes_inclusion_rules_rejects_repost_with_short_commentary():
    cand = _make_candidate(is_repost=True, repost_commentary_word_count=10)
    assert parser.passes_inclusion_rules(cand) is False


def test_passes_inclusion_rules_accepts_repost_with_long_commentary():
    cand = _make_candidate(is_repost=True, repost_commentary_word_count=75)
    assert parser.passes_inclusion_rules(cand) is True


def test_passes_inclusion_rules_rejects_empty_text():
    assert parser.passes_inclusion_rules(_make_candidate(has_text=False, post_text="")) is False


def test_parse_post_html_minimal_fixture():
    html = """
    <div data-urn="urn:li:activity:987654" class="feed-shared-update-v2">
      <span class="update-components-text">Hello world from a top post</span>
      <button aria-label="1,234 reactions">like</button>
      <button aria-label="56 comments">comment</button>
      <button aria-label="7 reposts">repost</button>
      <time datetime="2026-01-01T10:00:00Z">Jan 1</time>
    </div>
    """
    cand = parser.parse_post_html(html)
    assert cand is not None
    assert cand.post_urn == "urn:li:activity:987654"
    assert "Hello world" in cand.post_text
    assert cand.reactions == 1_234
    assert cand.comments == 56
    assert cand.reshares == 7
    assert cand.is_sponsored is False


def test_parse_post_html_detects_sponsored():
    html = """
    <div data-urn="urn:li:activity:1" class="feed-shared-update-v2">
      <span>Promoted</span>
      <span class="update-components-text">sponsored thing</span>
    </div>
    """
    cand = parser.parse_post_html(html)
    assert cand is not None
    assert cand.is_sponsored is True


def test_parse_post_html_no_urn_returns_none():
    html = "<div>no urn at all</div>"
    assert parser.parse_post_html(html) is None
