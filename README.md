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

The console catalog contains 1,774 identities, including home consoles,
handhelds, regional variations and promotional editions. The wider factual
reference snapshot is in `catalog/console_editions.json`; curated releases and
aliases are in `catalog/consoles.yaml`. Use **Search catalog** to find an edition;
reference links identify its source. Products appear 200 per page. See the
[coverage audit](docs/console-catalog-audit.md) for sources, exclusions, announced
releases and the limits of the completeness claim.

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
one runs every 2 minutes (`refresh_minutes` in `config.yaml`). Refreshes never
overlap. The header shows the active source/query, new and updated listing counts,
completion or failure, and the next scheduled check. Pause stops automatic checks;
Refresh now starts a manual check. The PC must be awake and the app must be running.

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

**Show all listings** is on by default on Deals, Bulk lots, and Game lots. This
includes results with no market value, listings below the deal thresholds, and
lots whose quantity is unknown. Uncheck it to show only deal and target matches.
Missing market values are not evidence of a bargain. Ended, dismissed, and stale
listings stay hidden. Running and completed refreshes update the rows automatically while
keeping the current filters. The dashboard uses dark mode throughout.

The database, `dealfinder.db`, is created in the folder you run from. If you
ran a pre-release build of this project, delete the old `dealfinder.db` first,
because the schema changed during development.

## eBay without API keys

Open **eBay browser** in the sidebar. Download and extract the helper, load its
folder with **Load unpacked** on Chrome or Edge's Extensions page (Developer mode),
then reopen the setup page in that same browser and click **Connect and start**.
This one-time browser installation is needed because direct eBay requests are
blocked on some networks. It does not bypass verification or replay browser cookies.

The helper rotates through enabled catalog queries, one search every 30 seconds,
using a single background tab. Searches alternate across GPUs, CPUs, consoles,
controllers and both lot categories, giving each category an early turn.
It alternates visible USD **Buy It Now** cards with
sold-price searches for products actually on your dashboard. Keep the browser and app open. Sleep and
browser throttling can delay checks. The header reports when the helper is not
connected. A verification or parser failure stops the helper and leaves its tab
open for you; complete verification and reconnect. **Stop browser helper** stops
its loop, and the header's Pause stops new automatic searches too.

Imports contain only listing fields, not account details, cookies or passwords.
They do not send alerts or initiate paid photo scans. Subsequent scheduled runs
use the usual notification and photo-budget gates. Cached records retain their
original observation time and expire under the normal stale-listing rule (two
full eBay passes, six hours by default); reprocessing a cache is not a new capture.
Auction search still uses the optional official API. Sold searches can require
you to sign in to eBay in the helper's browser. It leaves that tab open for you
and never reads credentials or solves verification. Direct sold-price requests
can remain blocked; captured sold pages supply evidence locally without an API key.

## Average sold value and deal colors

Current price and **Average eBay sold** appear together on Deals, both lot pages,
and listing details. The average uses matched, same-condition sales from the last
90 days, includes known shipping, and removes IQR outliers. Duplicated browser
records and undisclosed accepted-offer prices are excluded. Lot comparisons scale
the per-unit average to the lot quantity. Click **View sold evidence** to inspect
the retained comparable sales.

- **Green / Good deal:** estimated profit meets the configured minimum and the
  total purchase cost is at least the configured discount below the sold average
  (defaults: $30 for singles, $50 for lots, and 20%).
- **Yellow / Average:** estimated profit remains positive but misses those targets.
- **Red / At limit · Pass:** estimated profit is zero or negative after resale
  fees, shipping, and applicable repair assumptions. A low asking price alone does
  not make a profitable deal.
- **Gray / Unknown:** no reliable recent sold average, too few comps, unknown
  quantity/cost, or stale data. Asking-price estimates and older median caches
  never receive a sold-based traffic-light rating.

