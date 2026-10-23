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

The product lists live in `catalog/` (YAML) and can be edited from the
dashboard's Catalog page.

## Install

You need Python 3.11 or newer.

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

The database, `dealfinder.db`, is created in the folder you run from. If you
ran a pre-release build of this project, delete the old `dealfinder.db` first,
because the schema changed during development.

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

- **Cost:** each scan costs roughly $0.05 to $0.12, and at most about $0.40.
- **Caps:** the defaults are $5 per day and $50 per month. Only lots under $300
  are scanned automatically. The daily cap, monthly cap and max scan price are
  all editable in the header, and the header shows what you have spent today
  and this month. (`initial_daily_cap`, `initial_monthly_cap` and
  `initial_max_scan_price` in `config.yaml` set the starting values; once you
  edit them in the header, the saved values win.)
- **One lot at a time:** the "Scan this lot" button on a listing's page runs a
  single scan, even for lots over the max scan price. The caps still apply.

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

- **ShopGoodwill and HiBid** are read through unofficial endpoints. Their
  response shapes were written from assumptions: the environment this was built
  in could not reach either site. **The first live run may need fixes.**
- **eBay sold prices** (the market values) are scraped from eBay's public
  sold-listings page, whose markup changes from time to time.

When a site breaks, the dashboard header shows a "probable site change" or an
error banner for that source. The other sources keep running.

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
