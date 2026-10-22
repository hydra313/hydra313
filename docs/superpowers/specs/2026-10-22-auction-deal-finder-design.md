# Auction Deal Finder — Design

Date: 2026-10-22
Status: Approved in conversation, awaiting written-spec review

## 1. Goal

A personal, locally run web app that searches eBay, ShopGoodwill and HiBid for
underpriced **recent-generation CPUs, graphics cards, and limited-edition game
consoles and controllers**, and shows them in a ranked dashboard.

It serves two uses at once:

- **Buying for myself** — flag any listing whose total cost is at or below a
  max price I set for a product ("target hit").
- **Flipping** — flag listings whose total cost is well below the product's
  recent eBay sold value, after selling fees and shipping ("resale deal").

Success looks like: opening the dashboard shows a short, trustworthy list of
real deals on the exact models I care about, with the evidence (sold comps)
behind each price estimate, and without junk (empty boxes, broken parts,
laptops, wrong variants).

## 2. Decisions from brainstorming

| Topic | Decision |
|---|---|
| Purpose | Both price-target alerts and resale-value scoring |
| Sites | eBay, ShopGoodwill, HiBid |
| Interface | Local web dashboard (no hosting, no accounts, single user) |
| Item scope | Recent CPUs, GPUs, limited-edition consoles and controllers |
| Condition | Working items only; new/sealed and used valued separately |
| Pickup-only lots | Excluded — shipped items only |
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
- Polite use only: at least 2 seconds between requests to the same site, a
  normal browser user agent, no parallel hammering, no auto-bidding.
- Sales tax is excluded from cost by default (configurable `tax_rate`, default 0).

## 4. Architecture

```
            ┌──────────── scheduler (every 30 min, or "Refresh now") ────────────┐
            │                                                                     │
  catalog ──┼─► source adapters ──► Listing ──► matcher ──► scorer ──► SQLite ◄───┼── web dashboard
  (YAML +   │   (ebay, shopgoodwill,           (product +   (cost,                │   (FastAPI +
   DB       │    hibid)                         condition)   profit,              │    Jinja2 + HTMX)
  overrides)│                                                rank)                │
            │   market value service ── eBay sold page ──► matcher ──► comps ─────┘
            └─────────────────────────────────────────────────────────────────────┘
```

Package layout (`dealfinder/`):

| Module | Responsibility | Depends on |
|---|---|---|
| `models.py` | `Listing`, `Product`, `MarketValue`, `Deal` dataclasses | — |
| `catalog.py` | Load seed YAML, apply DB overrides and custom products | `models`, `db` |
| `matcher.py` | Title normalization; map a title to one product + condition, or reject it | `models` |
| `sources/base.py` | `Source` protocol: `search(query) -> list[Listing]`; shared rate-limited HTTP client | `httpx` |
| `sources/ebay.py` | Browse API search + OAuth token caching | `base` |
| `sources/shopgoodwill.py` | ShopGoodwill search endpoint | `base` |
| `sources/hibid.py` | HiBid GraphQL search | `base` |
| `market.py` | Sold-comps fetch/parse, outlier trimming, median, caching, fallback | `matcher`, `sources.ebay`, `db` |
| `scoring.py` | Total cost, resale profit, discount, deal decision, rank score | `models`, `config` |
| `pipeline.py` | One refresh run: search → match → value → score → store | all of the above |
| `db.py` | SQLite schema and queries (plain `sqlite3`) | — |
| `config.py` | Settings from `config.yaml` + env vars, with defaults | `pyyaml` |
| `web/` | FastAPI app, routes, templates | `db`, `catalog`, `pipeline` |

Each source adapter and the matcher are pure enough to test without a network:
adapters take raw response bodies through a `parse()` function that tests call
directly with fixtures.

## 5. Product catalog

Seed catalog files ship in `catalog/` (`gpus.yaml`, `cpus.yaml`,
`consoles.yaml`, `controllers.yaml`). A product entry:

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
  vs 16GB); AMD RX 6000, 7000 and 9000 series (XT vs XTX kept distinct);
  Intel Arc B-series.
- **CPUs:** Intel Core 12th, 13th and 14th gen (i5/i7/i9, K/KF/F variants
  distinct), Intel Core Ultra 200S desktop; AMD Ryzen 7000 and 9000 desktop
  (X vs X3D distinct), plus Ryzen 5000 X3D.
