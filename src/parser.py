"""Pure-function DOM extraction helpers for LinkedIn activity feeds.

Designed to be testable without a live browser: the core functions accept
either a BeautifulSoup-compatible element OR a raw HTML string. Playwright
Locators in the collector render to HTML and feed into these helpers.

Selectors are kept at module-level with a fallback chain; if LinkedIn rotates
class names, update selectors here only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

# --- Selectors (primary -> fallback). When updating, put the most specific first. ---
POST_CONTAINER_SELECTORS = [
    "div.feed-shared-update-v2",
    "div[data-urn^='urn:li:activity']",
]
POST_URN_ATTR = "data-urn"
SEE_MORE_SELECTORS = [
    "button.feed-shared-inline-show-more-text__see-more-less-toggle",
    "button[aria-label^='see more']",
]
REPOST_INDICATOR_SELECTORS = [
    "span.feed-shared-actor__description",  # "X reposted this"
    "span[data-test-reshared-indicator]",
]
PROMOTED_INDICATOR_TEXTS = {"promoted", "sponsored"}
FOLLOWER_COUNT_SELECTORS = [
    "span.t-bold",
    "li.text-body-small",
]

_URN_RE = re.compile(r"urn:li:(?:activity|ugcPost):\d+")
_NUMBER_RE = re.compile(r"([\d,\.]+)\s*([KkMm]?)")
_FOLLOWER_RE = re.compile(r"([\d,\.]+)\s*([KkMm]?)\s*follower", re.IGNORECASE)
_RELATIVE_TIME_RE = re.compile(
    r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago", re.IGNORECASE
)


@dataclass
class PostCandidate:
    post_urn: str
    post_url: str | None
    post_text: str
    reactions: int
    comments: int
    reshares: int
    post_date: str | None
    is_repost: bool
    repost_commentary_word_count: int
    is_sponsored: bool
    has_text: bool

    @property
    def qualifies_as_original(self) -> bool:
        """§4.3: original, or repost with >= 50 words of commentary."""
        if not self.is_repost:
            return True
        return self.repost_commentary_word_count >= 50


# --- Text-based parsers (testable without DOM) ---


def parse_number_with_suffix(text: str) -> int | None:
    """Parse '12,345' / '12K' / '1.2M' / '1,234 reactions' -> int."""
    if not text:
        return None
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    raw, suffix = m.group(1), m.group(2).upper()
    try:
        val = float(raw.replace(",", ""))
    except ValueError:
        return None
    if suffix == "K":
        val *= 1_000
    elif suffix == "M":
        val *= 1_000_000
    return int(val)


def parse_reaction_count(aria_label: str | None) -> int:
    """'12,543 reactions' or '12,543 people reacted' -> 12543. Missing -> 0."""
    if not aria_label:
        return 0
    val = parse_number_with_suffix(aria_label)
    return val or 0


def parse_follower_count(text: str) -> int | None:
    """Extract follower count from '12,345 followers' / '12K followers' / '1.2M followers'."""
    if not text:
        return None
    m = _FOLLOWER_RE.search(text)
    if not m:
        return None
    return parse_number_with_suffix(f"{m.group(1)}{m.group(2)}")


def parse_relative_post_date(text: str, now: datetime | None = None) -> str | None:
    """'3d' / '3 days ago' / '2w' / '1 month ago' -> ISO timestamp approximation.

    Returns None if unparseable. Used to compute post age for §4.3 filters.
    """
    if not text:
        return None
    now = now or datetime.now(timezone.utc)

    m = _RELATIVE_TIME_RE.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
    else:
        short = re.search(r"(\d+)\s*([smhdwy])\b", text, re.IGNORECASE)
        if not short:
            return None
        n = int(short.group(1))
        unit = {
            "s": "second",
            "m": "minute",
            "h": "hour",
            "d": "day",
            "w": "week",
            "y": "year",
        }[short.group(2).lower()]

    delta_map = {
        "second": timedelta(seconds=n),
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=30 * n),
        "year": timedelta(days=365 * n),
    }
    ts = now - delta_map[unit]
    return ts.isoformat()


def extract_post_urn(raw: str | None) -> str | None:
    if not raw:
        return None
    m = _URN_RE.search(raw)
    return m.group(0) if m else None


def post_age_hours(post_date_iso: str | None, now: datetime | None = None) -> float | None:
    if not post_date_iso:
        return None
    try:
        dt = datetime.fromisoformat(post_date_iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 3600.0


def passes_inclusion_rules(post: PostCandidate, now: datetime | None = None) -> bool:
    """§4.3 filters applied to a parsed candidate."""
    if post.is_sponsored:
        return False
    if not post.has_text or not post.post_text.strip():
        return False
    if not post.qualifies_as_original:
        return False
    age = post_age_hours(post.post_date, now=now)
    if age is None:
        return False
    if age < 48:
        return False
    if age > 30 * 24:
        return False
    return True


# --- Lightweight HTML parser used by the collector to turn rendered HTML -> candidates. ---


class _PostHTMLExtractor(HTMLParser):
    """Minimal extractor — collects urn, text, reaction/comment counts, repost markers.

    NOT a full LinkedIn parser. The real work is done by Playwright selectors in
    the collector. This class exists so unit tests can run without a browser.
    """

    def __init__(self) -> None:
        super().__init__()
        self.urn: str | None = None
        self.post_url: str | None = None
        self.text_chunks: list[str] = []
        self.reactions_label: str | None = None
        self.comments_label: str | None = None
        self.reshares_label: str | None = None
        self.is_repost: bool = False
        self.is_sponsored: bool = False
        self.date_text: str | None = None
        self._in_text = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if "data-urn" in a and not self.urn:
            self.urn = extract_post_urn(a.get("data-urn"))
        cls = (a.get("class") or "").lower()
        label = (a.get("aria-label") or "").lower()
        dtest = (a.get("data-test") or "").lower()

        if "reaction" in label and self.reactions_label is None:
            self.reactions_label = a.get("aria-label")
        if "comment" in label and self.comments_label is None:
            self.comments_label = a.get("aria-label")
        if ("repost" in label or "reshare" in label) and self.reshares_label is None:
            self.reshares_label = a.get("aria-label")
        if "promoted" in cls or "sponsored" in cls or dtest == "promoted":
            self.is_sponsored = True
        if "reshared" in cls or "reposted" in cls:
            self.is_repost = True
        if tag == "time" and self.date_text is None:
            self.date_text = a.get("datetime")
        if tag in ("span", "div", "p"):
            if "post-text" in cls or "update-components-text" in cls or "commentary" in cls:
                self._in_text = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("span", "div", "p"):
            self._in_text = False

    def handle_data(self, data: str) -> None:
        if self._in_text and data.strip():
            self.text_chunks.append(data)
        low = data.strip().lower()
        if low in PROMOTED_INDICATOR_TEXTS:
            self.is_sponsored = True


def parse_post_html(html: str) -> PostCandidate | None:
    """Parse a single post container's HTML into a PostCandidate.

    Returns None if the post lacks a URN (unidentifiable).
    """
    ex = _PostHTMLExtractor()
    ex.feed(html)
    if not ex.urn:
        return None
    text = " ".join(c.strip() for c in ex.text_chunks if c.strip())
    reactions = parse_reaction_count(ex.reactions_label)
    comments = parse_reaction_count(ex.comments_label)
    reshares = parse_reaction_count(ex.reshares_label)
    word_count = len(text.split())
    return PostCandidate(
        post_urn=ex.urn,
        post_url=ex.post_url,
        post_text=text,
        reactions=reactions,
        comments=comments,
        reshares=reshares,
        post_date=ex.date_text,
        is_repost=ex.is_repost,
        repost_commentary_word_count=word_count if ex.is_repost else 0,
        is_sponsored=ex.is_sponsored,
        has_text=bool(text),
    )