The percentage compares **landed purchase cost**, including configured inbound
shipping, taxes and buyer premiums, with the average sold value. Profit is a
separate estimate after selling costs. Averages expire after `market_ttl_hours`
(24 hours by default); older cached prices are labeled instead of colored.
Existing median records are retained as legacy estimates until refreshed.

The browser helper is in `browser-helper/`; its local capability token is stored
in an ignored `*.browser-token` file beside the database. Never publish that file.
The helper can access eBay search pages and `127.0.0.1:8000`, and sends captures only
to the local app. It does not place bids or buy items. A manual import form is
available on the same setup page; reload eBay before copying its results.

For JavaScript workflow checks, run `node --test tests/browser_helper.test.cjs`.

## Connections and automatic HiBid search

Open **Connections** in the sidebar. Browser helper **1.2** supports both eBay and
HiBid. Existing users should replace the helper files, reload the extension, allow
its HiBid permission, reload Connections in that browser, and click **Connect both
sites**. You can start or stop either site independently. ShopGoodwill already
searches public listings automatically and needs no account connection.

The helper rotates searches automatically in one background tab every 30 seconds.
No per-search copying or importing is needed. Keep the app and browser running.
If a site requests sign-in or verification, its collection stops and keeps its tab
open for you; the other enabled site continues. Complete verification yourself,
then reconnect that site. App Pause suspends new searches on both sites.

Only USD HiBid lots with shipping, a payable bid and a closing countdown are
accepted. Captures expire after 30 minutes and are never renewed just by
reprocessing them. Shipping costs and premiums remain estimates. Browser capture
itself starts no paid scans or alerts; scheduled refreshes retain all budget and
notification gates. Manual import remains available as a fallback. The legacy API
is still blocked; no browser session tokens or cookies are replayed.

For **Discord**, expand Connect Discord, copy a channel webhook from Discord's
channel settings → Integrations → Webhooks, and paste it into the password field.
On Windows this is saved using DPAPI for the current Windows user, outside SQLite
and source archives. A saved connection takes effect without a restart and takes
precedence over the environment variable. Disconnect disables it across restarts.
Select your alert types and optionally click Send test message. Single-item deal
alerts are opt-in; the existing default alert preferences are preserved.

Discord account sign-in alone cannot choose an alert channel. Webhook setup avoids
requiring users to register an OAuth application. Discord's alternative
[webhook.incoming OAuth flow](https://discord.com/developers/docs/topics/oauth2)
requires a registered application and an authorization-code exchange.

## Search and sorting

Deals, Bulk lots and Game lots share title/model/edition search, brand, console /
platform, condition, minimum/maximum current price and deal-rating filters. Site,
category and existing lot-platform filters still work together with these controls.
Console matching uses catalog identities first so a bundled game's name does not
change the detected console. Brand choices include chip makers and card vendors.

Sort ascending or descending by condition, current price, estimated profit,
profit margin (profit divided by total purchase cost), average sold value, deal
rating or time left. Unknown values sort last in either direction. A lot's sold
value uses its known quantity; missing quantities remain unknown. Search and sort
choices survive automatic row refreshes, bookmarks and page reloads.

## API keys and the webhook (all optional)

Everything is optional. A missing key disables only the feature that needs it,
and the dashboard says so. API keys are read from environment variables; Discord can also use the protected Connections form,
never from `config.yaml`.

| Variable | Enables | Where to get it |
| --- | --- | --- |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | Official eBay API search, including auctions; keyless Buy It Now uses the browser helper | https://developer.ebay.com: join the developer program, create an app, and use the **production** keyset (App ID is the client ID, Cert ID is the client secret) |
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

When a Discord channel is connected or `DISCORD_WEBHOOK_URL` is set, alerts are sent for:

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
- **HiBid** uses automatic browser-helper search after one-time connection.
  Live card selectors were checked on October 24, 2026. The legacy API remains
  blocked; browser verification must be completed by the user when requested.
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
