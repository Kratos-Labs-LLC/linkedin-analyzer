"""Pure-function DOM extraction for LinkedIn profile pages.

Mirrors src/parser.py for posts: an HTMLParser-backed extractor that pulls
out everything we want from the profile page in one pass over the rendered
HTML the collector already captures. No new browser requests.

Selector / class-name heuristics live as module constants so updating them
when LinkedIn rotates names is a one-place change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from src.parser import parse_follower_count

# LinkedIn rotates class hashes but the semantic class fragments below have
# survived multiple rotations. Anything we match on is a substring check —
# `"profile-photo" in cls`, not exact equality — so a longer hashed class
# like `pv-top-card__photo--abc123 profile-photo-edit__container` still hits.
HEADLINE_CLASS_HINTS = {"text-body-medium", "pv-text-details__about-this-profile-entrypoint"}
HEADLINE_TAG_HINTS = {"div", "span"}
ABOUT_SECTION_CLASS_HINT = "pv-shared-text-with-see-more"
ABOUT_SECTION_ID_HINTS = {"about"}
EXPERIENCE_SECTION_ID_HINTS = {"experience"}
FEATURED_SECTION_ID_HINTS = {"featured"}
PROFILE_PHOTO_CLASS_HINTS = {"pv-top-card__photo", "pv-top-card-profile-picture", "profile-photo-edit__preview"}
BANNER_CLASS_HINTS = {"profile-background-image", "pv-top-card__background-image"}
LOCATION_CLASS_HINTS = {"text-body-small"}

_WHITESPACE_RE = re.compile(r"\s+")
_NAME_AT_COMPANY_RE = re.compile(r"^(.+?)\s+(?:at|@)\s+(.+)$", re.IGNORECASE)


@dataclass
class ProfileSnapshot:
    follower_count: int | None
    headline: str | None
    about_text: str | None
    current_role: str | None
    current_company: str | None
    location: str | None
    has_profile_photo: bool | None
    has_banner: bool | None
    featured_count: int | None
    raw_html: str


class _ProfileHTMLExtractor(HTMLParser):
    """Streaming-style extractor — accumulates text in named buckets keyed on
    the section we're currently inside. Robust to extra wrapper tags around
    each section because we track a stack-depth-style counter per section.
    """

    def __init__(self) -> None:
        super().__init__()
        self.headline: str | None = None
        self.about_chunks: list[str] = []
        self.location_chunks: list[str] = []
        self.experience_chunks: list[str] = []
        self.featured_count: int = 0
        self.has_profile_photo: bool = False
        self.has_banner: bool = False

        # Section depth counters — non-zero = we're inside that section.
        self._in_about_depth = 0
        self._in_about_text_depth = 0
        self._in_experience_depth = 0
        self.first_experience_role: str | None = None
        self._capture_first_experience_text_depth = 0
        self._in_featured_depth = 0
        # Headline grabber: capture the first text after we see a headline-like
        # element, then stop.
        self._capture_headline_depth = 0
        self._headline_buffer: list[str] = []
        # Location: typically appears as text-body-small inside the top card.
        self._capture_location_depth = 0
        # Featured items: count list-item-ish nodes inside the featured section.
        self._featured_item_seen = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = (a.get("class") or "").lower()
        sid = (a.get("id") or "").lower()

        # Profile photo / banner detection — presence of an element with these
        # classes is enough; we don't need to verify the src.
        for hint in PROFILE_PHOTO_CLASS_HINTS:
            if hint in cls:
                self.has_profile_photo = True
                break
        for hint in BANNER_CLASS_HINTS:
            if hint in cls:
                self.has_banner = True
                break

        # Section entry markers
        if sid in ABOUT_SECTION_ID_HINTS or ABOUT_SECTION_CLASS_HINT in cls:
            self._in_about_depth += 1
        elif self._in_about_depth > 0:
            self._in_about_depth += 1
            # Capture text inside about, but skip "About" heading itself.
            if tag in ("p", "span", "div"):
                self._in_about_text_depth += 1

        if sid in EXPERIENCE_SECTION_ID_HINTS:
            self._in_experience_depth += 1
            self._capture_first_experience_text_depth = 1
        elif self._in_experience_depth > 0:
            self._in_experience_depth += 1
            if (
                self._capture_first_experience_text_depth > 0
                and tag in ("span", "div", "p")
                and self.first_experience_role is None
            ):
                self._capture_first_experience_text_depth += 1

        if sid in FEATURED_SECTION_ID_HINTS:
            self._in_featured_depth += 1
        elif self._in_featured_depth > 0:
            self._in_featured_depth += 1
            # Count distinct featured items: a `<li>` or a `<a>` with a
            # featured-item-ish class.
            if tag == "li" or "feed-mini-update-v2" in cls or "pv-featured-item" in cls:
                self._featured_item_seen = True
                self.featured_count += 1

        # Headline candidates — the first one we see is good enough. LinkedIn
        # places it near the profile photo, marked with text-body-medium.
        if (
            self.headline is None
            and tag in HEADLINE_TAG_HINTS
            and any(h in cls for h in HEADLINE_CLASS_HINTS)
        ):
            self._capture_headline_depth = 1

        # Location marker — text-body-small, but only AFTER we've passed the
        # photo. Rather than depend on order, we capture every text-body-small
        # text and pick the most "city-ish" one later.
        if any(h in cls for h in LOCATION_CLASS_HINTS):
            self._capture_location_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._in_about_depth > 0:
            self._in_about_depth = max(0, self._in_about_depth - 1)
            if self._in_about_text_depth > 0:
                self._in_about_text_depth = max(0, self._in_about_text_depth - 1)
        if self._in_experience_depth > 0:
            self._in_experience_depth = max(0, self._in_experience_depth - 1)
            if self._capture_first_experience_text_depth > 0:
                self._capture_first_experience_text_depth = max(
                    0, self._capture_first_experience_text_depth - 1
                )
        if self._in_featured_depth > 0:
            self._in_featured_depth = max(0, self._in_featured_depth - 1)
        if self._capture_headline_depth > 0:
            self._capture_headline_depth = max(0, self._capture_headline_depth - 1)
            if self._capture_headline_depth == 0 and self._headline_buffer:
                self.headline = " ".join(self._headline_buffer).strip()
                self._headline_buffer = []
        if self._capture_location_depth > 0:
            self._capture_location_depth = max(0, self._capture_location_depth - 1)

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._capture_headline_depth > 0 and self.headline is None:
            self._headline_buffer.append(text)
        if self._in_about_depth > 0 and self._in_about_text_depth > 0:
            self.about_chunks.append(text)
        if (
            self._in_experience_depth > 0
            and self._capture_first_experience_text_depth > 0
            and self.first_experience_role is None
        ):
            # The first non-whitespace text inside the experience section is
            # the role. The next "at <Company>" pattern feeds the company.
            self.first_experience_role = text
        if self._capture_location_depth > 0:
            # Heuristic: city-ish if it has a comma, no colon, no parentheses.
            if "," in text and ":" not in text and "(" not in text and len(text) <= 80:
                self.location_chunks.append(text)


def _normalize(s: str | None) -> str | None:
    if not s:
        return None
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s or None


def _extract_role_and_company(experience_role: str | None) -> tuple[str | None, str | None]:
    """Best-effort split of '<Title> at <Company>' or '<Title> @ <Company>'.

    Returns (role, company). Either may be None.
    """
    if not experience_role:
        return None, None
    text = _normalize(experience_role) or ""
    m = _NAME_AT_COMPANY_RE.match(text)
    if m:
        return _normalize(m.group(1)), _normalize(m.group(2))
    return _normalize(text), None


def parse_profile_attributes(html: str) -> ProfileSnapshot:
    """Best-effort extraction of profile attributes from rendered HTML.

    Robust to selector rotation: every field is independent, missing data
    falls through to None. The follower-count parser is the existing
    `parse_follower_count` from src/parser.py — handles "12K / 1.2M / 12,345
    followers" already.
    """
    ex = _ProfileHTMLExtractor()
    ex.feed(html)

    follower_count = parse_follower_count(html)

    about_text = _normalize(" ".join(ex.about_chunks)) if ex.about_chunks else None
    if about_text and len(about_text) > 4000:
        about_text = about_text[:4000]

    role, company = _extract_role_and_company(ex.first_experience_role)
    location = _normalize(ex.location_chunks[0]) if ex.location_chunks else None

    return ProfileSnapshot(
        follower_count=follower_count,
        headline=_normalize(ex.headline),
        about_text=about_text,
        current_role=role,
        current_company=company,
        location=location,
        has_profile_photo=ex.has_profile_photo or None,
        has_banner=ex.has_banner or None,
        featured_count=ex.featured_count if ex.featured_count > 0 else None,
        raw_html=html,
    )


