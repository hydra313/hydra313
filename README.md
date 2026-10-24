# dealfinder

A local web app that watches eBay, ShopGoodwill and HiBid for deals worth
reselling, scores them against recent sold prices, and (optionally) alerts you
on Discord. It runs on your own machine and serves a dashboard at
http://127.0.0.1:8000.

## What it looks for

- **GPUs:** NVIDIA RTX only (30, 40 and 50 series).
- **CPUs:** recent Intel and AMD desktop processors.
- **Limited-edition consoles and controllers:** collectible editions of any
  generation, for example the Xbox 360 Halo 3 Special Edition or the PS4 20th
  Anniversary.
- **Bulk for-parts hardware lots:** broken consoles, controllers, GPUs and CPUs
  sold in bulk, to repair and resell. The expected profit accounts for repair
  cost and success rate.
- **Working video game lots:** priced per game against market value per game.

The YAML files in `catalog/` are the seed lists. The Catalog page lets you turn
products on or off, set a target max price per product, tune repair cost and
success rate for bulk lots, and add your own entries. Those changes are stored
in the database, not in the YAML, so updating the seed files never loses them.

## Install

You need Python 3.11 or newer, and SQLite 3.35 or newer (bundled with Python 3.11+
on Windows and macOS; on Linux check
`python -c 'import sqlite3; print(sqlite3.sqlite_version)'`).

```
python -m venv .venv
# macOS / Linux:        source .venv/bin/activate
# Windows PowerShell:   .venv\Scripts\Activate.ps1
pip install -e .
```

Run it from the project folder (the one that contains `catalog/`):

```
python -m dealfinder
```

Then open http://127.0.0.1:8000. The first refresh starts immediately, and then
one runs every 30 minutes (`refresh_minutes` in `config.yaml`). The header has
a Pause button and a "Refresh now" button.

The sidebar opens each workspace. **Scan budgets** in the header expands the
spending controls; **Source status** expands connection notices and errors.
Wide tables keep their headers and listing actions visible while scrolling.
On a phone, listings appear as individual cards.

On Windows, after installation, double-click **Start-Dealfinder.cmd** in the
project folder. It starts the app in the background, waits for the dashboard,
and opens your browser. Double-clicking again opens the running app. Keep the
launcher with the project and its `.venv` folder; a Windows shortcut can point
to it from anywhere. Startup errors appear in the launcher window, with app
logs saved in `logs/`. The app keeps running after you close the browser.

Use **Show all listings** on Deals, Bulk lots or Game lots to inspect results that
have no market value or do not meet the deal thresholds. It also exposes lots
whose quantity is unknown, so you can open them for a manual photo scan. Missing
market values are not evidence of a bargain. Completed refreshes update the rows
automatically while keeping the current filters.

The database, `dealfinder.db`, is created in the folder you run from. If you
ran a pre-release build of this project, delete the old `dealfinder.db` first,
because the schema changed during development.

## HiBid browser import

HiBid's direct API currently returns HTTP 403, so the default mode imports
results that you load in your normal browser. Open **HiBid import** in the
dashboard, choose a search, and wait for lots to load. Complete any sign-in or
verification in HiBid's tab. Press **Ctrl+A**, **Ctrl+C**, then paste into the
import box and click **Import HiBid results**. Alternatively, save the loaded
page as **Webpage, Complete** and upload its HTML file.

Only USD lots with shipping, a payable bid and a closing countdown are accepted.
The app matches them against your catalog. Imports expire after 30 minutes;
refresh HiBid and import again to update prices. Expired prices are hidden and
excluded from scans and alerts. Importing itself sends no alerts and starts no
paid scans. Shipping costs and premiums remain estimates; check the auction.

This is a manual browser capture, not unattended HiBid search. No cookies or raw
page HTML are stored in the database. `hibid_mode` can be `browser` (default),
`api` (legacy adapter) or `off`; `hibid_snapshot_minutes` accepts 1–120 minutes.
See [the HiBid validation report](docs/hibid-browser-import.md).

## API keys and the webhook (all optional)

Everything is optional. A missing key disables only the feature that needs it,
and the dashboard says so. Secrets are read from environment variables only,
never from `config.yaml`.

| Variable | Enables | Where to get it |
| --- | --- | --- |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | eBay search | https://developer.ebay.com: join the developer program, create an app, and use the **production** keyset (App ID is the client ID, Cert ID is the client secret) |
| `ANTHROPIC_API_KEY` | Photo scanning | https://console.anthropic.com |
| `DISCORD_WEBHOOK_URL` | Discord alerts | In Discord: Server Settings, Integrations, Webhooks, New Webhook, Copy Webhook URL |

