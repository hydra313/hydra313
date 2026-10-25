# AGENTS.md: handoff for coding agents (Codex and others)

This file hands the project over to the next agent. Read it before changing
anything.

## What this is

**ConsoleHawk** (Python module `dealfinder`) is a local web app that searches eBay, ShopGoodwill and HiBid
for underpriced items and shows them on a dashboard at http://127.0.0.1:8000.

It looks for:
- **single items:** NVIDIA RTX GPUs, recent Intel/AMD CPUs, and limited-edition
  and collectible consoles and controllers of any generation (e.g. the Xbox 360
  Halo 3 Special Edition);
- **broken hardware lots**, valued as repair-and-resell;

Prices come from eBay sold listings. Paid photo analysis has been removed at the
user’s request. Listing photos remain visible. Selected deals are sent to Discord.

- **Spec (authoritative):** `docs/superpowers/specs/2026-10-22-auction-deal-finder-design.md`
- **Build plan:** `docs/superpowers/plans/2026-10-22-auction-deal-finder.md`
- **Decisions made during the build, plus known minor issues:** `docs/superpowers/build-decisions.md`
- **User-facing setup:** `README.md`

## Current state (as of 2026-10-24)

Windows executable: `desktop/Launcher.cs` builds to the committed
`desktop/ConsoleHawk.exe`; rebuild with `scripts/Build-Launcher.ps1 -Destination ...`
after changing it. This is a .NET Framework WinExe launcher, not a bundled Python
runtime. It discovers a source checkout or reads `App/project-path.txt` beside the
EXE. It runs the existing PowerShell launcher hidden, validates installation files,
and reports startup failure in a message box. `--check` is a silent diagnostic.
Six targeted launcher tests pass; the actual local EXE launch returned 0.
The user's outputs folder is organized into App, Assets, Guides, Screenshots,
Releases and Archive, with ConsoleHawk.exe and START-HERE.md at the top. Preserve
that organization for future exports; don't recreate scattered root artifacts.
Runtime data remains in this checkout, including private browser profiles.

Newest runtime: `python -m dealfinder` now calls `create_app(...,
start_collector=True)`. `collector.py` owns separate persistent Edge profiles per
site and runs public card captures headlessly on a daemon worker. Connections has
Sign in / Resume scanning controls; headed windows open only on that user action.
The worker never reads login forms or automates verification. A blocked site pauses
while the other continues. `/browser/*` retains its local capability check; normal
extension calls now return paused (or reject results) when background mode is on.
Only the built-in worker sends `X-ConsoleHawk-Collector: background`. Keep that
header on local HTTP only, never third-party navigation. No localhost token in logs.
Never publish `.collector-profile/` or `.desktop-profile/`: these are private
browser profiles. Do not share a normal Chrome/Edge user profile. Preserve sandboxing.
Desktop launcher defaults to an app window; `-Browser` opts into a tab, `-NoBrowser`
starts only the server. Windows collector requires installed Microsoft Edge.
Tests explicitly opt into browser smoke tests via `CONSOLEHAWK_BROWSER_SMOKE=1`;
normal `create_app` tests do not launch collectors. Live HiBid background capture
produced 22 usable records. User chose to sign in to eBay later; eBay is paused in
its dedicated sign-in window. Do not claim live background eBay capture is verified.
This supersedes the old required-extension instructions below.


Latest user scope: game lots are removed from the product. `load_catalog()` retains
platform records for masking/console-family boundaries but forces them disabled,
even over old DB overrides. Raw `load_seed()` and legacy pure valuation/scan helpers
remain for archival tests; never expose their game-lot paths in the shipped app.
Discovery excludes game-lot queries (including old custom configuration); game-lot
alert reasons cannot send. Old page bookmarks redirect to Deals; platform override
endpoints return 410. Preserve historical DB rows. Camera/camcorder titles, named laptop families/mobile HX CPUs, and
eGPU/external GPU/XG Mobile docks are rejected by the shared title guard, including
saved listings and contaminated sold caches. `category_compatible` also prevents
gamepads from inheriting console identities; apply it to cached rows and alerts,
not just new matches.


The October 24 refinement in `docs/consolehawk-refinement-2026-10-24.md` supersedes
older photo-scan UI, all-results defaults, fixed-price-only capture and single-page
ShopGoodwill assumptions in the original design. Preserve the existing DB and
connections. Browser helper 1.3 files are installed; Chrome reload/reconnection is
pending because Windows UI automation could not verify its current tab URL.

- Per-item Discord subscriptions live in settings, separate from catalog enablement.
  Catalog Alert me + Connections selected scope + matching reason must all be set.
