"""Discord/Telegram adapters. Never log transport exceptions containing secrets."""
import asyncio
import html
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .models import Listing
from .branding import NAME


class DeliveryError(Exception):
    def __init__(self, message: str, *, uncertain: bool = False, retry_after: float = 300):
        super().__init__(message)
        self.uncertain = uncertain
        self.retry_after = retry_after


def env_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Set environment variable {name}")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward credentials or follow unexpected notification endpoints.
        return None


def posted_age_minutes(posted: str) -> float | None:
    """Parse Facebook's English relative times; unknown times never trigger a ping."""
    text = posted.lower().strip()
    if re.search(r"\b(?:just now|moments? ago|less than (?:a|one|1) minute ago)\b", text):
        return 0
    match = re.search(r"\b(\d+|a|an|one)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)\s+ago\b", text)
    if not match:
        return None
    amount = float(match[1]) if match[1].isdigit() else 1
    unit = match[2]
    multiplier = (1 / 60 if unit.startswith("sec") else 1 if unit.startswith("min")
                  else 60 if unit.startswith(("hour", "hr")) else 1440 if unit.startswith("day") else 10080)
    return amount * multiplier


class Notifier:
    def __init__(self, config):
        self.config = config
        self.provider = config.notification.get("provider", "discord")
        if self.provider == "discord":
            webhook = config.notification.get("webhook_url") or env_secret(config.notification.get("webhook_url_env", "DISCORD_WEBHOOK_URL"))
            parts = urlsplit(webhook.strip())
            if (parts.scheme != "https" or parts.netloc not in ("discord.com", "discordapp.com",
                    "canary.discord.com", "ptb.discord.com") or not re.fullmatch(
                        r"/api(?:/v\d+)?/webhooks/\d+/[\w-]+/?", parts.path)):
                raise ValueError("Discord webhook must be a valid Discord HTTPS webhook URL")
            query = dict(parse_qsl(parts.query))
            query["wait"] = "true" # Ask Discord to confirm the message was saved.
            self._url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
            self._chat_id = None
        else:
            token = env_secret(config.notification.get("bot_token_env", "TELEGRAM_BOT_TOKEN"))
            if not re.fullmatch(r"\d+:[\w-]+", token):
                raise ValueError("Telegram bot token has an invalid format")
            self._url = f"https://api.telegram.org/bot{token}/sendMessage"
            self._chat_id = env_secret(config.notification.get("chat_id_env", "TELEGRAM_CHAT_ID"))

    def listing_payload(self, item: Listing) -> dict:
        template = self.config.message_template.replace("```", "'''")
        if self.provider == "discord":
            embed = {"title": item.title[:256], "url": item.url, "color": 0x2ECC71,
                     "fields": [
                         {"name": "Price", "value": f"{item.price} ({self.config.currency})"[:1024], "inline": True},
                         {"name": "Location", "value": item.location[:1024], "inline": True},
                         {"name": "Posted", "value": item.posted[:1024]},
                         {"name": "Copy this message", "value": f"```\n{template}\n```"},
                     ], "footer": {"text": f"{NAME} | Listing {item.id} | Open the link to message the seller"}}
            if item.thumbnail:
                embed["thumbnail"] = {"url": item.thumbnail}
            payload = {"username": NAME, "embeds": [embed], "allowed_mentions": {"parse": []}}
            age = posted_age_minutes(item.posted)
            threshold = self.config.notification.get("recent_ping_minutes", 60)
            # The mention must be in message content, not inside the embed.
            if age is not None and age < threshold:
                payload["content"] = "@everyone New listing posted less than " + f"{threshold:g} minutes ago!"
                payload["allowed_mentions"] = {"parse": ["everyone"]}
            return payload
        e = html.escape
        text = (f"<b>{e(NAME)}</b>\n<b>{e(item.title[:500])}</b>\n{e(item.price)} ({e(self.config.currency)})\n"
                f"Location: {e(item.location[:500])}\nPosted: {e(item.posted[:500])}\n"
                f'<a href="{e(item.url, quote=True)}">Open listing</a>\n'
                f"Copy this message:\n<pre>{e(self.config.message_template)}</pre>\nListing {item.id}")
        if item.thumbnail:
            text += f'\n<a href="{e(item.thumbnail, quote=True)}">Thumbnail</a>'
        return {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": False, "url": item.url}}

    async def send_listing(self, item: Listing):
        await asyncio.to_thread(self._post, self.listing_payload(item))

    async def send_status(self, text: str):
        payload = ({"username": NAME, "content": text[:1900], "allowed_mentions": {"parse": []}}
                   if self.provider == "discord" else {"chat_id": self._chat_id, "text": text[:4000]})
        await asyncio.to_thread(self._post, payload)

    def _post(self, payload: dict):
        request = Request(self._url, data=json.dumps(payload).encode(), method="POST",
                          headers={"Content-Type": "application/json", "User-Agent": "KartiksMarketplaceBot/0.1"})
        try:
            with build_opener(NoRedirect).open(request, timeout=20) as response:
                data = json.loads(response.read())
        except HTTPError as exc:
            if exc.code == 429:
                try:
                    body = json.loads(exc.read())
                    seconds = float(body.get("retry_after") or body.get("parameters", {}).get("retry_after") or 300)
                    seconds = max(120, seconds)
                except (ValueError, TypeError, AttributeError):
                    seconds = 300
                raise DeliveryError("Notification rate limited", retry_after=seconds) from None
            # 5xx responses can be returned after an upstream accepted a message.
            raise DeliveryError(f"Notification HTTP {exc.code}", uncertain=exc.code >= 500) from None
        except (URLError, OSError, ValueError):
            raise DeliveryError("Notification connection/response outcome unknown", uncertain=True) from None
        if not isinstance(data, dict):
            raise DeliveryError("Notification acknowledgement invalid", uncertain=True)
        if self.provider == "telegram" and not data.get("ok"):
            raise DeliveryError("Telegram rejected notification")
        if self.provider == "discord" and not data.get("id"):
            raise DeliveryError("Discord acknowledgement missing", uncertain=True)