- **Limited-edition consoles and controllers:** a hand-curated starter list
  (e.g. PlayStation 30th Anniversary PS5 / PS5 Pro / DualSense, Xbox Series X
  Halo Infinite edition, Switch OLED Zelda: Tears of the Kingdom and Pokémon
  Scarlet & Violet editions, God of War Ragnarök and Spider-Man 2 DualSense
  editions). Every entry is verified to be a real release when written.

User changes made in the dashboard (enable/disable, max price, custom products)
are stored in SQLite and layered over the seed files, so seed files can be
updated without losing user settings. A custom product uses the same fields as
a seed entry.

## 6. Matching

The matcher is the main correctness risk and gets the most tests.

1. **Normalize** the title: lowercase, collapse whitespace, split joined model
   tokens (`rtx4090` → `rtx 4090`, `7800x3d` stays one token), unify `super`
   / `ti` spacing.
2. **Global excludes** (any match rejects the listing): for parts, parts only,
   not working, broken, untested, as is / as-is, box only, empty box, no
   gpu/no cpu, bent pins, engineering sample, `\bES\b`, `\bQS\b`, read
   description, lot of, `x2`/`x3` quantity markers.
3. **Category excludes:** GPUs — laptop, notebook, prebuilt, gaming pc,
   desktop pc, bundle, waterblock / water block, backplate, shroud, fan only,
   bracket, cooler only. CPUs — motherboard combo, combo, mobo, bundle, laptop,
   cooler only.
   Consoles/controllers — case only, skin, faceplate only, shell only,
   stand only, charging station. ("Bundle" is not excluded here: some
   limited editions are sold as bundles.)
4. **Identify product:** test every enabled product's `match` pattern.
   - No match → reject.
   - Several matches where one is strictly more specific (its match span
     contains the others', e.g. "4070 Ti Super" vs "4070 Ti") → take the most
     specific.
   - Several unrelated matches (e.g. "3080 or 3090") → reject as ambiguous.
5. **Condition:** eBay — from the API's condition ID (new / open box → `new`,
   used / refurbished → `used`; "for parts" is already rejected). Other sites —
   `new` if the title says sealed, new in box, NIB, BNIB, brand new; otherwise
   `used`.

## 7. Market value

Per `(product, condition)`:

1. Fetch eBay's sold-listings page for the product's `ebay_query`
   (sold + completed filters, US). Parse title, sold price, shipping, sold
   date, condition label.
2. Run each sold title through the matcher; keep only comps that match this
   product and condition.
3. Trim outliers with the IQR rule (drop values outside Q1 − 1.5·IQR and
   Q3 + 1.5·IQR); value = median of the remaining sold prices (item price +
   shipping).
4. Fewer than 5 comps after trimming → mark `low_confidence`.
5. If the sold page fails or yields no comps → fallback to the median of
   current fixed-price ("Buy It Now") eBay listings for the product from the
   Browse API, labeled `method = active_bin` and always `low_confidence`.
6. Cache for 24 hours in SQLite along with the comps used, so the dashboard
   can show them.

If neither source gives a value, the product has no market value; its
listings can still be target hits but get no resale score.

## 8. Scoring

For each matched listing (`price` = current bid or fixed price):

```
total_cost   = price + shipping + price * buyer_premium_pct + handling_fee
               (+ price * tax_rate)
resale_net   = market_value * (1 - sell_fee_rate) - sell_shipping[category]
profit       = resale_net - total_cost
discount_pct = 1 - total_cost / market_value
```

- `buyer_premium_pct` comes from the listing when the site reports it (HiBid
  auctions do); otherwise the per-source default from config.
- `handling_fee` is a per-source default from config (ShopGoodwill charges
  handling; eBay 0).
- Shipping: from the listing when reported. If shipping is offered but the
  cost is unknown, use `buy_shipping_estimate[category]` and mark it
  estimated. Pickup-only listings are dropped before scoring.
- Defaults (all in `config.yaml`): `sell_fee_rate` 0.1325, `sell_shipping`
  gpu 20 / cpu 8 / console 30 / controller 10, `buy_shipping_estimate` same
  values, `min_profit` 30, `min_discount` 0.20.

