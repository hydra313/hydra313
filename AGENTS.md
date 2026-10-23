# AGENTS.md: handoff for coding agents (Codex and others)

This file hands the project over to the next agent. Read it before changing
anything.

## What this is

**dealfinder** is a local web app that searches eBay, ShopGoodwill and HiBid
for underpriced items and shows them on a dashboard at http://127.0.0.1:8000.

It looks for:
- **single items:** NVIDIA RTX GPUs, recent Intel/AMD CPUs, and limited-edition
  and collectible consoles and controllers of any generation (e.g. the Xbox 360
  Halo 3 Special Edition);
- **broken hardware lots**, valued as repair-and-resell;
- **working video game lots.**

Prices come from eBay sold listings. When the user turns it on, Claude
(`claude-opus-5-5`) reads lot photos to spot rare items, under spending caps.
Deals are sent to Discord.

- **Spec (authoritative):** `docs/superpowers/specs/2026-10-22-auction-deal-finder-design.md`
- **Build plan:** `docs/superpowers/plans/2026-10-22-auction-deal-finder.md`
- **Decisions made during the build, plus known minor issues:** `docs/superpowers/build-decisions.md`
- **User-facing setup:** `README.md`

## Current state (as of 2026-10-23)

- Branch `claude/auction-deal-scraper-9mgait` has PR #1 into `main`. `main`
  holds only the original spec commit.
- 840 tests pass with no warnings.
- **The first live run is complete for ShopGoodwill search and detail photos.**
  Explicit price bounds and normalized image paths fixed real adapter failures.
  HiBid and eBay sold pages returned HTTP 403; their live integration remains
  open. eBay Browse, Anthropic and Discord need credentials for live checks.
  See `docs/live-validation-2026-10-23.md` for evidence and remaining limits.
- The dashboard can show unpriced listings and unknown-quantity lots with
  **Show all listings**. Refresh completion updates the current filtered rows.
  Explicit Intel/AMD conflicts reject CPU matches, including hardware lots.

## Commands

```
python -m venv .venv
.venv/bin/pip install -e '.[dev]'      # Windows: .venv\Scripts\pip ...
.venv/bin/pytest -q                    # full suite, ~20 s, no network
.venv/bin/python -m dealfinder         # serves 127.0.0.1:8000; run from the repo root
```

Run from the repo root so `catalog/` (the seed YAML) is found.
`dealfinder.db` (SQLite) is created in the working directory. Optional
settings go in `config.yaml`; copy `config.example.yaml`, and note that unknown
keys raise an error.

