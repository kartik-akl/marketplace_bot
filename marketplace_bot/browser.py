"""Visible DOM only: no private API calls or attempts to bypass challenges."""
import asyncio
from dataclasses import replace
import random
import re

from playwright.async_api import async_playwright, Error as PlaywrightError

from .parsing import POSTED, parse_card


class HumanRequired(Exception):
    pass


class LayoutError(Exception):
    pass


# Facebook changes markup regularly. Keep selectors and heuristics in one place.
CARD_SELECTOR = 'a[href*="/marketplace/item/"]'
CARD_SCRIPT = r"""(limit) => {
  const root = document.querySelector('[role="main"]') || document.body;
  const items = new Map();
  const boundary = [...root.querySelectorAll('h2,h3,[role="heading"]')].find(n =>
    /outside your search|outside your area|related listings|more picks|results from outside/i.test(n.innerText));
  for (const anchor of root.querySelectorAll('a[href*="/marketplace/item/"]')) {
    if (boundary && (boundary.compareDocumentPosition(anchor) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
    const match = anchor.href.match(/\/marketplace\/item\/(\d+)/);
    if (!match) continue;
    let card = anchor;
    // An image and a text link can be siblings. Walk only within this one item.
    for (let n = 0; n < 4 && card.parentElement; n++) {
      if (card.innerText.trim().split('\n').length >= 3) break;
      const parent = card.parentElement;
      const ids = new Set([...parent.querySelectorAll('a[href*="/marketplace/item/"]')]
        .map(a => (a.href.match(/\/item\/(\d+)/) || [])[1]).filter(Boolean));
      if (ids.size !== 1 || !ids.has(match[1]) || parent === root) break;
      card = parent;
    }
    if (/\bSponsored\b/i.test(card.innerText)) continue;
    const img = card.querySelector('img');
    const row = {href: anchor.href, text: card.innerText,
      alt: img?.alt || '', thumbnail: img?.currentSrc || img?.src || null};
    const old = items.get(match[1]);
    if (!old || row.text.length > old.text.length) items.set(match[1], row);
    if (items.size >= limit) break;
  }
  return [...items.values()];
}"""

GATE_SCRIPT = r"""() => {
  const visible = n => n && n.getClientRects().length > 0;
  if ([...document.querySelectorAll('input[type="password"],input[name="email"]')].some(visible))
    return 'Facebook login required';
  if ([...document.querySelectorAll('input[name="approvals_code"],input[autocomplete="one-time-code"]')].some(visible))
    return 'Facebook login verification required';
  if ([...document.querySelectorAll('iframe')].some(n => visible(n) && /captcha|recaptcha|hcaptcha/i.test(n.src)))
    return 'CAPTCHA requires manual completion';
  const root = document.querySelector('[role="main"]') || document.body;
  const headings = [...document.querySelectorAll('h1,h2,[role="heading"],[role="dialog"]')]
    .filter(visible).map(n => n.innerText).join('\n');
  const text = root.innerText.slice(0, 20000);
  if (/confirm you.re human|security check|enter the characters|complete the captcha|verify you are human|help us confirm|confirm your identity|verify your identity|enter security code|two.factor authentication|log (?:in|into) (?:to )?facebook/i.test(headings))
    return 'Facebook security challenge';
  if (/temporarily blocked|you.re temporarily blocked|going too fast|account has been locked|marketplace isn.t available to you|you can.t use this feature right now/i.test(text))
    return 'Facebook access restricted';
  return null;
}"""


