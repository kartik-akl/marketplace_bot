"""Poll loop, durable notifications, and user-driven recovery."""
import asyncio
from contextlib import contextmanager
from collections import Counter
import json
import logging
import os
from pathlib import Path
import random

from playwright.async_api import Error as PlaywrightError

from .ai_filter import AIError, AIListingFilter
from .browser import Browser, HumanRequired, LayoutError
from .config import facebook_search_url
from .notifications import DeliveryError, Notifier
from .parsing import matches, rejection_reason
from .storage import Store
from .branding import NAME

log = logging.getLogger(__name__)


@contextmanager
def single_instance(database_path: Path):
    """OS lock is released on crash. Keep one owner of the profile and delivery queue."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with database_path.with_suffix(".lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Another bot command is using this database/profile") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


async def manual_input(prompt: str):
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        raise ValueError("Manual action needs an interactive terminal. Run setup, then restart run.") from None


async def setup(config):
    browser = Browser(config)
    entries = setup_entries(config)
    searches = config.search_configs()
    try:
        await browser.open(headed=True)
        for index, search in enumerate(searches, start=1):
            # Login is expected here, so navigate without gate rejection.
            await browser.page.goto(search.build_url(), wait_until="domcontentloaded")
            print(f"Search {index}/{len(searches)}: log in if needed, then verify these visible filters:\n"
                  f"  Query: {search.search_query}\n  Max price: {search.max_price:g} {search.currency}\n"
                  f"  Location: {search.location}\n  Radius: {search.radius_km:g} km\n"
                  "  Sort: Date listed / newest first\n"
                  "Use Facebook's Location/Filters controls if URL parameters were ignored.\n"
                  "Keep the browser on this item's filtered SEARCH results page.")
            while True:
                answer = (await manual_input("Type READY after verifying all filters, or QUIT: ")).strip().upper()
                if answer == "QUIT":
                    return
                if answer == "READY":
                    break
                print("This search has not been saved. Ctrl+C to exit.")
            await browser.check_gate()
            entries[search.fingerprint()] = facebook_search_url(browser.page.url)
            path = config.path("setup_state_path")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"searches": entries}, indent=2), encoding="utf-8")
            temporary.replace(path)
            print(f"Verified search saved: {search.search_query}")
        print("Session and all verified search URLs saved. Run the bot with: marketplace-bot run")
    finally:
        await browser.close()


def setup_entries(config) -> dict:
    """Read per-search verification, including the original single-search format."""
    try:
        state = json.loads(config.path("setup_state_path").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    if isinstance(state.get("searches"), dict):
        return state["searches"]
    if isinstance(state.get("fingerprint"), str) and isinstance(state.get("url"), str):
        return {state["fingerprint"]: state["url"]}
    return {}


def verified_url(config) -> str:
    url = setup_entries(config).get(config.fingerprint())
    if not isinstance(url, str):
        raise ValueError(f"Search '{config.search_query}' needs verification. Run 'marketplace-bot setup'")
    return facebook_search_url(url)


async def recover(browser, notifier, reason: str, search_url: str):
    log.warning("Monitoring paused: %s", reason)
    try:
        await notifier.send_status(
            f"{NAME} paused: {reason}. On the bot computer, resolve the issue in the visible "
            "browser, then press Enter in the bot terminal. No automated refreshes while paused.")
    except DeliveryError as exc:
        log.error("Pause alert could not be confirmed: %s. Check the bot terminal.", exc)
    challenge_url = browser.page.url if browser.page else search_url
    # Close headless before reusing the profile. No concurrent profile access.
    await browser.open(headed=True)
    try:
        await browser.page.goto(challenge_url, wait_until="domcontentloaded")
    except PlaywrightError:
        log.warning("Recovery page did not finish loading; inspect it manually.")
    while True:
        await manual_input("Resolve login/CAPTCHA/access issue in the browser; press Enter to resume: ")
        try:
            await browser.check_gate() # DOM only: no network polling in this loop.
            break
        except HumanRequired as exc:
            log.warning("Still paused: %s", exc)
    await browser.open(headed=False)
    log.info("Manual recovery complete; monitoring will resume.")


async def deliver_pending(store, notifier, browser, config, ai_filter=None):
    ai_filter = ai_filter or AIListingFilter(config)
    for item, enriched in store.pending(int(config.poll.get("max_new_per_poll", 5)), config.fingerprint()):
        if not matches(item, config):
            store.finish(item.id, "suppressed")
            continue
        if not enriched:
            item = await browser.detail(item)
            store.enrich(item)
        try:
            verdict = await ai_filter.check(item, config)
        except AIError as exc:
            log.error("Listing %s: %s; AI screening deferred", item.id, exc)
            return exc.retry_after
        if not verdict.is_match:
            store.finish(item.id, "suppressed", f"AI filter: {verdict.reason}")
            log.info("Listing suppressed by AI filter: %s | %s", item.id, verdict.reason)
            continue
        if not store.claim(item.id):
            continue
        try:
            await notifier.send_listing(item)
        except DeliveryError as exc:
            state = "uncertain" if exc.uncertain else "pending"
            store.finish(item.id, state, str(exc), exc.retry_after)
            log.error("Listing %s: %s (%s)", item.id, exc, state)
            # One failure ends the batch; no immediate retries or flood of requests.
            return exc.retry_after
        else:
            store.finish(item.id, "sent")
            log.info("Alert sent: %s | %s", item.id, item.title)
        await asyncio.sleep(1.1) # Telegram free-tier per-chat pacing.
    return 0


async def run(config, once: bool = False):
    # Validate EVERY search before opening Facebook or sending notifications.
    searches = [(search, verified_url(search)) for search in config.search_configs()]
    notifier = Notifier(config) # Validate environment secrets before opening browser.
    ai_filter = AIListingFilter(config) # Validate AI key before opening browser when enabled.
    store = Store(config.path("database_path"))
    browser = Browser(config)
    failures = {search.fingerprint(): 0 for search, _ in searches}
    search_index = 0
    notification_cooldown = 0
    try:
        if any(row["state"] == "uncertain" for row in store.status()):
            log.warning("Some deliveries are uncertain. Inspect 'marketplace-bot status'; they will not be resent.")
        await browser.open()
        log.info("Monitoring %s searches with one search per randomized polling interval", len(searches))
        while True:
            search, url = searches[search_index]
            fingerprint = search.fingerprint()
            delay_override = 0
            try:
                listings = await browser.scan(url)
                filtered = [item for item in listings if matches(item, search)]
                seed = not store.initialized(fingerprint) and not config.poll.get("alert_on_first_run", True)
                store.discover(filtered, fingerprint, seed=seed)
                log.info("Search '%s': scanned %s cards; %s match title/price filters%s",
                         search.search_query, len(listings), len(filtered),
                         " (initial results seeded silently)" if seed else "")
                if listings and not filtered:
                    reasons = Counter(rejection_reason(item, search) for item in listings)
                    log.warning("Search '%s': rejected cards by reason: %s", search.search_query, dict(reasons))
                    for item in listings[:5]:
                        log.info("Rejected sample: title=%r price=%r reason=%s",
                                 item.title, item.price, rejection_reason(item, search))
                if asyncio.get_running_loop().time() >= notification_cooldown:
                    delay_override = await deliver_pending(store, notifier, browser, search, ai_filter)
                    notification_cooldown = asyncio.get_running_loop().time() + delay_override
                failures[fingerprint] = 0
            except HumanRequired as exc:
                await recover(browser, notifier, str(exc), url)
                continue
            except (PlaywrightError, LayoutError) as exc:
                if once:
                    raise ValueError("One-shot scan failed; inspect the visible page with setup") from None
                failures[fingerprint] += 1
                # Browser exceptions can contain page details. Log only their type.
                log.warning("Search '%s' failed (%s), consecutive failures: %s",
                            search.search_query, type(exc).__name__, failures[fingerprint])
                delay_override = min(3600, 300 * 2 ** min(failures[fingerprint] - 1, 4))
                if failures[fingerprint] >= 3:
                    await recover(browser, notifier, "Repeated load/extraction failure; inspect the search page", url)
                    failures[fingerprint] = 0
                    continue
            search_index += 1
            if once and search_index == len(searches):
                break
            search_index %= len(searches)
            delay = max(delay_override, random.uniform(config.poll.get("min_seconds", 120),
                                                       config.poll.get("max_seconds", 300)))
            log.info("Next scan in %.0f seconds", delay)
            await asyncio.sleep(delay)
    finally:
        try:
            await browser.close()
        finally:
            store.close()