Set them before starting the app.

macOS / Linux:

```
export EBAY_CLIENT_ID="your-app-id"
export EBAY_CLIENT_SECRET="your-cert-id"
export ANTHROPIC_API_KEY="sk-ant-..."
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python -m dealfinder
```

Windows PowerShell:

```
$env:EBAY_CLIENT_ID = "your-app-id"
$env:EBAY_CLIENT_SECRET = "your-cert-id"
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
python -m dealfinder
```

These last only for that terminal session.

## Photo scanning and what it costs

Some lots are only worth buying because of what is in the photos (a box of
mixed consoles, a lot with a rare game in it). Photo scanning sends a lot's
photos to Claude to identify the items and value them.

**It is off by default.** Turn it on with the "Photo scanning" switch in the
dashboard header. It also needs `ANTHROPIC_API_KEY`.

- **Cost:** each scan costs roughly $0.05 to $0.12, and at most about $0.50.
- **Caps:** the defaults are $5 per day and $50 per month. Only lots under $300
  are scanned automatically. The daily cap, monthly cap and max scan price are
  all editable in the header, and the header shows what you have spent today
  and this month. (`initial_daily_cap`, `initial_monthly_cap` and
  `initial_max_scan_price` in `config.yaml` set the starting values; once you
  edit them in the header, the saved values win.)
- **One lot at a time:** the "Scan this lot" button on a listing's page runs a
  single scan. It works even when the scanning switch is off, as long as
  `ANTHROPIC_API_KEY` is set and the daily and monthly caps allow it, and it
  ignores the max scan price filter.

As a backstop, also set a monthly spend limit on the API key in the Anthropic
Console (console.anthropic.com, Settings, Limits). Then even a bug or a
misconfiguration here cannot spend more than that limit.

## Discord alerts

When `DISCORD_WEBHOOK_URL` is set, alerts are sent for:

- photo finds (rare items or valuable lots found by scanning),
- target hits (a listing at or below the max price you set for a product on the Catalog page),
- hardware-lot deals,
- game-lot deals.

Plain resale deals (a single item priced below market) are **off by default**.
Turn them on with `notify_reasons` in `config.yaml`:

```yaml
notify_reasons:
  resale_deal: true
```

Each listing alerts at most once per reason.

## config.yaml

Copy `config.example.yaml` to `config.yaml` in the folder you run from, and
change only what you need. The example lists every setting with its default
and a one-line comment. Unknown keys are rejected at startup, so a typo shows
up as an error instead of being silently ignored.

## Fragile parts, and how to fix them

Parts of this app depend on sites that give no stability guarantees.

- **ShopGoodwill** uses an unofficial buyer API. Search and detail photos were
  verified against live responses on October 23, 2026. Requests include explicit
  price bounds and exclude pickup-only listings. Actual shipping still needs to
  be checked on the seller's listing; the dashboard labels shipping estimates.
- **HiBid** defaults to browser import, verified with real copied results on
  October 23, 2026. The legacy API still returns HTTP 403. Browser imports require
  manual refreshes and depend on the visible card markup.
- **eBay sold prices** (the market values) are scraped from eBay's public
  sold-listings page, whose markup changes from time to time. The live check
  returned HTTP 403. A block stops further sold-price requests for that refresh
  and appears in the dashboard. Cached values and the existing eBay Browse
  fallback remain available; the next refresh retries sold prices.

When a site breaks, the dashboard header shows a "probable site change" or an
error banner for that source. The other sources keep running.

See [the live validation report](docs/live-validation-2026-10-23.md) for the
tested fixes and remaining integration limits.

### Capturing a real response and fixing an adapter

1. Open the site in your browser, open dev tools (F12), go to the Network tab,
   and run the search that the app makes.
2. Find the request, and copy its response (right-click, Copy, Copy response).
   For eBay sold prices, save the page's HTML instead.
3. Save it under `tests/fixtures/<site>/` (`shopgoodwill`, `hibid` or
   `ebay_sold`), replacing or adding to the existing fixture.
4. Edit the constants block at the top of `dealfinder/sources/<site>.py`: the
   URLs, the request body or query, and the field names. For eBay sold prices,
   edit the `SELECTORS` dict in `dealfinder/sources/ebay_sold.py`.
5. Run `pytest tests/test_<site>.py` (for example `tests/test_shopgoodwill.py`)
   and adjust until it passes, then run the app to confirm.

## Running the tests

```
pip install -e '.[dev]'
pytest
```

The tests never touch the network or the real Anthropic API.
