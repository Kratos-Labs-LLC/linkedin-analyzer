"""LinkedIn activity-feed collector using a pre-authenticated persistent Chrome profile."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src import storage
from src.config import AppConfig, CreatorConfig
from src.parser import (
    PostCandidate,
    parse_follower_count,
    parse_post_html,
    passes_inclusion_rules,
)
from src.profile_parser import parse_profile_attributes

log = logging.getLogger(__name__)

POSTS_PER_CREATOR = 5
SCROLLS_PER_FEED = 3
INTER_CREATOR_SLEEP_RANGE = (15, 30)
INTRA_SCROLL_SLEEP_RANGE = (3, 8)
VIEWPORT = {"width": 1920, "height": 1080}
AUTH_SENTINEL = ".authed"


class AuthError(RuntimeError):
    """Raised when LinkedIn shows a captcha or login page during scraping."""


@dataclass
class CollectorResult:
    posts_collected: int
    creators_scraped: int
    errors: list[dict]
    auth_error: bool = False


async def _detect_auth_wall(page) -> bool:
    url = page.url.lower()
    if "login" in url or "checkpoint" in url or "authwall" in url:
        return True
    # Captcha hint: LinkedIn embeds arkoselabs or shows a challenge iframe
    try:
        if await page.locator("iframe[src*='arkoselabs'], iframe[src*='captcha']").count() > 0:
            return True
    except Exception:
        pass
    return False


async def _scrape_creator(page, creator: CreatorConfig, creator_id: int, conn) -> tuple[int, int | None]:
    """Return (posts_inserted, follower_count). Raises AuthError on wall."""
    # 1) Profile page — grab follower count
    await page.goto(creator.profile_url, wait_until="domcontentloaded", timeout=30_000)
    if await _detect_auth_wall(page):
        raise AuthError(f"Auth wall hit on {creator.profile_url}")

    follower_count: int | None = None
    try:
        html = await page.content()
        # Full profile snapshot — follower count comes back from this same
        # parser (parse_follower_count is reused under the hood).
        snapshot = parse_profile_attributes(html)
        follower_count = snapshot.follower_count
        try:
            storage.insert_profile_snapshot(
                conn,
                creator_id=creator_id,
                follower_count=snapshot.follower_count,
                headline=snapshot.headline,
                about_text=snapshot.about_text,
                current_role=snapshot.current_role,
                current_company=snapshot.current_company,
                location=snapshot.location,
                has_profile_photo=snapshot.has_profile_photo,
                has_banner=snapshot.has_banner,
                featured_count=snapshot.featured_count,
                raw_html=snapshot.raw_html,
            )
        except Exception as e:
            log.warning(
                "Failed to persist profile snapshot for %s: %s",
                creator.display_name,
                e,
            )
    except Exception as e:
        log.warning("Failed to read profile for %s: %s", creator.display_name, e)

    # 2) Activity feed
    await page.goto(creator.activity_url, wait_until="domcontentloaded", timeout=30_000)
    if await _detect_auth_wall(page):
        raise AuthError(f"Auth wall hit on {creator.activity_url}")

    for _ in range(SCROLLS_PER_FEED):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        await asyncio.sleep(random.uniform(*INTRA_SCROLL_SLEEP_RANGE))

    # 3) Click "see more" buttons to un-truncate post text
    try:
        see_more = page.locator("button:has-text('see more')")
        count = await see_more.count()
        for i in range(min(count, 20)):
            try:
                await see_more.nth(i).click(timeout=2_000)
            except Exception:
                pass
    except Exception:
        pass

    # 4) Extract post containers
    candidates: list[PostCandidate] = []
    try:
        locators = page.locator("div.feed-shared-update-v2, div[data-urn^='urn:li:activity']")
        n = await locators.count()
        for i in range(min(n, 15)):
            try:
                html = await locators.nth(i).inner_html()
                parsed = parse_post_html(html)
                if parsed:
                    candidates.append(parsed)
            except Exception as e:
                log.debug("Post container %d parse failed: %s", i, e)
    except Exception as e:
        log.warning("Failed to enumerate posts for %s: %s", creator.display_name, e)

    # 5) Apply filters, keep first N valid, insert
    inserted = 0
    for cand in candidates:
        if inserted >= POSTS_PER_CREATOR:
            break
        if not passes_inclusion_rules(cand):
            continue
        if storage.insert_post(
            conn,
            post_urn=cand.post_urn,
            creator_id=creator_id,
            post_url=cand.post_url,
            post_text=cand.post_text,
            reactions=cand.reactions,
            comments=cand.comments,
            reshares=cand.reshares,
            follower_count_at_collection=follower_count,
            post_date=cand.post_date,
            is_repost_with_commentary=cand.is_repost and cand.qualifies_as_original,
            raw_html=cand.raw_html,
        ):
            inserted += 1

    storage.mark_creator_scraped(conn, creator_id, follower_count)
    return inserted, follower_count


async def run_daily_collection(cfg: AppConfig) -> CollectorResult:
    from playwright.async_api import async_playwright

    storage.init_db(cfg.db_path)

    started_at = datetime.now(timezone.utc).isoformat()
    run_date = datetime.now(timezone.utc).date().isoformat()
    errors: list[dict] = []
    posts_collected = 0
    creators_scraped = 0
    auth_error = False
    status = "success"

    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(conn, cfg.creators)
        todays = storage.select_todays_creators(conn, n=10)

    if not todays:
        log.warning("No active creators to scrape.")
        with storage.connect(cfg.db_path) as conn:
            storage.record_run(
                conn,
                run_date=run_date,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc).isoformat(),
                status="failed",
                posts_collected=0,
                creators_scraped=0,
                errors=[{"error": "no_active_creators"}],
            )
        return CollectorResult(0, 0, [{"error": "no_active_creators"}])

    # Sentinel check — if auth_setup.py has never been run, fail clearly.
    sentinel = cfg.chrome_profile_dir / AUTH_SENTINEL
    if not sentinel.exists():
        msg = (
            f"Chrome profile not authenticated — run scripts/auth_setup.py first "
            f"(expected sentinel at {sentinel})"
        )
        log.error(msg)
        errors.append({"error": "not_authenticated", "detail": msg})
        with storage.connect(cfg.db_path) as conn:
            storage.record_run(
                conn,
                run_date=run_date,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc).isoformat(),
                status="auth_error",
                posts_collected=0,
                creators_scraped=0,
                errors=errors,
            )
        return CollectorResult(0, 0, errors, auth_error=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(cfg.chrome_profile_dir),
            headless=cfg.headless,
            viewport=VIEWPORT,
        )
        page = await context.new_page()
        try:
            for row in todays:
                creator_cfg = next(
                    (c for c in cfg.creators if c.linkedin_url == row["linkedin_url"]),
                    None,
                )
                if creator_cfg is None:
                    continue
                creator_id = row["id"]
                try:
                    with storage.connect(cfg.db_path) as conn:
                        inserted, _ = await _scrape_creator(page, creator_cfg, creator_id, conn)
                    posts_collected += inserted
                    creators_scraped += 1
                    log.info("Scraped %s: %d new posts", creator_cfg.display_name, inserted)
                except AuthError as e:
                    log.error("Auth error: %s", e)
                    errors.append({"error": "auth_error", "creator": creator_cfg.linkedin_url, "detail": str(e)})
                    auth_error = True
                    status = "auth_error"
                    break
                except Exception as e:
                    log.exception("Failed to scrape %s", creator_cfg.display_name)
                    errors.append({"error": "scrape_failed", "creator": creator_cfg.linkedin_url, "detail": str(e)})

                await asyncio.sleep(random.uniform(*INTER_CREATOR_SLEEP_RANGE))
        finally:
            await context.close()

    if errors and not auth_error:
        status = "partial"
    if posts_collected == 0 and not auth_error:
        status = "failed"

    with storage.connect(cfg.db_path) as conn:
        storage.record_run(
            conn,
            run_date=run_date,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc).isoformat(),
            status=status,
            posts_collected=posts_collected,
            creators_scraped=creators_scraped,
            errors=errors or None,
        )

    return CollectorResult(posts_collected, creators_scraped, errors, auth_error=auth_error)


async def run_dry_collection(cfg: AppConfig) -> CollectorResult:
    """Bypasses Playwright — exercises DB rotation + insert loop with synthetic posts.

    Intended for CI-free smoke testing of the storage pipeline without a browser.
    """
    storage.init_db(cfg.db_path)
    started_at = datetime.now(timezone.utc).isoformat()
    run_date = datetime.now(timezone.utc).date().isoformat()

    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(conn, cfg.creators)
        todays = storage.select_todays_creators(conn, n=10)

    posts_collected = 0
    creators_scraped = 0
    with storage.connect(cfg.db_path) as conn:
        for idx, row in enumerate(todays):
            fake_urn = f"urn:li:activity:DRY-{row['id']}-{int(datetime.now().timestamp())}-{idx}"
            inserted = storage.insert_post(
                conn,
                post_urn=fake_urn,
                creator_id=row["id"],
                post_url=None,
                post_text=f"[dry-run synthetic post for {row['display_name']}]",
                reactions=random.randint(50, 500),
                comments=random.randint(5, 50),
                reshares=random.randint(0, 20),
                follower_count_at_collection=random.randint(10_000, 200_000),
                post_date=datetime.now(timezone.utc).isoformat(),
                is_repost_with_commentary=False,
            )
            if inserted:
                posts_collected += 1
            storage.mark_creator_scraped(conn, row["id"], follower_count=50_000)
            creators_scraped += 1

        storage.record_run(
            conn,
            run_date=run_date,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc).isoformat(),
            status="success",
            posts_collected=posts_collected,
            creators_scraped=creators_scraped,
            errors=[{"note": "dry_run"}],
        )
    return CollectorResult(posts_collected, creators_scraped, [])