A listing is a **deal** if either:

- **Target hit:** a max price is set and `total_cost <= max_price`, or
- **Resale deal:** `profit >= min_profit` and `discount_pct >= min_discount`.

**Rank score** = `max(profit, 0) * time_weight`, with target hits always
listed first. `time_weight` is 1.0 for fixed-price listings and for auctions
ending within 12 hours, 0.7 within 48 hours, 0.4 beyond that, so early cheap
bids don't crowd out auctions about to close.

## 9. Search strategy

- **eBay:** one Browse API search per enabled product (`ebay_query`, US,
  category-restricted where the category ID is known), well within the API's
  ~5,000 calls/day.
- **ShopGoodwill and HiBid:** a fixed set of broad queries per category
  (e.g. "rtx", "geforce", "radeon", "ryzen", "intel core", "ps5", "xbox
  series", "switch oled", "limited edition controller", "dualsense"), defined
  in config. Every returned listing is matched locally against the catalog.
- Listings are upserted by `(source, source_id)`. `first_seen` drives "new
  since last visit"; listings past their end time are hidden.

## 10. Storage (SQLite)

- `listings` — source, source_id, product_id, condition, title, url,
  image_url, price, shipping, shipping_estimated, buyer_premium_pct,
  handling_fee, is_auction, end_time, bid_count, first_seen, last_seen,
  dismissed, plus computed total_cost, profit, discount_pct, is_target_hit,
  is_deal, rank_score.
- `market_values` — product_id, condition, value, comp_count, method,
  low_confidence, computed_at.
- `comps` — product_id, condition, title, price, sold_date, url.
- `product_overrides` — product_id, enabled, max_price.
- `custom_products` — same fields as a seed catalog entry.
- `runs` — started_at, finished_at, source, status, listings_seen, error.
- `settings` — key/value (e.g. `last_visit`).

## 11. Dashboard

- **Deals (`/`)** — ranked table: image, title, product, condition, site,
  total cost (with breakdown on hover), market value (with a confidence badge),
  profit, discount %, time left, 🎯 for target hits, NEW badge since last
  visit. Filters: category, site, ending within N hours, new only, target hits
  only. Actions: open listing, dismiss.
- **Deal detail** — cost breakdown and the sold comps behind the market value.
- **Catalog (`/catalog`)** — products grouped by category: enable/disable,
  set max price, current market values; form to add a custom product.
- **Status** — banner when the last run of any source failed, with the error;
  "Refresh now" button; time of last successful run per source.

HTMX handles dismiss, filters, catalog edits and refresh without page reloads.

## 12. Error handling

- Each source runs independently in a refresh; one failing site does not stop
  the others. Failures are stored in `runs` and shown in the status banner.
- HTTP: timeouts of 20 s, one retry with backoff on network errors and 5xx;
  429 or bot-block pages stop that source for the run and are reported.
- Parse failures on single items are skipped and counted; a response that
  parses to zero items when the site returned data is reported as a probable
  site change.
- Missing eBay credentials disable the eBay source with a clear dashboard
  message instead of crashing.

## 13. Testing

- **Matcher:** table-driven tests with real-world-style titles covering
  variant collisions (4070 Ti vs Ti Super, 7800X vs 7800X3D, 7900 XT vs XTX,
  3060 8GB vs 12GB), every exclude family, ambiguous titles, condition
  detection.
- **Adapters:** `parse()` tests on saved fixture responses for eBay Browse,
  eBay sold page, ShopGoodwill and HiBid; pickup-only detection.
- **Market value:** IQR trimming, median, low-confidence threshold, fallback.
- **Scoring:** cost, profit, discount, deal decision and time weighting.
- **Web:** FastAPI `TestClient` smoke tests for each page and HTMX action
  against a temporary database.
- **Pipeline:** an end-to-end refresh using fake sources that return fixtures.

Fixtures are built from the sites' documented/observed response shapes, since
live capture isn't possible from the development container. The README tells
the user how to capture a real response to replace a fixture if a site's
format differs.

## 14. Out of scope (for now)

Email/push alerts, auto-bidding, sites beyond the three, pickup-radius
filtering, for-parts/repair deals, multi-user accounts, hosting/deployment,
price-history charts.