- Default pages require a fresh good sold-based rating. All-results opt-in remains.
  Deals paginates by 100 and shows hidden/pending counts. Query aliases respect
  console generations. Current sold values determine displayed single-item profit.
- `matcher.unrelated_accessory` is shared by classification, saved rows, sold-cache
  validation and alerts; its pure title cache avoids repeated normalization.
- No production factory creates a vision scanner. Legacy scan/control endpoints
  return 410. Retained budget/vision libraries and historical data are archival;
  never expose or re-enable paid scanning without a new user request.
- Status polling carries a browser-specific content revision, so idle polls do not
  reload the listing table. Per-request valuation caching avoids thousands of
  duplicate comparison queries. Always preserve page/filter state on auto refresh.
- eBay captures auctions with visible countdowns and both listing formats. Browser
  queries prioritize console families, request searches and subscribed products.
  Full pages queue bounded pagination. Source query caps remain documented.


- Branch `claude/auction-deal-scraper-9mgait` has PR #1 into `main`. `main`
  holds only the original spec commit.
- 1,086 Python tests pass; 4 optional real-browser capture checks also pass.
- Public app name is ConsoleHawk. The UI uses olive armor green and warm visor
  orange, with an original SVG hawk mark and favicon. Keep deal-rating semantic
  colors distinct. Internal module, DB filenames, pairing headers and alarm IDs
  retain their existing names for compatibility with installed connections.
- `Start-ConsoleHawk.cmd` / `scripts/Start-ConsoleHawk.ps1` are the named launchers;
  the previous filenames forward to them. Readiness accepts both API titles.
- Installed Chrome helper 1.2 was verified collecting eBay and HiBid automatically.
  Its actual folder is `C:\Downloads\dealfinder-browser-helper`; user-facing output
  copies alone do not update that installed folder. Discord protected connection
  was verified with a read-only HTTP 200; never include the credential in handoffs.
- Category controls submit lowercase database values and normalize old capitalized
  links. eBay discovery queries alternate between GPUs, CPUs, consoles, controllers
  and both lot categories so the larger GPU catalog cannot delay all other searches.
- Sold averages use the arithmetic mean after existing IQR filtering. New
  `MarketValue.method = sold_average` distinguishes them from legacy `sold`
  median caches; no schema reset or deletion is needed. Old medians and active
  asking estimates are explicitly labeled and never get sold-based deal colors.
- `deal_rating.py` supplies green (configured margin + discount), yellow (positive
  margin below targets), red (zero/negative profit after costs), and gray
  (missing/weak/stale/quantity-unknown). Percentages compare landed cost to the
  sold average; lot values multiply unit averages by known quantities.
- `ebay_sold_browser.py` stores factual dated sold snapshots in settings, dedupes
  item IDs, excludes unknown accepted offers and dates outside 90 days, and feeds
  the existing variant/condition matchers. Fresh snapshots override negative or
  asking-price caches. An unrelated HTTP block must not hide local evidence.
- Shared `web/browse.py` handles text, brand, console, condition, price range and
  rating filters plus both sort directions for price/profit/margin/sold/rating/time.
  Unknown values sort last. Options stay usable when HTMX changes categories.
- `/connections` provides per-site helper controls and secure Discord channel setup.
  Discord secrets are DPAPI protected for the current Windows user in ignored
  `*.credentials` files beside SQLite; never render, log, commit or archive them.
  Environment variables remain supported. Saved connection/disconnection takes
  precedence, and notifier config updates work without restarting. Alert preference
  booleans are stored in settings; existing defaults and deduplication stay intact.
- Browser helper 1.2 supports automatic HiBid searches and eBay active/sold capture.
  One site's verification/import failure must not stop another enabled site.
  It alternates enabled sites and eBay active searches and needed sold evidence. Active
  query rotation has a separate index so alternating modes never skip half the
  catalog. Sold-page sign-in is completed by the user; no login data is read.
- A signed-in live sold capture read 242 cards and retained 197 dated records;
  the standard used RTX 4070 match retained 59 after matching/outlier filtering.
  Open-box cards use the existing new/open-box group, and generic `Pickup only`
  cards are excluded, including the current distance-based shipping label.
- Listing checks run every two minutes by default. The status block polls while
  idle (10 s) and running (3 s), shows source/query and counts, and refreshes rows
  incrementally. Runner reports completion and unexpected errors visibly.
- eBay without keys uses `sources/ebay_browser.py` and the optional Chrome/Edge
  helper in `browser-helper/`. Real rendered search validation imported catalog
  matches without API credentials. The helper needs one-time Load unpacked setup;
  the installed Chrome helper has been verified collecting both sites.
