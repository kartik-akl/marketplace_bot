"""Optional AI relevance filter for Marketplace listings."""
import asyncio
from dataclasses import dataclass
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .models import Listing


class AIError(Exception):
    def __init__(self, message: str, retry_after: float = 300):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class AIVerdict:
    is_match: bool
    reason: str


def _env_secret(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"Set environment variable {name} before running with ai.enabled")
    return value


class AIListingFilter:
    def __init__(self, config):
        ai = config.ai
        self.enabled = bool(ai.get("enabled", False))
        self.config = config
        self.model = ai.get("model", "gemini-3.1-flash-lite")
        self.endpoint = ai.get("endpoint", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        self.timeout = float(ai.get("timeout_seconds", 30))
        self.api_key = (ai.get("api_key") or _env_secret(ai.get("api_key_env", "GEMINI_API_KEY"))).strip() if self.enabled else ""

    async def check(self, item: Listing, config) -> AIVerdict:
        if not self.enabled:
            return AIVerdict(True, "AI filter disabled")
        return await asyncio.to_thread(self._check_sync, item, config)

    def _check_sync(self, item: Listing, config) -> AIVerdict:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": (
                    "You are screening Facebook Marketplace listings for a buyer. "
                    "Return only JSON with keys is_match boolean and reason string. "
                    "is_match must be true only when the listing appears to sell the requested item. "
                    "Return false for unrelated products, accessories only, wanted/buying/looking-for posts, "
                    "repair services, scams, rentals, or vague posts that do not clearly include the target."
                )},
                {"role": "user", "content": json.dumps({
                    "search_query": config.search_query,
                    "wanted_description": config.listing_description,
                    "max_price": config.max_price,
                    "currency": config.currency,
                    "listing": {
                        "title": item.title,
                        "price": item.price,
                        "location": item.location,
                        "posted": item.posted,
                        "url": item.url,
                        "visible_text": item.description[:6000],
                    },
                }, ensure_ascii=True)},
            ],
        }
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener().open(request, timeout=self.timeout) as response:
                body = response.read()
        except HTTPError as exc:
            retry = 300
            if exc.code == 429:
                retry = 600
            raise AIError(f"AI filter HTTP {exc.code}", retry_after=retry) from None
        except (URLError, TimeoutError):
            raise AIError("AI filter connection/response failed") from None

        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
            verdict = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise AIError("AI filter returned an unreadable response") from None
        return AIVerdict(bool(verdict.get("is_match")), str(verdict.get("reason", ""))[:300])
