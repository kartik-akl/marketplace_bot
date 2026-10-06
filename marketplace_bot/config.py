"""Validated YAML settings; paths are relative to the config file."""
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml


def facebook_search_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "www.facebook.com"
            or not re.fullmatch(r"/marketplace/(?:[\w-]+/)?search/?", parts.path)):
        raise ValueError("search_url must be an HTTPS www.facebook.com Marketplace search URL")
    return url


@dataclass(frozen=True)
class Config:
    search_query: str
    max_price: float
    location: str
    radius_km: float
    location_slug: str = "auckland"
    currency: str = "NZD"
    search_url: str | None = None
    listing_description: str = ""
    aliases: list[str] = field(default_factory=list)
    message_template: str = "Kia ora! Is this still available? I can pick up today with cash."
    notification: dict = field(default_factory=dict, repr=False)
    ai: dict = field(default_factory=dict, repr=False)
    poll: dict = field(default_factory=dict)
    browser: dict = field(default_factory=dict)
    base_dir: Path = Path(".")
    searches: tuple[dict, ...] = ()

    def search_configs(self) -> tuple["Config", ...]:
        """Expand search entries while sharing browser, notification, and DB settings."""
        if not self.searches:
            return (self,)
        configs = []
        for entry in self.searches:
            overrides = {key: value for key, value in entry.items() if key not in ("query", "description")}
            if "description" in entry:
                overrides["listing_description"] = entry["description"]
            configs.append(replace(self, searches=(), search_query=entry["query"], **overrides))
        return tuple(configs)

    def path(self, key: str) -> Path:
        defaults = {"profile_dir": "data/browser-profile", "database_path": "data/listings.sqlite3",
                    "setup_state_path": "data/search-state.json"}
        return (self.base_dir / self.browser.get(key, defaults[key])).resolve()

    def fingerprint(self) -> str:
        # Any search-filter change requires another visible filter verification.
        filters = [self.search_query, self.max_price, self.location, self.radius_km,
                   self.location_slug, self.search_url, self.currency]
        return sha256(json.dumps(filters).encode()).hexdigest()

    def build_url(self) -> str:
        base = self.search_url or f"https://www.facebook.com/marketplace/{self.location_slug}/search/"
        parts = urlsplit(facebook_search_url(base))
        params = dict(parse_qsl(parts.query))
        # Facebook's URL parameters are unofficial. `setup` verifies the actual
        # visible filters and captures the resulting account-specific URL.
        params.update(query=self.search_query, maxPrice=f"{self.max_price:g}",
                      radius=f"{self.radius_km:g}", sortBy="creation_time_descend",
                      exact="true")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))


