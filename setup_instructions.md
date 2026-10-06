# Kartik's Marketplace Bot

```text
  +--------------------------------------------------+
  |           KARTIK'S MARKETPLACE BOT                |
  |       Your step-by-step first-time setup          |
  +--------------------------------------------------+
```

Find Facebook Marketplace listings and receive Discord alerts on your phone.
The bot checks multiple items, uses SQLite to remember sent listings, and can use
Gemini to reject unrelated items. You open the listing and message the seller yourself.

**Follow these steps in order.** Commands below are for Windows PowerShell.
You do not need to activate the virtual environment or set environment variables.

## 1. Get the folder and Python ready

1. Download this project and extract the ZIP if needed.
2. Put the folder somewhere easy to find, such as `Documents\marketplace_bot`.
3. Install Python **3.11 or newer** from [python.org](https://www.python.org/downloads/windows/).
4. In the installer, enable **Add Python to PATH** if offered.
5. Close and reopen PowerShell after installing Python.
6. Open the project folder in File Explorer. Make sure you can see `pyproject.toml`.
7. Click the address bar, type `powershell`, and press Enter.
8. Check Python:

```powershell
py --version
```

You should see Python 3.11 or newer. If `py` is unavailable but `python --version`
works, use `python` instead of `py` in the next command.

## 2. Install the bot

Run these commands **one at a time**, waiting for each to finish:

```powershell
py -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

The beginner configuration uses **Microsoft Edge**, which must be installed.
If you specifically want Firefox, see the Firefox section below.

## 3. Create your config file

For a fresh installation, run:

```powershell
Copy-Item config.example.yaml config.yaml
notepad config.yaml
```

**If you already have a configured `config.yaml`, skip the copy command.**
It would overwrite your settings. Open your existing file instead.

Keep Notepad open. You will paste your Discord URL into this file shortly.
Save with **Ctrl+S** after every change. Keep the filename `config.yaml`, not
`config.yaml.txt`. YAML uses spaces for indentation; do not use tabs.

## 4. Create a Discord server and alerts channel

1. Install Discord on your computer and phone and sign into the same account.
2. On desktop Discord, click the **+** in the server list.
3. Choose **Create My Own**, then the personal/friends option if asked.
4. Name the server something like **Marketplace Alerts** and create it.
5. Create a **text channel** called `marketplace-alerts`, using the **+** beside
   Text Channels. A voice channel will not work for these messages.
6. You can also use an existing server where you have permission to manage webhooks.

## 5. Create the Discord webhook

1. Click your server name at the top left.
2. Open **Server Settings**.
3. Select **Integrations**.
4. Open **Webhooks**, then choose **New Webhook** or **Create Webhook**.
5. Set its name to **Kartik's Marketplace Bot**.
6. Select your `marketplace-alerts` text channel as its destination.
7. Save changes if prompted.
8. Click **Copy Webhook URL**.
9. Return to `config.yaml` in Notepad.
10. Find `notification:` and replace the placeholder after `webhook_url:` with
    the copied URL. Keep quotation marks around it:

```yaml
notification:
  provider: "discord"
  webhook_url: "PASTE_YOUR_DISCORD_WEBHOOK_URL_HERE"
  recent_ping_minutes: 60
```

Paste **only the URL**: do not include `$env:`, a PowerShell command, or Markdown
link brackets. Its beginning should look like `https://discord.com/api/webhooks/`.
Save the file. If Integrations or Webhooks is missing, use a server you own or ask
its owner for the Manage Webhooks permission.

The URL lets someone post to that channel. Keep your real `config.yaml` private;
share `config.example.yaml` with friends instead.

Official reference: [Discord's webhook setup guide](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks).

## 6. Turn on phone notifications and test them

1. On your phone, allow Discord notifications in the phone's Settings app.
2. In Discord, make sure the server and alerts channel are not muted.
3. Choose **All Messages** in the alerts channel's notification settings if you
   want normal listing alerts as well as mentions.
4. Turn off suppression of `@everyone` mentions for this server if you want urgent pings.
5. In PowerShell, run:

```powershell
.\.venv\Scripts\marketplace-bot.exe test-notification
```

Look for a test message in the chosen channel. A success message confirms Discord
accepted it; phone push delivery also depends on your Discord and phone settings.

Now test the recent-listing ping:

```powershell
.\.venv\Scripts\marketplace-bot.exe test-notification --recent
```

This sends a **fake test listing marked 10 minutes old**, including `@everyone`
with the default 60-minute threshold. It does not scan Facebook or call Gemini.
Everyone who can see the channel may receive this mention. Real alerts only ping
when Facebook shows a time younger than the threshold. Unknown times do not ping.
Set `recent_ping_minutes: 0` to disable mentions.

## 7. Choose what to search for

Edit the `searches:` section in `config.yaml`. Each entry is one Facebook search:

```yaml
searches:
  - query: "ps5"
    aliases: ["playstation 5", "play station 5"]
    description: "A PS5 console for sale. Reject games only, controllers only, wanted posts, and repairs."
    max_price: 500
  - query: "series s"
    aliases: ["xbox series s"]
    description: "An Xbox Series S console for sale. Reject Xbox One S, accessories only, and wanted posts."
    max_price: 300
  - query: "quest"
    aliases: ["oculus", "meta quest"]
    description: "A Meta or Oculus Quest 2, Quest 3, or Quest 3S headset for sale. Reject accessories only, wanted posts, and repairs."
    max_price: 600

max_price: 1000
currency: "NZD"
location: "Auckland, New Zealand"
location_slug: "auckland"
radius_km: 35
```

Replace your existing search section; do not add a second `searches:` heading.
An item without its own `max_price` uses the shared maximum. The example broad
`quest` query can find several models; Gemini needs to be enabled to apply its description.
Aliases and typo tolerance help match returned titles, but do not create extra
Facebook searches. Add separate query entries if you want more searches.

Edit `message_template` to the message you want to copy when contacting a seller.
The bot includes it in alerts; it never sends that message to the seller.

## 8. Optional: enable Gemini to reject irrelevant listings

Skip this step to start without AI. The sample has `ai.enabled: false`.

1. Open [Google AI Studio's API key page](https://aistudio.google.com/apikey).
2. Sign in and follow the account's setup prompts.
3. Select **Create API key** and choose or create a project when prompted.
4. Copy the generated key.
5. In `config.yaml`, find `ai:`. Set `enabled: true` and replace the API key placeholder:

```yaml
ai:
  enabled: true
  api_key: "PASTE_YOUR_GEMINI_API_KEY_HERE"
  model: "gemini-3.1-flash-lite"
  endpoint: "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
  timeout_seconds: 30
```

Save. Your per-item `description` tells AI what to accept or reject. API availability,
quotas, and pricing depend on your Google project; check them in AI Studio.
On API failures, listings stay pending rather than being sent without verification.

Official reference: [Google's API key instructions](https://ai.google.dev/gemini-api/docs/api-key).

## 9. Log into Facebook and verify each search

Run:

```powershell
.\.venv\Scripts\marketplace-bot.exe setup
```

1. A browser opens. Log into Facebook yourself.
2. For the first item, inspect the query, location, radius, maximum price, and
   **Date listed: Newest first** in Facebook's visible controls.
3. Check the actual cards. They should include the kind of item you want.
4. If results are unrelated, retype the query into Facebook's search box and press
   Enter. Apply filters one at a time and check the results again.
5. Leave the browser on the **search results page**, not an individual listing.
6. Return to PowerShell, type `READY`, and press Enter.
7. Repeat for every configured search.
8. Wait for **Session and all verified search URLs saved**.

Facebook can ignore generated URL filters. Do not approve a page solely because
the address bar looks right. The bot saves the page URL you verified.
Your login is saved in the bot's dedicated profile, separate from your everyday browser.

## 10. Start monitoring

```powershell
.\.venv\Scripts\marketplace-bot.exe run
```

Keep the terminal open and the computer awake and connected to the internet.
Normal monitoring runs headlessly. The bot rotates through searches, waiting
2–5 minutes after each scan. Four searches take roughly 8–20 minutes plus scan
time for a full cycle; this is not an instant stream of all new Facebook listings.

`Alert sent` means the notification was confirmed. `Listing suppressed by AI`
means it was rejected. `0 match title/price filters` means cards were rejected
before AI; the log then prints reasons and sample titles.

Existing results can trigger alerts on the first run. To start silently, set
`poll.alert_on_first_run: false` before the first scan of a new search.
The bot considers up to five queued candidates per scan by default, so a backlog
may take several cycles to process.

## 11. Stop, restart, and change settings

Press **Ctrl+C** in PowerShell to stop. To start next time, open PowerShell in the
project folder and run the same `run` command. No installation commands are needed again.

Restart after changing config: it is loaded on startup. If you change search queries,
prices, city, radius, or search URLs, rerun `setup` before `run`.
Changing descriptions, aliases, or notification credentials only needs a restart.

Keep `data/listings.sqlite3`: it remembers sent IDs across searches and restarts.
Deleting it removes that memory and can cause old listings to alert again.
Keep the browser profile private because it contains your saved Facebook session.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| `marketplace-bot` is not recognized | Use the full `.\.venv\Scripts\marketplace-bot.exe` command shown above. |
| `config.yaml` not found | Open PowerShell in the project folder and copy `config.example.yaml` as described in step 3. |
| Invalid YAML | Use spaces, preserve indentation, and quote URLs, keys, and descriptions. |
| Discord HTTP 401/403/404 | Check the copied URL and that the webhook still exists in the selected server. |
| Discord test arrives but phone stays quiet | Check server/channel mute settings, phone notification permissions, and mention suppression. |
| AI HTTP 401/403 | Check the Gemini key and its project/API access in AI Studio. |
| AI HTTP 429/503 | Wait: quota or service failure defers screening. Check Google project quotas if persistent. |
| Zero Quest matches | Inspect actual results in `setup`, try a broader `quest` query, and read the logged rejection samples. |
| Another bot command is using this database/profile | Stop the existing bot with Ctrl+C before running setup or tests. |
| Login or CAPTCHA | Resolve it manually in the visible browser and press Enter in the terminal when asked. |
| Uncertain delivery | Use the `status` command and inspect Discord. Automatic retry is disabled to avoid duplicates. |

```powershell
.\.venv\Scripts\marketplace-bot.exe status
```

### Optional Firefox setup

Install the Playwright Firefox build:

```powershell
.\.venv\Scripts\python.exe -m playwright install firefox
```

Set these fields inside your existing `browser:` section:

```yaml
browser:
  engine: "firefox"
  profile_dir: "data/firefox-profile"
```

Keep the other browser settings. `channel` is ignored for Firefox. Run `setup`
again to log into its separate profile. Playwright uses its own Firefox build,
not your ordinary Firefox installation.

If you see `spawn UNKNOWN`, launch the installed executable directly to obtain
Windows' error. If Windows reports an incorrect side-by-side configuration, try
repairing/installing the [Microsoft Visual C++ x64 Redistributable](https://aka.ms/vc14/vc_redist.x64.exe)
and restarting Windows. That is a possible remedy, not a guaranteed diagnosis.
You can return to Edge by setting `engine: "chromium"`, `channel: "msedge"`, and
`profile_dir: "data/browser-profile"`, then running `setup`.

## Everyday commands

```powershell
# Start
.\.venv\Scripts\marketplace-bot.exe run

# Verify searches after changing filters
.\.venv\Scripts\marketplace-bot.exe setup

# Test Discord
.\.venv\Scripts\marketplace-bot.exe test-notification

# Test the under-an-hour @everyone alert
.\.venv\Scripts\marketplace-bot.exe test-notification --recent
```
