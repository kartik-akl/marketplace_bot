# Kartik's Marketplace Bot

Bot made to help you find items on marketplace :)\

New here? Follow [the step-by-step setup guide](setup_instructions.md).
Start with `config.example.yaml`; keep your own credentials in `config.yaml`.

Python 3.11+ / Playwright / SQLite / Discord or Telegram. Monitors multiple searches,
sends phone alerts, and leaves all seller messaging to you.

## Install and start (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item config.example.yaml config.yaml
```

Python 3.12 and later also work. If the project already has a `.venv`, reuse it.
Edit [config.yaml](config.yaml) to add/remove items and set maximum prices, city, and radius.
The example uses Microsoft Edge and searches for PS5 and Xbox Series S within
35 km of Auckland. Edit each item's price limit and add the items you want.
Skip the copy command if you already have your own configured file.

Use a list for items that share the global `max_price`, `location`, and `radius_km`:

```yaml
searches:
  - "PS5"
  - "Xbox Series S"
  - "Meta Quest 3"
max_price: 300
location: "Auckland, New Zealand"
radius_km: 20
```

For different filters per item, mix text entries with mappings:

```yaml
searches:
  - query: "PS5"
    max_price: 500
  - query: "Xbox Series S"
    max_price: 250
  - query: "Meta Quest 3"
    max_price: 400
    radius_km: 40
```

Supported per-item overrides are `max_price`, `location`, `radius_km`,
`location_slug`, and `search_url`. Omitted values inherit the shared defaults.
All searches share the notification service, currency, message template, browser
profile, and database. Do not set both `searches` and `search_query`.
Old single-item configs still work. A quick alternative is
`search_query: "PS5, Xbox Series S, Meta Quest 3"`, which splits at commas;
use the list format when a query itself contains a comma.

Set `notification.webhook_url` in `config.yaml` to the webhook you create in
Discord's channel integrations. Enable notifications
for that channel in the Discord phone app.

```powershell
.\.venv\Scripts\marketplace-bot.exe test-notification
.\.venv\Scripts\marketplace-bot.exe test-notification --recent
.\.venv\Scripts\marketplace-bot.exe setup
.\.venv\Scripts\marketplace-bot.exe run
```

`setup` opens a visible browser and walks through every configured item. Log into
Facebook once, then verify each query, price, location, radius, and
**Date listed / newest first** in the actual Marketplace controls. Change any ignored
filters using Facebook's UI. Keep the browser on that item's filtered search
results and type `READY` in the terminal to move to the next item. Verified searches
are saved individually, even if you quit partway through. `run` requires every
configured search to be verified before it opens the browser.
The bot saves each search URL and the separate browser profile. `run` uses this
profile headlessly. Login is normally needed only once, until Facebook expires
the session or requires another check. Do not use your everyday Chrome profile.

Facebook's search URL parameters are unofficial and can be ignored. The visible
setup verification is required for this reason; URL parameters alone do not
prove that the location/radius/sort were applied. Changing search settings in YAML
requires another `setup`; adding an item also requires its filters to be verified.
Removing or reordering verified items needs no new verification. The original
single-search verification file is still supported. For another city, also change `location_slug` to its
Marketplace route, or set `search_url` to a search URL from that city. Paths are
relative to the config file, so use `--config C:\path\config.yaml` before a command
if you run from a different directory.

For one pass through all configured items (with the normal pause between searches):

```powershell
.\.venv\Scripts\marketplace-bot.exe run --once
```

## Telegram instead

Change `notification.provider` to `telegram`. Create a bot using Telegram's
BotFather, start a conversation with it, and obtain your chat ID through the
Bot API's `getUpdates` method (or your existing Telegram tooling).

```powershell
$env:TELEGRAM_BOT_TOKEN = '<your bot token>'
$env:TELEGRAM_CHAT_ID = '<your chat ID>'
.\.venv\Scripts\marketplace-bot.exe test-notification
```

Both adapters include title, price, location, posted time when visible, thumbnail
(embedded in Discord, linked in Telegram), and a direct listing link. The
`message_template` is formatted for copying. The bot does not send seller messages.
Config credentials take priority over optional environment references. `config.yaml`
is ignored by Git because it contains credentials. `.env` files are not loaded.
Keep your config private and do not commit the session profile. Restrict access to `data/`, which contains your login
session and listing history.

## Optional AI screening

Set `ai.enabled: true` to ask an AI model to confirm that each potential listing is
actually the item you want before the phone alert is sent. The default config uses
Google's OpenAI-compatible Gemini endpoint with `gemini-3.1-flash-lite`.

Create a Gemini API key in Google AI Studio, then set `ai.api_key` in `config.yaml`
before starting the bot:

```powershell
.\.venv\Scripts\marketplace-bot.exe run
```

Describe what should count as a good listing in `listing_description`, or override
it per item:

```yaml
searches:
  - query: "ps5"
    description: "A Sony PlayStation 5 console for sale. Reject wanted posts, accessories only, repairs, and unrelated items."

ai:
  enabled: true
  api_key: "<your Gemini API key>"
  model: "gemini-3.1-flash-lite"
  endpoint: "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