class Browser:
    def __init__(self, config):
        self.config = config
        self.playwright = None
        self.context = None
        self.page = None

    async def open(self, headed: bool = False):
        await self.close()
        self.playwright = await async_playwright().start()
        try:
            engine = self.config.browser.get("engine", "chromium")
            options = ({"channel": self.config.browser.get("channel"), "args": ["--disable-quic"]}
                       if engine == "chromium" else {})
            self.context = await getattr(self.playwright, engine).launch_persistent_context(
                user_data_dir=str(self.config.path("profile_dir")), headless=not headed,
                locale=self.config.browser.get("locale", "en-NZ"),
                viewport={"width": 1440, "height": 1100}, accept_downloads=False,
                **options,
            )
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            for extra in self.context.pages[1:]:
                await extra.close()
            self.page.set_default_timeout(self.config.browser.get("timeout_seconds", 30) * 1000)
        except PlaywrightError as exc:
            await self.close()
            # Report known launch failures without dumping private profile paths.
            if "spawn UNKNOWN" in str(exc):
                raise ValueError(f"{engine} could not start (spawn UNKNOWN). On Windows, run the browser executable directly to check for missing runtime or side-by-side configuration errors.") from None
            if "Executable doesn't exist" in str(exc):
                raise ValueError(f"Install the browser with: .\\.venv\\Scripts\\python.exe -m playwright install {engine}") from None
            raise
        except BaseException:
            await self.close()
            raise

    async def close(self):
        try:
            if self.context:
                await self.context.close()
        finally:
            self.context = None
            self.page = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None

    async def check_gate(self):
        if re.search(r"/(?:login|checkpoint|challenge|captcha|recover)(?:/|\?|$)", self.page.url):
            raise HumanRequired("Facebook login or security challenge")
        reason = await self.page.evaluate(GATE_SCRIPT)
        if reason:
            raise HumanRequired(reason)

    async def navigate(self, url: str):
        response = await self.page.goto(url, wait_until="domcontentloaded")
        if response and response.status in (401, 403, 429):
            raise HumanRequired(f"Facebook returned HTTP {response.status}")
        # Brief rendering wait, with no refreshes or retries of the document.
        await self.page.wait_for_timeout(2000)
        await self.check_gate()
        if response and response.status >= 500:
            raise LayoutError("Facebook server unavailable")

    async def scan(self, url: str):
        await self.navigate(url)
        try:
            await self.page.wait_for_function(r"""() =>
                !!document.querySelector('a[href*="/marketplace/item/"]') ||
                /no listings found|no results found|no results for/i.test(document.body.innerText) ||
                !!document.querySelector('input[type="password"]')""", timeout=15000)
        except Exception:
            await self.check_gate()
            raise LayoutError("No recognizable results or empty-search message") from None
        await self.check_gate()
        # Two bounded scrolls load a small result window, never infinite scrolling.
        raw_by_id = {}
        limit = int(self.config.poll.get("max_cards", 60))
        for step in range(3):
            rows = await self.page.evaluate(CARD_SCRIPT, limit)
            for raw in rows:
                item = parse_card(raw)
                if item:
                    raw_by_id[item.id] = item
            if len(raw_by_id) >= limit or not rows:
                break
            if step < 2:
                await self.page.mouse.wheel(0, 1000)
                await self.page.wait_for_timeout(2000)
                await self.check_gate()
        if not raw_by_id:
            text = await self.page.locator("body").inner_text()
            if not re.search(r"no listings found|no results found|no results for", text, re.I):
                raise LayoutError("Listing cards could not be parsed; selectors may need an update")
        return list(raw_by_id.values())[:limit]

    async def detail(self, item):
        # One navigation per unseen item, bounded by max_new_per_poll.
        await asyncio.sleep(random.uniform(
            self.config.poll.get("detail_delay_min_seconds", 4),
            self.config.poll.get("detail_delay_max_seconds", 8)))
        await self.navigate(item.url)
        root = self.page.locator('[role="main"]')
        if not await root.count():
            root = self.page.locator("body")
        text = await root.first.inner_text()
        await self.check_gate()
        updated = replace(item, description=text[:6000])
        for line in text.splitlines():
            match = POSTED.search(line.strip())
            if match:
                return replace(updated, posted=line.strip()[:500], location=(match[2] or item.location)[:500])
        # Facebook sometimes hides the time. Never fabricate an exact timestamp.
        return updated