- The helper rotates one enabled catalog query every 30 seconds in one owned tab,
  reads rendered USD active cards (auctions require visible countdowns), and stops
  on verification or parsing/import failures. It never solves verification.
  Other eBay tabs cannot submit captures. Pause suppresses new helper queries.
- The exact `/browser/next`, `/browser/ebay/{next,results}` and
  `/browser/hibid/{next,results}` endpoints require the local capability token, stored in an
  ignored `*.browser-token` file beside SQLite. That token grants only those
  ingestion endpoints; normal CSRF rules still apply elsewhere. Do not log or
  commit the token. Setup HTML is no-store and no-referrer.
- eBay cached records preserve observation times and cannot overwrite newer
  captures. Reprocessing them does not count as a fresh price update. The normal
  six-hour stale cutoff applies. Browser imports never initiate scans or alerts;
  scheduled runs retain all existing notification and paid-scan budget gates.
- **The first live run is complete for ShopGoodwill search and detail photos.**
  Explicit price bounds and normalized image paths fixed real adapter failures.
  HiBid browser import is verified: 68 real cards, 3 usable USD lots, 1 catalog
  match. Direct HiBid API and eBay sold requests remain blocked. eBay Browse,
  Anthropic and Discord need credentials for live checks. Keyless active listings
  do not supply auction closing times. Direct sold requests remain blocked;
  signed-in browser captures can supply sold evidence locally.
  See `docs/live-validation-2026-10-23.md` for evidence and remaining limits.
- HiBid defaults to automatic browser-helper collection after one-time connection,
  with 30-minute expiry. Manual import remains a fallback. Version 1.2 requires
  reloading the extension and approving the new HiBid host permission. Live card
  selectors were checked against 69 rendered results on October 24, 2026.
  See `docs/hibid-browser-import.md`; no database reset is needed.
- Windows users can double-click `Start-ConsoleHawk.cmd` after installation.
  It starts a hidden server, waits for readiness and opens the dashboard; repeated
  launches reuse the server. Logs go in the ignored `logs/` folder.
- **Include unpriced and lower-margin listings** is an explicit opt-in, including
  unpriced listings and unknown-quantity lots. Unchecking it submits an explicit
  false value. Refresh completion updates the current filtered rows.
  Explicit Intel/AMD conflicts reject CPU matches, including hardware lots.
- The UI uses dark olive surfaces with armor-green structure and visor-orange accents. Scan budgets
  and source notices expand from the header. Desktop tables scroll within their
  panels with sticky headers/actions; phone layouts use listing cards. The web regression
  tests pass, with desktop and 390px-wide browser checks across the main pages.
- Console coverage is 1,774 identities: 39 curated and 1,735 sourced from the
  dated `catalog/console_editions.json` snapshot. The audit accounts for all 3,734
  candidate reference records; ambiguous bundles, grouped designs and bad platform
  metadata are retained for review. See `docs/console-catalog-audit.md`.
- Description-derived review flags quarantine source records under authenticity
  investigation, unofficial modifications and unreleased/prototype hardware even
  if the source's release label says official. Full descriptions are not stored.
- `console_editions.py` keeps hardware-family patterns separate from edition
  tokens. Promotional entries require stronger title evidence. Do not bypass
  these guards or turn every reference bundle into a distinct premium console.
  `Product.required_tokens`, `promotional`, `source_url` and `discovery_query`
  support this path; legacy/custom regex products retain their old behavior.
- Catalog search and 200-product pagination keep the larger catalog usable.
  New reference editions share eBay discovery queries per hardware family.
  Run `scripts/audit-console-catalog.py` to export dispositions/watchlist to
  ignored `.audit/`; `scripts/update-console-reference.py` rebuilds the factual
  snapshot from a downloaded public index. No runtime metadata download occurs.

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

API secrets come from environment variables; Discord also supports protected local
Connections setup on Windows. Each credential is optional and
disables only its own API feature: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`,
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
| HiBid | `dealfinder/sources/hibid_browser.py` (default), `hibid.py` (legacy API) | Browser card `SELECTORS`, visible USD/shipping/countdown markup; API remains blocked |
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

1. Direct HiBid API and eBay sold requests are blocked; helper 1.2 automates browser
   capture after one-time connection. Authenticated eBay Browse, Anthropic
   and Discord still need live credential checks. ShopGoodwill is verified
   against captured search and photo responses; ShopGoodwill now paginates up to five pages per query.
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