```

If the AI API is temporarily unavailable or rate-limited, the bot leaves the listing
pending and tries later instead of sending an unverified alert.

## Polling and manual recovery

- The bot rotates through the items in config order using one browser. Each scan
  finishes, then waits a uniformly randomized 120–300 seconds before the next
  item's search. Adding searches keeps the total search request cadence the same.
  With three items, each is revisited roughly every 6–15 minutes, plus scan time.
- One search navigation, at most two short scrolls, and at most five new-listing
  detail navigations per cycle by default. Details are spaced 4–8 seconds apart.
  These limits apply to the current item's scan. There is no infinite scroll,
  automatic messaging, or challenge bypass.
- New candidates are queued durably in SQLite. Each is alerted immediately after
  its detail page is checked. A backlog is drained over subsequent polls.
- Login/CAPTCHA/checkpoint/access restrictions trigger a pause alert, close the
  headless context, and open the same profile visibly. Resolve the issue on the
  bot computer and press Enter in its terminal. The bot reads only local DOM while
  waiting; it does not automatically refresh the page. If Facebook blocks access,
  keep it paused until access is restored, or stop with Ctrl+C.
- Ordinary load/extraction failures back off from five minutes up to an hour.
  Three consecutive failures for the same search pause for manual inspection.
  Successful scans of other items do not reset that search's failure count. If selectors need
  maintenance, stop and update `marketplace_bot/browser.py` and `parsing.py`.
- Webhook rejection/rate limits end the current notification batch and defer
  retries across all searches using that notification service. If the pause alert
  itself fails, the reason appears in the terminal.

This needs a running computer, network access, and an interactive desktop/terminal
for setup and challenge recovery. Headless operation still needs a desktop when a
challenge occurs. Typical detection latency is several minutes; a backlog or slow
page or additional searches can add time. Jitter and low request volume do not guarantee account safety
or access. Use only an account and access you are authorized to use, consistent
with Facebook's applicable rules.

## Deduplication and delivery uncertainty

Listing IDs are global primary keys across restarts and searches in the same DB.
Already alerted IDs are never automatically alerted again. Keep the database to
retain this history. First-run existing results are alerted by default; set
`poll.alert_on_first_run: false` to seed each item's initial visible result window silently.
Listings outside the initial window may still appear as new on a later scan.

HTTP webhooks and SQLite cannot provide a single atomic exactly-once transaction.
The bot commits `sending` before POST. A timeout, ambiguous server failure, missing
acknowledgement, or crash during delivery becomes `uncertain` and is **not retried
automatically**, avoiding duplicates at the cost of potentially missing an alert.
Definite rejections remain queued. Discord uses `wait=true` for acknowledgement.

```powershell
.\.venv\Scripts\marketplace-bot.exe status
# Stop the running bot before using status or retry.
# Check your notification channel first. This opt-in can send a duplicate.
.\.venv\Scripts\marketplace-bot.exe retry 123456789 --accept-duplicate-risk
```

Pending discoveries are associated with their search settings. Removing an item
does not send its unrelated backlog. If another active search finds the same
unsent listing, it can take ownership of that queue entry without clearing any
retry deadline. Overlapping searches therefore alert a listing once. Sent and
uncertain IDs remain deduplicated globally. Return to the original configuration
to drain other pending entries from a removed item.

## Extraction limits and modification

Only rendered Marketplace content is read. Posted time remains Facebook's relative
text (for example, "Listed 2 hours ago in Auckland"); if hidden it is labelled
`Not shown by Facebook`. The card parser uses visible price/title/location and
the item's thumbnail. Do not infer an exact timestamp from the time first seen.

Title filtering requires all query words by default, because Marketplace can show
loosely related recommendations. Turn `require_all_query_words` off for broader
matching. Numeric prices are checked locally against `max_price`; unknown prices
are skipped unless `alert_unknown_price` is enabled. Use the account's local NZD
currency. Location/radius filtering depends on the verified Facebook UI and its
behaviour; the bot cannot compute exact distances from suburb text. Related/outside
search sections and sponsored cards are skipped where their visible headings are
recognized. Facebook can still omit results or ignore filters, so this cannot
guarantee complete coverage or a strict geographic boundary.

DOM selectors, challenge detection, and card extraction live together in
`marketplace_bot/browser.py`. Parsing lives in `parsing.py`; adapters live in
`notifications.py`. Config validation, store transactions, and the poll loop are
separate modules with comments explaining the recovery/deduplication tradeoffs.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests_browser -v
```

The first suite uses temporary databases and mocked notification/browser calls.
The second uses local HTML fixtures in Chromium; neither contacts Facebook or
sends real notifications. Your account-specific selectors and filters still need
verification using `setup`, `test-notification`, and `run --once`.

References: [Playwright persistent browser contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context),
[Discord webhook API](https://docs.discord.com/developers/resources/webhook#execute-webhook),
[Telegram Bot API](https://core.telegram.org/bots/api#sendmessage).
Discord alerts mention `@everyone` when Facebook shows a posted time under 60 minutes
(including just now, seconds ago, and minutes ago). Older or unknown times receive
the normal alert. Set `notification.recent_ping_minutes` in `config.yaml` to change
the threshold, or set it to `0` to disable mentions. Matching and AI screening still
apply before alerts are sent, and polling remains unchanged. Discord channel
permissions and your phone's notification settings must allow the mention.
Search entries accept `aliases`, such as `["playstation 5", "play station 5"]`.
With `poll.fuzzy_titles: true`, title filtering tolerates common misspellings in
long words and spacing differences, while keeping short model identifiers exact.
AI screening remains the final relevance check. Aliases apply to titles Facebook
returns; they do not issue extra Facebook searches. For broader discovery, add
another entry under `searches` with an alternate query and the same item description,
then run `setup` to verify it. Extra searches share the existing polling cadence
and SQLite deduplication.
To use Firefox, install it with `python -m playwright install firefox`, set
`browser.engine: "firefox"` and `browser.profile_dir: "data/firefox-profile"`, then
run `setup` to log in and verify searches. This uses Playwright's Firefox build
and a dedicated profile rather than your everyday Firefox session. `browser.channel`
is only used by the Chromium engine. The SQLite listing history remains shared.
