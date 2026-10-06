"""Pure parsing helpers; DOM extraction is isolated in browser.py for maintenance."""
import re
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from .models import Listing

PRICE = re.compile(r"^(?:(?:NZ|AU|US|CA)?\$|NZD\s*|AUD\s*|USD\s*)\s*(\d[\d,]*(?:\.\d{1,2})?)(?:\s|$)", re.I)
POSTED = re.compile(r"(?:Listed|Posted)\s+(.+?)(?:\s+in\s+(.+))?$", re.I)


def parse_price(text: str) -> float | None:
    text = text.strip()
    if text.casefold() == "free":
        return 0.0
    match = PRICE.match(text)
    return float(match[1].replace(",", "")) if match else None


def listing_id(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.netloc and parts.netloc not in ("www.facebook.com", "facebook.com", "m.facebook.com"):
        return None
    match = re.fullmatch(r"/marketplace/item/(\d+)/?", parts.path)
    return match[1] if match else None


def parse_card(raw: dict) -> Listing | None:
    item_id = listing_id(raw.get("href", ""))
    if not item_id:
        return None
    lines = [s.strip() for s in raw.get("text", "").splitlines() if s.strip()]
    price_index = next((i for i, s in enumerate(lines) if parse_price(s) is not None), None)
    price = lines[price_index] if price_index is not None else "Not shown"
    candidates = [s for i, s in enumerate(lines) if i != price_index and parse_price(s) is None
                  and s.casefold() not in {"save", "sponsored", "pending", "sold", "new"}]
    posted = next((s for s in candidates if POSTED.search(s)), "Not shown by Facebook")
    candidates = [s for s in candidates if s != posted]
    title = candidates[0] if candidates else raw.get("alt", "").strip()
    if not title:
        return None
    thumbnail = raw.get("thumbnail") or None
    if thumbnail and urlsplit(thumbnail).scheme != "https":
        thumbnail = None
    return Listing(item_id, title[:500], price, parse_price(price),
                   candidates[-1] if len(candidates) > 1 else "Not shown",
                   f"https://www.facebook.com/marketplace/item/{item_id}/", thumbnail, posted)


def matches(listing: Listing, config) -> bool:
    return rejection_reason(listing, config) is None


def rejection_reason(listing: Listing, config) -> str | None:
    """Explain the pre-AI filter without guessing why Facebook returned a card."""
    # The visible currency is assumed to match the account's configured currency.
    if listing.price_amount is None:
        if not config.poll.get("alert_unknown_price", False):
            return "unknown price"
    elif listing.price_amount > config.max_price:
        return "over maximum price"
    if config.poll.get("require_all_query_words", True):
        title_words = re.findall(r"\w+", listing.title.casefold())
        fuzzy = config.poll.get("fuzzy_titles", False)
        for name in [config.search_query, *config.aliases]:
            wanted = re.findall(r"\w+", name.casefold())
            # Ignore spacing/punctuation differences (PS 5, series-s, quest3).
            compact = "".join(wanted)
            if any("".join(title_words[start:start + count]) == compact
                   for start in range(len(title_words)) for count in range(1, len(wanted) + 2)):
                return None
            # Short model identifiers stay exact: S must not become X, 3 not 2.
            if all(any(word == candidate or (fuzzy and len(word) >= 4 and len(candidate) >= 4
                       and re.findall(r"\d+", word) == re.findall(r"\d+", candidate)
                       and SequenceMatcher(None, word, candidate).ratio() >= 0.8)
                       for candidate in title_words) for word in wanted):
                return None
        return "title does not match query or aliases"
    return None
