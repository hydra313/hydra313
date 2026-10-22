# Auction Deal Finder — Design

Date: 2026-10-22
Status: Approved in conversation, awaiting written-spec review

## 1. Goal

A personal, locally run web app that searches eBay, ShopGoodwill and HiBid
for underpriced items in four kinds of listing, shows them in a ranked
dashboard, and sends the best finds to Discord:

1. **Single items** — recent-generation CPUs, NVIDIA RTX graphics cards, and
   limited-edition consoles and controllers, in working condition.
2. **Bulk hardware lots** — 3+ broken or untested units of the same console,
   controller, GPU or CPU, bought to repair and resell.
3. **Video game lots** — 5+ working games for one platform, any generation.
4. **Hidden gems in lots** — Claude reads lot photos to spot rare games,
   consoles and special editions the title doesn't mention.

It serves two uses at once:

- **Buying for myself** — flag any single item whose total cost is at or
  below a max price I set for that product ("target hit").
- **Flipping** — flag listings whose total cost is well below what they
  resell for on eBay, after fees, shipping and (for broken lots) repairs.

Success looks like: opening the dashboard (or getting a Discord message)
shows a short, trustworthy list of real deals, with the evidence behind each
price estimate, without junk (empty boxes, wrong variants, reproductions),
and without spending money on photo scanning unless I've switched it on.

## 2. Decisions from brainstorming

| Topic | Decision |
|---|---|
| Purpose | Both price-target alerts and resale-value scoring |
| Sites | eBay, ShopGoodwill, HiBid |
| Interface | Local web dashboard (no hosting, no accounts, single user) |
| Single-item scope | Recent CPUs, NVIDIA RTX GPUs only, limited-edition consoles and controllers |
| Single-item condition | Working only; new/sealed and used valued separately |
| Bulk hardware lots | 3+ units, broken allowed, valued as repair-and-resell |
| Game lots | 5+ working games, one platform, any generation |
| Pickup-only lots | Excluded — shipped items only |
| Photo scanning | Claude Opus 5.5, off by default, daily and monthly caps |
| Item prices for photo finds | eBay sold listings, cached 30 days per title |
| Notifications | Discord webhook |
| Stack | Python 3.11+, FastAPI, SQLite, Jinja2 + HTMX, in-process scheduler |

## 3. Constraints and assumptions

- Runs on the user's own machine. The development container cannot reach the
  three sites, so development is tested against saved fixture responses; the
  first live run happens on the user's machine and may need adapter fixes.