def number(value, name: str, minimum: float, maximum: float = math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        # YAML errors can include source lines containing inadvertently pasted secrets.
        raise ValueError("Invalid YAML in config file") from None
    if not isinstance(raw, dict):
        raise ValueError("config must be a YAML mapping")
    allowed = set(Config.__dataclass_fields__) - {"base_dir"}
    if set(raw) - allowed:
        raise ValueError("Unknown top-level config keys")
    if "searches" in raw and "search_query" in raw:
        raise ValueError("Use either searches or search_query, not both")
    if "searches" in raw:
        entries = raw["searches"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("searches must be a nonempty list of queries or search mappings")
    else:
        query = raw.get("search_query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Set searches or a nonempty search_query")
        # Keep old single-query configs working; a comma-separated string is also
        # accepted as a quick way to monitor several independent items.
        entries = query.split(",")
    normalized = []
    search_keys = {"query", "description", "aliases", "max_price", "location", "radius_km", "location_slug", "search_url"}
    for entry in entries:
        entry = {"query": entry} if isinstance(entry, str) else entry
        if not isinstance(entry, dict) or set(entry) - search_keys:
            raise ValueError("Each search must be text or a mapping with supported search filters")
        entry = dict(entry)
        if not isinstance(entry.get("query"), str) or not entry["query"].strip():
            raise ValueError("Each search query must be nonempty text")
        entry["query"] = entry["query"].strip()
        if "aliases" in entry:
            validate_aliases(entry["aliases"])
        if "max_price" in entry:
            entry["max_price"] = number(entry["max_price"], "search.max_price", 0)
        if "radius_km" in entry:
            entry["radius_km"] = number(entry["radius_km"], "search.radius_km", 1, 500)
        if "location" in entry and (not isinstance(entry["location"], str) or not entry["location"].strip()):
            raise ValueError("search.location must be nonempty text")
        if "location_slug" in entry and (not isinstance(entry["location_slug"], str)
                or not re.fullmatch(r"[\w-]+", entry["location_slug"])):
            raise ValueError("search.location_slug must be a Marketplace city slug or numeric ID")
        if entry.get("search_url") is not None:
            if not isinstance(entry["search_url"], str):
                raise ValueError("search.search_url must be a Marketplace search URL")
            facebook_search_url(entry["search_url"])
        if "description" in entry and (not isinstance(entry["description"], str)
                or not entry["description"].strip() or len(entry["description"]) > 1200):
            raise ValueError("search.description must be nonempty text of at most 1200 characters")
        normalized.append(entry)
    raw["search_query"] = normalized[0]["query"]
    raw["searches"] = tuple(normalized)
    validate_aliases(raw.get("aliases", []))
    for key in ("search_query", "location"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"{key} must be nonempty text")
    raw["max_price"] = number(raw.get("max_price"), "max_price", 0)
    raw["radius_km"] = number(raw.get("radius_km"), "radius_km", 1, 500)
    slug = raw.get("location_slug", "auckland")
    if not isinstance(slug, str) or not re.fullmatch(r"[\w-]+", slug):
        raise ValueError("location_slug must be a Marketplace city slug or numeric ID")
    if raw.get("search_url"):
        facebook_search_url(raw["search_url"])
    for key in ("message_template", "currency"):
        if key in raw and (not isinstance(raw[key], str) or not raw[key] or len(raw[key]) > 800):
            raise ValueError(f"{key} must be nonempty text of at most 800 characters")
    if "listing_description" in raw and (not isinstance(raw["listing_description"], str)
            or len(raw["listing_description"]) > 1200):
        raise ValueError("listing_description must be text of at most 1200 characters")
    for section in ("notification", "ai", "poll", "browser"):
        if not isinstance(raw.get(section, {}), dict):
            raise ValueError(f"{section} must be a mapping")
    notification = raw.get("notification", {})
    if set(notification) - {"provider", "webhook_url", "webhook_url_env", "bot_token_env", "chat_id_env", "recent_ping_minutes"}:
        raise ValueError("Unknown notification config key")
    if "webhook_url" in notification and (not isinstance(notification["webhook_url"], str) or not notification["webhook_url"].strip()):
        raise ValueError("notification.webhook_url must be nonempty text")
    if notification.get("provider", "discord") not in ("discord", "telegram"):
        raise ValueError("notification.provider must be discord or telegram")
    number(notification.get("recent_ping_minutes", 60), "notification.recent_ping_minutes", 0, 1440)
    for key, value in notification.items():
        if key.endswith("_env") and (not isinstance(value, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value)):
            raise ValueError(f"notification.{key} must be an environment variable name")
    ai = raw.get("ai", {})
    if set(ai) - {"enabled", "api_key", "api_key_env", "endpoint", "model", "timeout_seconds"}:
        raise ValueError("Unknown ai config key")
    if "api_key" in ai and (not isinstance(ai["api_key"], str) or not ai["api_key"].strip()):
        raise ValueError("ai.api_key must be nonempty text")
    if "enabled" in ai and not isinstance(ai["enabled"], bool):
        raise ValueError("ai.enabled must be true or false")
    if "api_key_env" in ai and (not isinstance(ai["api_key_env"], str)
            or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", ai["api_key_env"])):
        raise ValueError("ai.api_key_env must be an environment variable name")
    if "endpoint" in ai:
        parts = urlsplit(ai["endpoint"])
        if parts.scheme != "https" or not parts.netloc:
            raise ValueError("ai.endpoint must be an HTTPS URL")
    if "model" in ai and (not isinstance(ai["model"], str) or not ai["model"] or len(ai["model"]) > 100):
        raise ValueError("ai.model must be nonempty text")
    if "timeout_seconds" in ai:
        number(ai["timeout_seconds"], "ai.timeout_seconds", 5, 120)
    poll = raw.get("poll", {})
    for low, high, default_low, default_high, floor in (
        ("min_seconds", "max_seconds", 120, 300, 120),
        ("detail_delay_min_seconds", "detail_delay_max_seconds", 4, 8, 2),
    ):
        a = number(poll.get(low, default_low), f"poll.{low}", floor)
        b = number(poll.get(high, default_high), f"poll.{high}", floor)
        if a > b:
            raise ValueError(f"poll.{low} must not exceed {high}")
    for key, default, maximum in (("max_cards", 60, 200), ("max_new_per_poll", 5, 20)):
        n = number(poll.get(key, default), f"poll.{key}", 1, maximum)
        if not n.is_integer():
            raise ValueError(f"poll.{key} must be an integer")
    for key in ("alert_on_first_run", "require_all_query_words", "alert_unknown_price", "fuzzy_titles"):
        if key in poll and not isinstance(poll[key], bool):
            raise ValueError(f"poll.{key} must be true or false")
    browser = raw.get("browser", {})
    if browser.get("engine", "chromium") not in ("chromium", "firefox"):
        raise ValueError("browser.engine must be chromium or firefox")
    number(browser.get("timeout_seconds", 30), "browser.timeout_seconds", 5, 120)
    if browser.get("channel") not in (None, "chrome", "msedge", "chromium"):
        raise ValueError("browser.channel must be null, chromium, chrome, or msedge")
    for key in ("profile_dir", "database_path", "setup_state_path", "locale"):
        if key in browser and (not isinstance(browser[key], str) or not browser[key]):
            raise ValueError(f"browser.{key} must be nonempty text")
    config = Config(**raw, base_dir=path.resolve().parent)
    fingerprints = [search.fingerprint() for search in config.search_configs()]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("Duplicate searches with the same query and filters")
    return config


def validate_aliases(value):
    if (not isinstance(value, list) or len(value) > 30
            or any(not isinstance(alias, str) or not alias.strip() or len(alias) > 100 for alias in value)):
        raise ValueError("aliases must be a list of up to 30 nonempty names of at most 100 characters")
