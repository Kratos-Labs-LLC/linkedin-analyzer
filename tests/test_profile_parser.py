from src.profile_parser import (
    ProfileSnapshot,
    _extract_role_and_company,
    parse_profile_attributes,
)


# --- pure helpers --------------------------------------------------------


def test_extract_role_and_company_basic():
    role, company = _extract_role_and_company("Founder at Acme Corp")
    assert role == "Founder"
    assert company == "Acme Corp"


def test_extract_role_and_company_at_symbol():
    role, company = _extract_role_and_company("CTO @ Beta Labs")
    assert role == "CTO"
    assert company == "Beta Labs"


def test_extract_role_and_company_no_at():
    role, company = _extract_role_and_company("Independent Consultant")
    assert role == "Independent Consultant"
    assert company is None


def test_extract_role_and_company_empty():
    assert _extract_role_and_company(None) == (None, None)
    assert _extract_role_and_company("") == (None, None)


# --- parse_profile_attributes end-to-end ---------------------------------


def test_parse_minimal_profile_returns_all_nones_except_html():
    snap = parse_profile_attributes("<html><body></body></html>")
    assert isinstance(snap, ProfileSnapshot)
    assert snap.follower_count is None
    assert snap.headline is None
    assert snap.about_text is None
    assert snap.current_role is None
    assert snap.current_company is None
    assert snap.location is None
    assert snap.has_profile_photo is None
    assert snap.has_banner is None
    assert snap.featured_count is None
    assert snap.raw_html == "<html><body></body></html>"


def test_parse_picks_up_follower_count():
    html = "<html><body><span>12,345 followers</span></body></html>"
    snap = parse_profile_attributes(html)
    assert snap.follower_count == 12_345


def test_parse_extracts_headline_with_text_body_medium():
    html = """
    <html><body>
      <div class="text-body-medium">Founder helping agencies escape the retainer trap</div>
    </body></html>
    """
    snap = parse_profile_attributes(html)
    assert snap.headline == "Founder helping agencies escape the retainer trap"


def test_parse_detects_profile_photo_class():
    html = '<html><body><img class="pv-top-card__photo" src="x.jpg"></body></html>'
    snap = parse_profile_attributes(html)
    assert snap.has_profile_photo is True


def test_parse_detects_banner_class():
    html = '<html><body><div class="profile-background-image"></div></body></html>'
    snap = parse_profile_attributes(html)
    assert snap.has_banner is True


def test_parse_finds_about_text_inside_section():
    html = """
    <html><body>
      <section id="about">
        <p>I help agency founders ship better content.</p>
        <p>Ex-McKinsey, now solo.</p>
      </section>
    </body></html>
    """
    snap = parse_profile_attributes(html)
    assert snap.about_text is not None
    assert "agency founders" in snap.about_text
    assert "Ex-McKinsey" in snap.about_text


def test_parse_caps_about_text_at_4000_chars():
    body = "x" * 5000
    html = f'<html><body><section id="about"><p>{body}</p></section></body></html>'
    snap = parse_profile_attributes(html)
    assert snap.about_text is not None
    assert len(snap.about_text) == 4000


def test_parse_extracts_first_role_and_company_from_experience():
    html = """
    <html><body>
      <section id="experience">
        <span>Founder at Acme Corp</span>
      </section>
    </body></html>
    """
    snap = parse_profile_attributes(html)
    assert snap.current_role == "Founder"
    assert snap.current_company == "Acme Corp"


def test_parse_counts_featured_items():
    html = """
    <html><body>
      <section id="featured">
        <li>Item one</li>
        <li>Item two</li>
        <li>Item three</li>
      </section>
    </body></html>
    """
    snap = parse_profile_attributes(html)
    assert snap.featured_count == 3


def test_parse_picks_up_location_when_text_body_small_has_comma():
    html = """
    <html><body>
      <span class="text-body-small">London, United Kingdom</span>
    </body></html>
    """
    snap = parse_profile_attributes(html)
    assert snap.location == "London, United Kingdom"


def test_first_experience_role_is_a_public_extractor_field():
    """F2: cross-module readers (profile_audit, eventual dashboard helpers)
    shouldn't reach across an underscore boundary."""
    from src.profile_parser import _ProfileHTMLExtractor

    ex = _ProfileHTMLExtractor()
    ex.feed(
        '<section id="experience"><span>Founder at Acme</span></section>'
    )
    assert ex.first_experience_role == "Founder at Acme"


def test_parse_full_synthetic_profile():
    """Sanity: a synthetic profile that exercises every field at once."""
    html = """
    <html><body>
      <img class="pv-top-card__photo" src="me.jpg">
      <div class="profile-background-image"></div>
      <div class="text-body-medium">Founder | Helping marketing agencies escape the retainer trap</div>
      <span class="text-body-small">London, United Kingdom</span>
      <span>12,345 followers</span>
      <section id="about">
        <p>I help agency founders ship better content and productize their delivery.</p>
      </section>
      <section id="experience">
        <span>Founder at Kratos Labs</span>
      </section>
      <section id="featured">
        <li>Lead magnet 1</li>
        <li>Podcast 1</li>
      </section>
    </body></html>
    """
    snap = parse_profile_attributes(html)
    assert snap.follower_count == 12_345
    assert snap.headline is not None and "retainer trap" in snap.headline
    assert snap.about_text is not None and "agency founders" in snap.about_text
    assert snap.current_role == "Founder"
    assert snap.current_company == "Kratos Labs"
    assert snap.location == "London, United Kingdom"
    assert snap.has_profile_photo is True
    assert snap.has_banner is True
    assert snap.featured_count == 2