- eBay is accessed through the official Browse API (free developer keys:
  `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, OAuth client-credentials flow).
- ShopGoodwill and HiBid have no public API. Their adapters call the JSON
  search endpoint and the GraphQL endpoint their own websites use. These can
  change without notice; each adapter is isolated so a breakage affects only
  that site and is visible on the dashboard.
- eBay sold prices come from eBay's public sold-listings search page, because
  the official sold-data API (Marketplace Insights) requires special approval.
  This is the most fragile part; it has a fallback (section 7).
- Photo scanning uses the user's own `ANTHROPIC_API_KEY` and is the only part
  of the app that costs money. Estimated cost is $0.05–0.12 per lot scanned
  (up to 8 photos, model reasoning included).
- Discord notifications use a webhook URL (`DISCORD_WEBHOOK_URL`) from a
  channel the user controls.
- Polite use only: at least 2 seconds between requests to the same site, a
  normal browser user agent, no parallel hammering, no auto-bidding.
- Sales tax is excluded from cost by default (configurable `tax_rate`, default 0).

## 4. Architecture

```
          ┌───────────── scheduler (every 30 min, "Refresh now", pausable) ─────────────┐
          │                                                                              │
 catalogs─┼─► source adapters ─► Listing ─► classifier ─┬─► single-item matcher ─┐        │
 (YAML +  │   (ebay, shopgoodwill,                      ├─► hardware-lot parser ─┼─► scorer ─► SQLite ◄── dashboard
  DB      │    hibid)                                   └─► game-lot parser ─────┘    │         │      (FastAPI +
  overrides)                                                     │                    │         │       HTMX)
          │                                         photo scanner (Claude, gated) ────┘         │
          │   market value service ── eBay sold page ──► comps                                  │
          │   notifier ── Discord webhook ◄──────────────────────────────────────────────────── ┘
          └──────────────────────────────────────────────────────────────────────────────┘
```

Package layout (`dealfinder/`):

| Module | Responsibility | Depends on |
|---|---|---|
| `models.py` | `Listing`, `Product`, `BulkProduct`, `Platform`, `MarketValue`, `Deal`, `ScanResult` dataclasses | — |
| `catalog.py` | Load seed YAML (products, bulk products, platforms), apply DB overrides and custom products | `models`, `db` |
| `text.py` | Title normalization and the quantity parser shared by all matchers | — |
| `classify.py` | Route a listing to single item, hardware lot, game lot, or reject | `text`, `matcher`, `lots` |
| `matcher.py` | Map a single-item title to one product + condition, or reject it | `text`, `models` |
| `lots.py` | Identify hardware lots and game lots (product/platform, quantity) | `text`, `models` |
| `sources/base.py` | `Source` protocol: `search(query) -> list[Listing]`; shared rate-limited HTTP client | `httpx` |
| `sources/ebay.py` | Browse API search + item detail (photos) + OAuth token caching | `base` |
| `sources/shopgoodwill.py` | ShopGoodwill search endpoint | `base` |
| `sources/hibid.py` | HiBid GraphQL search | `base` |
| `market.py` | Sold-comps fetch/parse, outlier trimming, median, caching, fallback; per-game lot values; per-title item prices | `matcher`, `lots`, `sources.ebay`, `db` |
| `scoring.py` | Total cost, resale profit, discount, deal decisions, rank score for all listing kinds | `models`, `config` |
| `vision.py` | Photo download/resize, Claude request, response validation | `anthropic`, `pillow`, `models` |
| `budget.py` | Scan gate: on/off switch, price filter, daily/monthly caps, spend tracking | `db`, `config` |
| `notify.py` | Decide which finds to send; Discord webhook delivery and dedupe | `httpx`, `db`, `config` |
| `pipeline.py` | One refresh run: search → classify → value → score → scan → store → notify | all of the above |
| `db.py` | SQLite schema and queries (plain `sqlite3`) | — |
| `config.py` | Settings from `config.yaml` + env vars, with defaults | `pyyaml` |
| `web/` | FastAPI app, routes, templates | `db`, `catalog`, `pipeline`, `budget` |

Source adapters, the classifier, matchers and the vision response parser are
pure enough to test without a network: adapters expose `parse()` functions
that tests call directly with fixtures, and `vision.py` takes the Anthropic
client as a parameter so tests pass a fake.

## 5. Catalogs

Seed files ship in `catalog/` and are never edited by the app. User changes
made in the dashboard (enable/disable, max price, repair assumptions, custom
entries) are stored in SQLite and layered on top, so seed files can be
updated without losing user settings.

### 5.1 Single-item products (`gpus.yaml`, `cpus.yaml`, `consoles.yaml`, `controllers.yaml`)

```yaml
- id: rtx-4070-ti-super
  category: gpu
  name: GeForce RTX 4070 Ti Super
  ebay_query: "rtx 4070 ti super"
  match: '\b(rtx\s*)?4070\s*ti\s*super\b'
  exclude: []            # product-specific, added to category + global excludes
  max_price: null        # optional personal target, in USD
```

Starter coverage:

- **GPUs:** NVIDIA RTX 30, 40 and 50 series including Ti / Super variants and
  VRAM variants where both exist (e.g. RTX 3060 8GB vs 12GB, RTX 5060 Ti 8GB
  vs 16GB). NVIDIA RTX only — no AMD Radeon or Intel Arc cards (scope
  narrowed by the user on 2026-10-22).
- **CPUs:** Intel Core 12th, 13th and 14th gen (i5/i7/i9, K/KF/F variants
  distinct), Intel Core Ultra 200S desktop; AMD Ryzen 7000 and 9000 desktop
  (X vs X3D distinct), plus Ryzen 5000 X3D.
- **Limited-edition consoles and controllers:** a hand-curated starter list
  (e.g. PlayStation 30th Anniversary PS5 / PS5 Pro / DualSense, Xbox Series X
  Halo Infinite edition, Switch OLED Zelda: Tears of the Kingdom and Pokémon
  Scarlet & Violet editions, God of War Ragnarök and Spider-Man 2 DualSense
  editions). Every entry is verified to be a real release when written.
- **Collectible limited-edition consoles, any generation** (added by the user
  on 2026-10-22): any limited or special-edition console run with strong
  resale value, starting with the Xbox 360 Halo 3 Special Edition. Examples
  to evaluate include Xbox 360 Halo Reach / Halo 4 / Star Wars / Gears of War 3
  editions, original Xbox Halo Special Edition, PS4 20th Anniversary Edition,
  N64 Pikachu Edition, and Pokémon / Zelda handheld editions. An entry is added
  only when it is confirmed real and recent eBay sold prices show strong
  resale (well above a standard console of the same model). Patterns must
  never match game-only listings that share the name (e.g. "Halo 3" the game,
  "Halo 3 Legendary Edition" the game collector's box).

### 5.2 Bulk-lot products (`bulk.yaml`)

```yaml
- id: ps2-console
  category: console
  name: PlayStation 2 console
  ebay_query: "ps2 console"
  match: '\b(ps2|playstation\s*2)\b.*\b(console|system|slim|fat|scph)\b'
  repair_cost: null      # null = category default
  success_rate: null     # null = category default
```

- **Consoles across generations:** PS1–PS5, PSP / PS Vita, Xbox through
  Series X|S, GameCube, Wii, Wii U, Switch, DS / 3DS families.
- **Standard controllers** for those consoles.
- **GPUs and CPUs:** every product in the single-item catalog is also a bulk
  product automatically, with the category defaults.
- Console and controller patterns require a hardware word (console, system,
  slim, model number, controller, DualShock, Joy-Con, gamepad), so a
  "PS2 game lot" never matches a console.
- Category defaults (editable): console $25 / 60%, controller $8 / 70%,
  GPU $40 / 40%, CPU $10 / 30% (repair cost per unit / success rate).

### 5.3 Game platforms (`platforms.yaml`)

```yaml
- id: ps2
  name: PlayStation 2
  match: '\b(ps2|playstation\s*2)\b'
  ebay_lot_query: "ps2 game lot"
```

Covers NES, SNES, N64, GameCube, Wii, Wii U, Switch, Game Boy / Color /
Advance, DS, 3DS, Genesis, Saturn, Dreamcast, Game Gear, PS1–PS5, PSP,
PS Vita, Xbox, Xbox 360, Xbox One, Xbox Series X|S, Atari 2600. Look-alike
platforms (DS vs 3DS, Xbox vs Xbox 360 vs Xbox One, PS2 vs PS3) are separated
by the "most specific match wins" rule in section 6.

## 6. Classification and matching

The classifier and matchers are the main correctness risk and get the most
tests.

**6.1 Normalize** the title: lowercase, collapse whitespace, split joined
model tokens (`rtx4090` → `rtx 4090`, `7800x3d` stays one token), unify
`super` / `ti` spacing.

**6.2 Quantity parser** (`text.py`): reads a unit count from "lot of 5",
"5x", "x5", "(5)", "qty 5", "5 consoles", "20 games", and number words up to
twenty ("five ps2 consoles"). Platform and model names are masked before
parsing, so "PlayStation 2 console" or "Xbox 360 controller" never read as a
count. Returns the count; `unknown` when the title has a lot word (lot,
box of, collection, bulk, wholesale) but no number; otherwise 1. "Bundle" is
not a lot word: limited editions are often sold as bundles.

**6.3 Routing** — each listing goes down exactly one path:

1. If the title names a platform and a game word (games, carts, cartridges,
   discs) and no hardware word → **game lot path**.
2. Else if the quantity is ≥ 2 or `unknown` → **hardware lot path**.
   A title with both games and a console ("PS2 console with 10 games") is
   skipped: it belongs to neither lot type.
3. Else → **single-item path**.

**6.4 Single-item path**

1. **Global excludes** (any match rejects): for parts, parts only, not
   working, broken, untested, as is / as-is, box only, empty box, no gpu /
   no cpu, bent pins, engineering sample, `\bES\b`, `\bQS\b`, read
   description.
2. **Category excludes:** GPUs — laptop, notebook, prebuilt, gaming pc,
   desktop pc, bundle, waterblock / water block, backplate, shroud, fan only,
   bracket, cooler only. CPUs — motherboard combo, combo, mobo, bundle,
   laptop, cooler only. Consoles/controllers — case only, skin, faceplate
   only, shell only, stand only, charging station. ("Bundle" is not excluded
   for consoles: some limited editions are sold as bundles.)
3. **Identify product:** test every enabled product's `match` pattern.
   - No match → reject.
   - Several matches where one is strictly more specific (its match span
     contains the others', e.g. "4070 Ti Super" vs "4070 Ti") → take the most
     specific.
   - Several unrelated matches (e.g. "3080 or 3090") → reject as ambiguous.
4. **Condition:** eBay — from the API's condition ID (new / open box → `new`,
   used / refurbished → `used`; "for parts" is already rejected). Other
   sites — `new` if the title says sealed, new in box, NIB, BNIB, brand new;
   otherwise `used`.

**6.5 Hardware lot path**

1. Excludes: shell only, case only, housing only, box only / boxes only,
   empty box, faceplate only, game(s) only. "For parts", "broken",
   "untested", "as-is" and "lot of" are allowed.
2. Identify one bulk product with the same specificity rules as 6.4. Titles
   matching two unrelated bulk products (e.g. PS2 and Xbox) → skip as mixed.
   Extras such as cables or "with controllers" are ignored for value.
3. Quantity ≥ `min_hardware_lot` (default 3) → hardware lot. Quantity
   `unknown` → photo-scan candidate only (section 11). Otherwise reject.

**6.6 Game lot path**

1. Excludes (working only): for parts, not working, broken, untested,
   damaged, cracked, scratched, resurface, as is / as-is, case(s) only,
   manual(s) only, inserts only, empty case, box only, repro, reproduction.
2. Identify one platform (most specific wins); two unrelated platforms →
   skip as mixed.
3. Quantity ≥ `min_game_lot` (default 5) → game lot. Quantity `unknown` →
   photo-scan candidate only. Otherwise reject.

## 7. Market values

All values come from eBay sold listings (sold + completed filters, US),
parsed for title, sold price, shipping, sold date and condition label.
Outliers are trimmed with the IQR rule (drop values outside Q1 − 1.5·IQR and
Q3 + 1.5·IQR) before taking the median of item price + shipping. Every value
is stored with the comps behind it so the dashboard can show them.

| Value | Query | Comps kept | Min comps for full confidence | Cache |
|---|---|---|---|---|
| Single product, per condition | product `ebay_query` | titles that match the same product and condition via 6.4 | 5 | 24 h |
| Bulk product, working used | bulk `ebay_query` | titles matching the bulk product, used, not a lot | 5 | 24 h |
| Per-game price, per platform | platform `ebay_lot_query` | game lots for that platform with a readable count; value = price ÷ count | 5 | 24 h |
| Photo-identified item, per completeness | "platform + title" (+ "complete" / "cib" when CIB) | titles containing the platform and all significant title words; no lot, case/manual/box only, repro | 3 | 30 days |

Below the minimum comp count a value is marked `low_confidence`. If the sold
page fails or yields no comps for a single or bulk product, fall back to the
median of current fixed-price eBay listings from the Browse API, labeled
`method = active_bin` and always `low_confidence`. With no value at all,
single items can still be target hits but get no resale score; lots without
a value are not scored.

## 8. Scoring

Common to every listing (`price` = current bid or fixed price):

```
total_cost = price + shipping + price * buyer_premium_pct + handling_fee
             (+ price * tax_rate)
```

- `buyer_premium_pct` comes from the listing when the site reports it (HiBid
  auctions do); otherwise the per-source default from config.
- `handling_fee` is a per-source default from config (ShopGoodwill charges
  handling; eBay 0).
- Shipping: from the listing when reported. If shipping is offered but the
  cost is unknown, use `buy_shipping_estimate[category]` and mark it
  estimated (game lots: `lot_ship_base + lot_ship_per_game × qty`).
  Pickup-only listings are dropped before scoring.

**8.1 Single items**

```
resale_net   = market_value * (1 - sell_fee_rate) - sell_shipping[category]
profit       = resale_net - total_cost
discount_pct = 1 - total_cost / market_value
```

Deal if **target hit** (`max_price` set and `total_cost <= max_price`) or
**resale deal** (`profit >= min_profit` and `discount_pct >= min_discount`).

**8.2 Bulk hardware lots** (repair and resell)

```
unit_net         = working_used_value * (1 - sell_fee_rate) - sell_shipping[category]
expected_revenue = qty * success_rate * unit_net
repair_cost      = qty * repair_cost_per_unit      # attempted on every unit
profit           = expected_revenue - repair_cost - total_cost
```

Units that can't be repaired count as $0. Deal if `profit >= min_lot_profit`.

**8.3 Game lots**

```
resale_net   = qty * per_game_value * (1 - sell_fee_rate)
               - (lot_ship_base + lot_ship_per_game * qty)
profit       = resale_net - total_cost
discount_pct = 1 - total_cost / (qty * per_game_value)
```

Deal if `profit >= min_lot_profit` and `discount_pct >= min_discount`.

**8.4 Ranking.** Rank score = `max(profit, 0) * time_weight`, with target
hits and photo finds listed first. `time_weight` is 1.0 for fixed-price
listings and for auctions ending within 12 hours, 0.7 within 48 hours, 0.4
beyond that, so early cheap bids don't crowd out auctions about to close.

**8.5 Defaults** (all in `config.yaml`): `sell_fee_rate` 0.1325;
`sell_shipping` and `buy_shipping_estimate` gpu 20 / cpu 8 / console 30 /
controller 10; `lot_ship_base` 8, `lot_ship_per_game` 0.40; `min_profit` 30;
`min_lot_profit` 50; `min_discount` 0.20; `min_hardware_lot` 3;
`min_game_lot` 5.

## 9. Search strategy

- **eBay:** one Browse API search per enabled single-item product, one
  "<bulk ebay_query> lot" search per enabled bulk product, and one
  `ebay_lot_query` search per enabled platform, US only, category-restricted
  where the category ID is known. With roughly 200 queries in total,
  running all of them every 30 minutes would exceed the API's ~5,000
  calls/day, so eBay searches rotate: each refresh covers a slice, and a full
  pass completes every `ebay_full_pass_hours` (default 3, about 1,600
  calls/day plus item-detail calls for photo scans).
- **ShopGoodwill and HiBid:** a fixed set of broad queries defined in config
  (e.g. "rtx", "geforce", "ryzen", "intel core", "ps5",
  "xbox series", "switch oled", "dualsense", "limited edition controller",
  "limited edition console", "special edition console",
  "console lot", "controller lot", "video game lot", "games lot"). Every
  returned listing is classified locally.
- Listings are upserted by `(source, source_id)`. `first_seen` drives "new
  since last visit"; listings past their end time are hidden.

## 10. Bulk-lot and game-lot specifics

- Hardware lots value against the bulk product's **working used** value
  (section 7), because the plan is to repair and sell working units.
- GPU/CPU lots use the single-item market value (used condition) of that
  product.
- Game lots show cost per game next to market value per game.
- Known blind spot of the text-only valuation: one rare game can make a lot
  worth far more than the per-game average. Photo scanning (section 11)
  covers this when switched on.

## 11. Photo scanning

**11.1 Candidates.** Every hardware lot and game lot, plus lot-path listings
whose quantity is `unknown`, that has not been scanned before and has not
ended.

**11.2 Gate** (`budget.py`). A candidate is scanned automatically only if
all of these hold:

- `scan_enabled` is on. **It is off by default** and toggled from the
  dashboard header, taking effect immediately.
- `total_cost <= max_scan_price` (default $300).
- Spend today + `scan_cost_ceiling` (default $0.40, the most one scan is
  assumed to cost) ≤ `daily_cap` (default $5), and the same for the month
  against `monthly_cap` (default $50).

A **"Scan this lot"** button on any candidate scans it on demand, ignoring
the on/off switch and the price filter but still respecting both caps. Caps
reset at local midnight and on the 1st of the month. The dashboard header
shows "spent today / this month" next to the switch, and both caps and the
price filter are editable there.

**11.3 Request** (`vision.py`).

- Download up to 8 listing photos (eBay item detail images; ShopGoodwill and
  HiBid image URLs from the listing), resize to at most 1568 px on the long
  edge with Pillow, send as base64 JPEG in one Messages API request through
  the official `anthropic` Python SDK.
- Model `claude-opus-5-5`, effort `medium` set explicitly, structured output
  (`output_config.format` JSON schema), `max_tokens` 16000, non-streaming.
- Server-side fallback on (`server-side-fallback-2026-07-01`,
  `fallbacks: "default"`), so a declined request is retried on another model.
  A final `stop_reason` of `refusal` marks the scan failed; it is not
  retried automatically.
- Prompt: identify every game, console and controller visible; use only what
  is readable in the photos, never guess a title from cover art alone; flag
  special and limited editions; count units for lots with no stated count.

**11.4 Response schema.**

```json
{
  "items": [
    {
      "kind": "game | console | controller | accessory | other",
      "platform": "string",
      "title": "string",
      "edition": "string | null",
      "completeness": "loose | cib | sealed | unknown",
      "quantity": 1,
      "confidence": "high | medium | low",
      "photo_index": 0
    }
  ],
  "unit_count_estimate": "integer | null",
  "notes": "string"
}
```

**11.5 Cost tracking.** Each response's `usage` (input and output tokens,
including any fallback model's usage) is multiplied by per-model prices from
config (Opus 5.5: $4 / $20 per million tokens) and recorded in
`vision_spend`. The caps are checked against these recorded amounts.

**11.6 Valuation and alerts.** Each identified item is priced per section 7
(photo-identified items). Only high- and medium-confidence items with a
non-low-confidence price count toward alerts:

- **Rare find:** any single item worth ≥ `rare_item_threshold` (default $50).
- **Lot find:** the sum of counted item values ≥ 1.5 × total cost and
  ≥ total cost + `min_lot_profit`.

Low-confidence items are still shown on the dashboard. For a lot with an
`unknown` quantity, `unit_count_estimate` fills in the quantity so it can
also be scored per section 8.

**11.7 Backstop.** The README explains how to set a monthly spend limit on
the API key in the Anthropic Console, so even an app bug cannot spend past
it.

## 12. Discord notifications

- Delivered to `DISCORD_WEBHOOK_URL` as an embed: title linked to the
  listing, photo thumbnail, total cost, time left, and why it was flagged
  (e.g. rare items found with their values, or profit and discount).
- Reasons and defaults: `photo_find` on, `target_hit` on, `hardware_lot_deal`
  on, `game_lot_deal` on, `resale_deal` off. All configurable.
- Each listing notifies at most once per reason (`notifications` table).
- Sends are spaced at least 1 second apart; a 429 from Discord waits the
  returned `retry_after` and retries once. Failures are logged and shown in
  the status banner; they never stop a refresh.
- Missing webhook URL disables notifications with a dashboard message.

## 13. Storage (SQLite)

- `listings` — source, source_id, kind (single / hardware_lot / game_lot /
  scan_only), product_id or bulk_product_id or platform_id, condition,
  quantity, title, url, image_urls, price, shipping, shipping_estimated,
  buyer_premium_pct, handling_fee, is_auction, end_time, bid_count,
  first_seen, last_seen, dismissed, plus computed total_cost, profit,
  discount_pct, is_target_hit, is_deal, rank_score.
- `market_values` — key (product / bulk product / platform), condition,
  value, comp_count, method, low_confidence, computed_at.
- `comps` — market value key, title, price, sold_date, url.
- `item_prices` — platform, title, completeness, value, comp_count,
  low_confidence, computed_at.
- `scans` — listing_id, status, model, items JSON, unit_count_estimate,
  identified_value, is_rare_find, is_lot_find, cost_usd, scanned_at, error.
- `vision_spend` — scanned_at, listing_id, input_tokens, output_tokens,
  cost_usd.
- `notifications` — listing_id, reason, sent_at, status (unique on
  listing_id + reason).
- `product_overrides`, `bulk_overrides`, `platform_overrides` — enabled,
  max_price / repair_cost / success_rate as applicable.
- `custom_products`, `custom_bulk_products` — same fields as seed entries.
- `runs` — started_at, finished_at, source, status, listings_seen, error.
- `settings` — key/value: `last_visit`, `scan_enabled` (false),
  `scheduler_paused` (false), `daily_cap`, `monthly_cap`, `max_scan_price`.

## 14. Dashboard

- **Header (every page)** — photo-scanning on/off switch, spent today / this
  month, editable caps and max scan price, pause/resume scheduler,
  "Refresh now", last successful run per source, and a status banner when a
  source, notification or scan failed.
- **Deals (`/`)** — single items: image, title, product, condition, site,
  total cost (breakdown on hover), market value (confidence badge), profit,
  discount %, time left, 🎯 for target hits, NEW since last visit. Filters:
  category, site, ending within N hours, new only, target hits only.
- **Bulk lots (`/lots/hardware`)** — product, quantity, cost per unit,
  expected profit, the success rate and repair cost used.
- **Game lots (`/lots/games`)** — platform, game count, cost per game vs
  market per game, expected profit. Filters: platform, ending soon.
- **Photo finds** — a filter on both lot tabs; finds are marked 💎.
- **Listing detail** — cost breakdown, the comps behind each value, and for
  scanned lots the photos next to the identified-items table (value and
  confidence per item). "Scan this lot" button when not yet scanned.
- **Catalog (`/catalog`)** — single products, bulk products and platforms:
  enable/disable, max price, repair cost and success rate, current market
  values; forms to add custom products and bulk products.

Every list supports open listing and dismiss. HTMX handles dismiss, filters,
toggles, catalog edits, scans and refresh without page reloads.

## 15. Error handling

- Each source runs independently in a refresh; one failing site does not stop
  the others. Failures are stored in `runs` and shown in the status banner.
- HTTP: timeouts of 20 s, one retry with backoff on network errors and 5xx;
  429 or bot-block pages stop that source for the run and are reported.
- Parse failures on single items are skipped and counted; a response that
  parses to zero items when the site returned data is reported as a probable
  site change.
- Photo scans: a failed photo download skips that photo; a scan with no
  usable photos, an API error, or a refusal is stored as a failed scan with
  the reason and no spend beyond what the API reported. Anthropic SDK errors
  are handled most-specific first (rate limit → retry later, other 4xx →
  fail, connection / 5xx → retry once).
- Missing eBay credentials, Anthropic key or Discord webhook disables only
  that feature, with a clear dashboard message instead of crashing.

## 16. Testing

- **Quantity parser and routing:** table-driven tests for every quantity
  form, unknown counts, game vs hardware vs single routing, console+games
  bundles, mixed lots.
- **Single-item matcher:** variant collisions (4070 Ti vs Ti Super, 7800X vs
  7800X3D, Ryzen 7900X vs a Radeon "RX 7900 XT" title (must not match a CPU),
  3060 8GB vs 12GB), every exclude family,
  ambiguous titles, condition detection.
- **Lot matchers:** platform look-alikes (DS vs 3DS, Xbox vs 360 vs One, PS2
  vs PS3), "PS2 game lot" not matching a console, working-only game
  excludes, for-parts allowed on hardware lots.
- **Adapters:** `parse()` tests on saved fixture responses for eBay Browse,
  eBay item detail, eBay sold page, ShopGoodwill and HiBid; pickup-only
  detection; photo URL extraction.
- **Market values:** IQR trimming, median, confidence thresholds, fallback,
  per-game value from sold lots, photo-item title filtering.
- **Scoring:** single, hardware-lot and game-lot formulas, deal decisions,
  time weighting.
- **Vision:** response parsing with recorded JSON fixtures via a fake
  Anthropic client; refusal handling; image resizing; cost computation from
  usage.
- **Budget gate:** off by default, switch, price filter, daily and monthly
  caps (including the per-scan ceiling), manual scan bypassing switch and
  price filter but not caps, cap resets.
- **Notifier:** embed contents, reason toggles, dedupe, 429 retry, using a
  mocked webhook.
- **Web:** FastAPI `TestClient` smoke tests for each page and HTMX action
  against a temporary database.
- **Pipeline:** an end-to-end refresh using fake sources, a fake Anthropic
  client and a mocked webhook.

Fixtures are built from the sites' documented/observed response shapes, since
live capture isn't possible from the development container. The README tells
the user how to capture a real response to replace a fixture if a site's
format differs. No test calls the real Anthropic API or spends money.

## 17. Out of scope (for now)

Email, push and other notification channels; auto-bidding; sites beyond the
three; pickup-radius filtering; for-parts single items; mixed-platform or
mixed-product lots; console + games bundles; PriceCharting or other paid
price data; multi-user accounts; hosting/deployment; price-history charts.
