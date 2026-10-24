# ConsoleHawk

ConsoleHawk is a local web app that watches eBay, ShopGoodwill and HiBid for deals worth
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

The sidebar opens each workspace. **Source status** expands connection notices and errors.
Wide tables keep their headers and listing actions visible while scrolling.
On a phone, listings appear as individual cards.

On Windows, after installation, double-click **Start-ConsoleHawk.cmd** in the
project folder. It starts the app in the background, waits for the dashboard,
and opens a separate app window without tabs or an address bar. Double-clicking again opens the running app. Keep the
launcher with the project and its `.venv` folder; a Windows shortcut can point
to it from anywhere. Startup errors appear in the launcher window, with app
logs saved in `logs/`. The scanner keeps running after you close the dashboard window. Microsoft Edge must be installed on Windows; Chrome is a fallback for the dashboard. Use `Start-ConsoleHawk.cmd -Browser` only if you prefer a normal browser tab.

The default view shows only good deals supported by recent matching eBay sold
averages. Missing or weak evidence, small margins and negative profit stay hidden.
Enable **Include unpriced and lower-margin listings** to review them. Counts show
how many matches are hidden or awaiting comparisons. Deals displays 100 results
per page; Next/Previous preserves filters. Pictures are 120 px on desktop and
150 px on phones. Rows update only when data changes (or once per minute for time
and freshness), without resetting the page or filters.

Keep your existing `dealfinder.db`; this update does not require a reset.

## Built-in background scanning (no extension)

ConsoleHawk starts its collector automatically. On Windows it uses installed
Microsoft Edge in headless mode with a separate local profile for each site.
It never attaches to your normal browser or opens ordinary search tabs. No API
key is required. Keep the PC awake and ConsoleHawk running; the dashboard window
can be closed. On other platforms install the Playwright Chromium browser with
`python -m playwright install chromium`.

Open **Connections** to see each site's live scanner state. If eBay or HiBid needs
sign-in or verification, click **Sign in** for that site, finish in the separate
site window yourself, then click **Resume scanning**. That window closes and
searches resume invisibly. Sessions are remembered; repeated login is needed only
if a site expires or rejects its session. A blocked site pauses without opening
pop-ups, while the other site continues. Background sessions can still be blocked
by a site after sign-in; the app reports this and does not bypass verification.

The collector rotates one search every 30 seconds, alternating sources and eBay
active/sold searches. It collects USD auctions with visible closing countdowns
and Buy It Now listings, and dated sold comparisons for actual catalog matches.
Full eBay pages queue bounded pagination (up to five pages per query). Query
rotation, site loading and sign-in requirements mean coverage is gradual.
App **Pause** suspends new search jobs. Current searches may finish.

Only public listing-card fields enter the database. Password fields and account
pages are never captured. Browser-managed sessions stay inside the ignored
`.collector-profile/` folder; the dashboard profile is `.desktop-profile/`.
Treat both as private. Never publish either profile or the ignored
`*.browser-token`/`*.credentials` files. The collector does not bid or buy.

Existing browser extensions receive a paused response and cannot start new jobs
while built-in collection is enabled. They are no longer required. The older
helper implementation remains available for legacy/custom deployments.
Imports do not send alerts; scheduled refreshes retain your notification
preferences and deduplication. Cached observation times are preserved and normal
freshness rules still apply. Sold prices are never invented when evidence is missing.

## Average sold value and deal colors

Current price and **Average eBay sold** appear together on Deals, hardware lots,
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

## Connections and automatic HiBid search

Open **Connections** to sign in to eBay or HiBid and resume their background
scans. ShopGoodwill searches public listings directly and needs no discovery
account. HiBid only accepts USD lots with shipping, a payable bid and a closing
countdown. Captures expire after 30 minutes; reprocessing does not renew them.
Shipping costs and premiums remain estimates. The legacy direct HiBid API is
still blocked. Background search uses rendered public cards, not session-token
replay against an undocumented API.

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

Deals and Bulk lots share title/model/edition search, brand, console /
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
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | Official eBay API search, keyless auctions and Buy It Now are also available through the browser helper | https://developer.ebay.com: join the developer program, create an app, and use the **production** keyset (App ID is the client ID, Cert ID is the client secret) |
| `DISCORD_WEBHOOK_URL` | Discord alerts | In Discord: Server Settings, Integrations, Webhooks, New Webhook, Copy Webhook URL |

Set them before starting the app.

macOS / Linux:

```
export EBAY_CLIENT_ID="your-app-id"
export EBAY_CLIENT_SECRET="your-cert-id"
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python -m dealfinder
```

Windows PowerShell:

```
$env:EBAY_CLIENT_ID = "your-app-id"
$env:EBAY_CLIENT_SECRET = "your-cert-id"
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
python -m dealfinder
```

These last only for that terminal session.

## Photos

Listing photos remain free to view. Paid AI photo analysis, its controls and its
manual endpoints have been removed. No Anthropic client is created, even if a
previous API key is still present. Historical scan data is preserved locally.

## Discord alerts

When a Discord channel is connected or `DISCORD_WEBHOOK_URL` is set, alerts are sent for:

- target hits (a listing at or below the max price you set for a product on the Catalog page),
- hardware-lot deals,

Plain resale deals (a single item priced below market) are **off by default**.
Enable **Single-item deals** under Connections → Discord. To alert only for
chosen products, select **Selected catalog items only**, then check **Alert me**
on the desired catalog rows and Save. Search enablement and notifications are
independent. An unselected item does not consume its notification claim.
The same defaults can also be set with `notify_reasons` in `config.yaml`:

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

## Coverage and matching

Console discovery includes Halo 3, PS2 and every enabled sourced console family's
query, alongside exact curated editions. ShopGoodwill and HiBid use the same
console-family expansion. ShopGoodwill follows up to five 40-item pages per query.
The browser helper rotates one search every 30 seconds; it needs a running browser,
PC and app. This is a rotating monitor with bounded result pages, not a complete
instantaneous copy of every marketplace. HiBid requires visible USD, shipping and
closing-time evidence. Verification stops that site until the user reconnects it.

Fans, protectors, loose buttons, controller parts, replacement housings, fight
sticks and other nontraditional controls are excluded before single/lot routing
and from saved results. A console sold with its ordinary controller or box can
still match. PS2 model/edition aliases cover seller wording such as Hyakushiki,
Sakura Pink and SCPH-77000PK. Explicitly used items cannot inherit a brand-new
valuation. Contaminated old comparison caches are recomputed from retained evidence.

## Hardware-focused scope

Game lots have been retired: no game-lot discovery queries, page, catalog controls
or Discord alert option remain. Existing game records stay in SQLite for history,
but the app does not collect or price new game lots. Platform names remain internal
for recognizing console generations and masking model numbers when counting hardware.
Console bundles that include games can still match their console hardware.

Cameras, camcorders, external GPU enclosures and docks, eGPUs and ASUS XG Mobile
systems and mobile/laptop computers are not desktop GPU listings. Controllers
are also prevented from inheriting complete-console valuations. These exclusions apply to new ingestion,
historical search results, sold comparison caches and Discord alerts.