Secrets come only from environment variables. Each one is optional and
disables only its own feature: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`,
`ANTHROPIC_API_KEY` and `DISCORD_WEBHOOK_URL`.

## Architecture (data flow)

```
scheduler / "Refresh now"
  └─ Pipeline.refresh()                      dealfinder/pipeline.py
       ├─ sources/*.search(query) → Listing  ebay.py (Browse API), shopgoodwill.py, hibid.py
       ├─ classify(listing, catalog, cfg)    classify.py → single | hardware_lot | game_lot | scan_only | None
       │    ├─ text.py      normalize(), parse_quantity() (masks model numbers so "PS2"/"PCIe x16" aren't counts)
       │    ├─ matcher.py   single items + exclude lists (broken / parts / accessories)
       │    └─ lots.py      hardware lots (bulk.yaml) and game lots (platforms.yaml)
       ├─ market.py         MarketService: values from eBay sold comps (sources/ebay_sold.py), IQR median, caching
       ├─ scoring.py        total cost, profit, discount, deal decision, rank
       ├─ db.py             SQLite schema + all queries (every write commits immediately)
       ├─ notify.py         Discord webhook, deduped per (listing, reason)
       └─ auto photo scans  budget.py (gate + spend) → vision.py (Claude) → finds.py (rare/lot finds)
web/app.py   create_app, Runner (refresh lock, background scans), DefaultPipelineFactory, CSRF guard
web/routes.py + templates/   FastAPI + Jinja2 + vendored htmx (static/htmx.min.js)
catalog.py   loads catalog/*.yaml and layers DB overrides and custom entries on top
config.py    Config dataclass and load_config()
```

## Rules that must not break

These are deliberate. Each one has tests, and reviewers flagged violations as
Critical.

1. **Money safety (photo scanning)**
   - Scanning is **off by default**: the setting `scan_enabled` is absent → off.
   - A scan starts only if `spent + scan_cost_ceiling ($0.50) <= cap` for both
     the daily and the monthly cap.
   - Every scan path runs gate → scan → record spend → save scan row while
     holding the single module-level `budget.SCAN_LOCK`.
   - A paid scan always gets a `scans` row, even on failure, so it is never
     re-billed.
   - If spend can't be recorded, `SpendNotRecorded` stops scanning for that run.
2. **Alerts:** a notification row is claimed (`INSERT OR IGNORE`, unique on
   `(listing_id, reason)`) **before** sending. Ended, dismissed and stale
   listings never alert.
3. **Valuation:** comps must match the same product and condition. Broken,
   for-parts and accessory listings are excluded from single-item matches.
   Look-alike products must land on exactly one product or be rejected:
   4070 Ti vs Ti Super, Ryzen 7900X vs RX 7900 XT, Switch vs Switch 2,
   DS vs 3DS, Xbox vs 360/One/Series.
4. **Isolation:**
   - One site failing never stops the others.
   - One bad listing never aborts its site's run.
   - Zero parsed listings across a run's queries → run status `site_change`,
     shown as a dashboard banner.
5. **Web security:**
   - Non-GET requests need the `HX-Request: true` header, a local `Host`, and a
     local `Origin` if one is sent.
   - Templates autoescape. Never use `|safe` on site data.
   - External URLs go through `routes._http_url`.
6. **Politeness:** at least 2 s between requests to the same host. One shared
   `HttpClient` throttles across threads.
7. **Secrets** never appear in logs, the DB or the HTML. The Discord webhook URL
   is redacted from httpx logs.

## First live run: the main open work

The site adapters keep every URL, field name and selector in a constants block
at the top of each module, so a fix is usually a one-place edit:

| Site | File | What may be wrong |
| --- | --- | --- |
| ShopGoodwill | `dealfinder/sources/shopgoodwill.py` | `SEARCH_URL` / `ITEM_URL`, request body (`build_search_body`), `RESULTS_PATH`, `FIELD_*` names, the Pacific-time `endTime` assumption |
| HiBid | `dealfinder/sources/hibid.py` | `GRAPHQL_URL`, `LOT_SEARCH_QUERY`, `RESULTS_PATH` and field names (`lead`, `lotState`, `pictures`, `buyerPremium`, `shippingOffered`) |
| eBay sold prices | `dealfinder/sources/ebay_sold.py` | the `SELECTORS` dict (covers both the `li.s-item` and `li.s-card` layouts); eBay may bot-block the scrape |
| eBay Browse API | `dealfinder/sources/ebay.py` | documented API, lowest risk; needs production keys |

Steps:
1. Run the app with network access and watch the header banners.
2. For a site that shows "error" or "probable site change", capture a real
   response with browser dev tools (Network tab → copy response). Save it as a
   fixture in `tests/fixtures/<site>/`.
3. Fix the constants or parser and add a test that parses the real fixture.
   Don't weaken the existing tests; replace any fixture assumptions that were
   wrong.
4. Check classification on real titles. If real listings are misrouted, add
   test rows in `tests/test_seed_catalog.py` / `tests/test_classify.py` first,
   then adjust the patterns in `catalog/*.yaml` or the excludes in
   `matcher.py` / `lots.py`.

## Conventions

- **TDD:** write the failing test first. Tests never touch the network: use
  `respx` / `httpx.MockTransport` for HTTP and fake objects for the Anthropic
  client.
- **Catalog patterns:** catalog YAML `match` / `exclude` regexes run against
  `text.normalize(title)`, which is lowercased with punctuation split.
  `w/` → `with`, GPU suffixes are split (`4070ti` → `4070 ti`), and CPU tokens
  stay joined (`7800x3d`, `13700kf`).
- **Catalog layering:** the seed YAML is never edited by the app. User changes
  live in the DB override and custom tables.
- **Claude API:**
  - The call is `client.beta.messages.create(model="claude-opus-5-5",
    max_tokens=16000, betas=["server-side-fallback-2026-07-01"],
    fallbacks="default", output_config={"effort": "medium", "format":
    json_schema})`.
  - Don't add `thinking` or sampling params.
  - Requires `anthropic>=2.1`.
- **Timestamps:** stored as ISO-8601 UTC. Spending caps reset at local
  midnight and on the 1st of the month.
- **Schema:** changes go in `db.connect()`'s `CREATE TABLE IF NOT EXISTS`.
  There is no migration system yet, so tell users to delete an old
  `dealfinder.db`.
- **Commits:** keep them small and descriptive. Don't commit `dealfinder.db*`,
  `.venv/` or `config.yaml` (all gitignored).

## Known gaps (prioritized)

1. HiBid and eBay sold requests are blocked; authenticated eBay Browse, Anthropic
   and Discord still need live credential checks. ShopGoodwill is verified
   against captured search and photo responses; adapters fetch one page only.
2. GPU/CPU hardware lots still take their quantity from the photo-wide unit
   estimate, which can include accessories.
3. There is no "retry scan" for failed or refused scans in the UI.
4. A manual scan that is declined after "Scanning…" shows nothing.
5. The scan-failure banner is unbounded.
6. Some conservative false negatives:
   - "no power cable included" is rejected;
   - "console with manual, only…" is rejected;
   - limited editions "with original dock" are skipped.

The full list is in `docs/superpowers/build-decisions.md` under "Known minor
issues".
