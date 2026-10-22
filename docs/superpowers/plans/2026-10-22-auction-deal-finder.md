# Auction Deal Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web app that finds underpriced CPUs, GPUs, limited-edition consoles/controllers, broken hardware lots and working game lots on eBay, ShopGoodwill and HiBid, optionally scans lot photos with Claude for rare items, and sends finds to Discord.

**Architecture:** A `dealfinder` Python package. Site adapters turn searches into `Listing`s; a classifier routes each listing to single-item, hardware-lot or game-lot matching against YAML catalogs; a market service prices them from eBay sold listings; a scorer decides deals; everything lands in SQLite. A FastAPI + Jinja2 + HTMX dashboard reads SQLite and an in-process APScheduler job runs refreshes. Photo scanning and Discord are gated side-paths.

**Tech Stack:** Python ≥ 3.11, FastAPI, Uvicorn, Jinja2, HTMX 2.0.4 (unpkg, pinned), python-multipart, httpx, selectolax, PyYAML, APScheduler 3.x, Pillow, anthropic (official SDK); tests with pytest and respx.

**Spec:** `docs/superpowers/specs/2026-10-22-auction-deal-finder-design.md` — read it alongside this plan; section numbers below (§n) refer to it.

## Global Constraints

- Python ≥ 3.11; package name `dealfinder`; entry point `python -m dealfinder` serving on `127.0.0.1:8000`.
- No test touches the network or the real Anthropic API. HTTP is mocked with respx or `httpx.MockTransport`; Anthropic with a fake client object.
- At least 2 seconds between requests to the same host; HTTP timeout 20 s; one retry with backoff on network errors and 5xx; 429 or a bot-block page stops that source for the run (§15).
- Secrets come only from env vars: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `ANTHROPIC_API_KEY`, `DISCORD_WEBHOOK_URL`. A missing one disables only its feature, with a dashboard message.
- Photo scanning is **off by default** (`scan_enabled` = false). Default caps: daily $5, monthly $50, max scan price $300, per-scan ceiling $0.25 (§11.2).
- Vision request: model `claude-opus-5-5`, `output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCAN_SCHEMA}}`, `max_tokens=16000`, non-streaming, `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, via `client.beta.messages.create`. If the installed SDK rejects `fallbacks` or `output_config` as keyword arguments, pass them through `extra_body` instead. Price table default: `{"claude-opus-5-5": (4.00, 20.00)}` USD per million input/output tokens.
- Scoring defaults (§8.5): `sell_fee_rate` 0.1325; `sell_shipping` = `buy_shipping_estimate` = gpu 20 / cpu 8 / console 30 / controller 10; `lot_ship_base` 8; `lot_ship_per_game` 0.40; `min_profit` 30; `min_lot_profit` 50; `min_discount` 0.20; `min_hardware_lot` 3; `min_game_lot` 5; `rare_item_threshold` 50; `tax_rate` 0.
- Repair defaults (§5.2): console 25 / 0.60, controller 8 / 0.70, gpu 40 / 0.40, cpu 10 / 0.30.
- Cache TTLs: market values 24 h; photo-item prices 30 days. Min comps: 5 (3 for photo items).
- Refresh every 30 min; eBay full pass every 3 h (`ebay_full_pass_hours`).
- Notification reasons and defaults: `photo_find` on, `target_hit` on, `hardware_lot_deal` on, `game_lot_deal` on, `resale_deal` off.
- ShopGoodwill and HiBid response shapes are **assumptions** (no public docs, no live access from the dev container). Each adapter keeps its URL, request body/query and field names in one constant block at the top of the module so a live fix is a one-place edit; fixtures mirror those shapes.
- Timestamps are stored as ISO-8601 UTC strings. Day/month boundaries for caps use the machine's local time zone.
- Commit after every task; commit messages end with the session's attribution lines.

## Review Focus

1. **Numbers that aren't quantities** — "PCIe 4.0 x16", "2x HDMI", "3x fans", "PlayStation 2 console", "Xbox 360 controller", "Spider-Man 2", "RX 7900 XT" must parse as quantity 1 and stay on the single-item path. Pinned in Task 3.
2. **Look-alike products across categories** — "Ryzen 9 7900X" (CPU) vs "RX 7900 XT"/"XTX" (GPU); "Switch" vs "Switch 2"/"Switch Lite"/"Switch OLED"; "DS" vs "3DS"/"2DS"; "Xbox" vs "Xbox 360"/"Xbox One"/"Xbox Series X". Each title must land on exactly one product or be rejected. Pinned in Task 7.
3. **Cap overshoot** — spend near the cap, or a manual scan and an auto-scan at the same moment, must never start a scan that could push spend past a cap. Scans are serialized by a lock and gated on spend + ceiling. Pinned in Tasks 13 and 16.
4. **Silent site breakage** — a site that changes format and returns data that parses to zero listings must show a "probable site change" banner, not an empty dashboard. Pinned in Task 16.
5. **Repeat alerts** — the same listing must not notify twice for the same reason across refreshes, price changes or app restarts, and ended listings never notify. Pinned in Tasks 15 and 16.

---

## File Structure

```
pyproject.toml                      deps, pytest config
config.example.yaml                 every tunable with its default (Task 19)
README.md                           setup, keys, Console spend limit, fixture capture (Task 19)
catalog/
  gpus.yaml cpus.yaml consoles.yaml controllers.yaml   single-item products (Task 7)
  bulk.yaml platforms.yaml                             lot catalogs (Task 7)
dealfinder/
  __init__.py  __main__.py          __main__ runs uvicorn (Task 17)
  models.py                         dataclasses + literal types (Task 1)
  config.py                         Config + load_config (Task 1)
  db.py                             schema + query functions (Task 2, extended by later tasks)
  text.py                           normalize, quantity parser, specificity picker (Task 3)
  catalog.py                        Catalog, seed loading, overrides, customs (Task 4)
  matcher.py                        single-item matching (Task 5)
  lots.py                           hardware/game lot matching (Task 6)
  classify.py                       routing (Task 6)
  sources/base.py                   HttpClient, errors, Source protocol (Task 8)
  sources/ebay.py                   Browse API (Task 8)
  sources/ebay_sold.py              sold-page fetch/parse (Task 9)
  sources/shopgoodwill.py sources/hibid.py   (Task 10)
  market.py                         values + caching (Task 11)
  scoring.py                        (Task 12)
  budget.py                         scan gate + spend (Task 13)
  vision.py finds.py                Claude scan; find evaluation (Task 14)
  notify.py                         Discord (Task 15)
  pipeline.py                       refresh + scan orchestration (Task 16)
  web/app.py web/routes.py web/templates/*.html web/static/style.css   (Tasks 17–18)
tests/
  fixtures/{ebay,ebay_sold,shopgoodwill,hibid,vision}/...
  test_<module>.py per module
```

---

### Task 1: Project skeleton, models, config

**Files:**
- Create: `pyproject.toml`, `dealfinder/__init__.py`, `dealfinder/models.py`, `dealfinder/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces (`models.py`, all `@dataclass`):
  - Literals: `Category = Literal["gpu","cpu","console","controller"]`, `Condition = Literal["new","used"]`, `Completeness = Literal["loose","cib","sealed","unknown"]`, `ListingKind = Literal["single","hardware_lot","game_lot","scan_only"]`, `Confidence = Literal["high","medium","low"]`.
  - `Product(id: str, category: Category, name: str, ebay_query: str, match: str, exclude: tuple[str, ...] = (), max_price: float | None = None, enabled: bool = True)` (frozen)
  - `BulkProduct(id, category: Category, name, ebay_query, match, repair_cost: float, success_rate: float, enabled: bool = True, source_product_id: str | None = None)` (frozen)
  - `Platform(id, name, match, ebay_lot_query, enabled: bool = True)` (frozen)
  - `Listing(source: str, source_id: str, title: str, url: str, price: float, shipping: float | None, shipping_offered: bool, image_urls: list[str], is_auction: bool, end_time: datetime | None, bid_count: int = 0, buyer_premium_pct: float | None = None, condition_id: str | None = None)` — `shipping=None` with `shipping_offered=True` means "ships, cost unknown"; `shipping_offered=False` means pickup only.
  - `Classified(kind: ListingKind, key: str, condition: Condition | None, quantity: int | None, category: Category | None)` — `quantity=None` means unknown; `key` is a product, bulk-product or platform id.
  - `Comp(title: str, price: float, sold_date: date | None, url: str, condition_label: str | None)` — `price` includes shipping.
  - `MarketValue(key: str, condition: str, value: float, comp_count: int, method: Literal["sold","active_bin"], low_confidence: bool, computed_at: datetime)`
  - `Scored(total_cost: float, shipping_estimated: bool, profit: float | None, discount_pct: float | None, is_target_hit: bool, is_deal: bool, rank_score: float)`
  - `ScanItem(kind: str, platform: str, title: str, edition: str | None, completeness: Completeness, quantity: int, confidence: Confidence, photo_index: int)`
  - `ScanResult(status: Literal["ok","refused","failed"], items: list[ScanItem], unit_count_estimate: int | None, notes: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float, error: str | None = None)`
- Produces (`config.py`): `Config` dataclass holding every default in Global Constraints plus `db_path="dealfinder.db"`, `catalog_dir=Path("catalog")`, `refresh_minutes=30`, `ebay_full_pass_hours=3`, `buyer_premium_pct={"ebay":0.0,"shopgoodwill":0.0,"hibid":0.15}`, `handling_fee={"ebay":0.0,"shopgoodwill":5.0,"hibid":0.0}`, `broad_queries={"shopgoodwill":[...], "hibid":[...]}` (the §9 list), `vision_model="claude-opus-5-5"`, `vision_prices`, `scan_cost_ceiling=0.25`, `initial_daily_cap=5.0`, `initial_monthly_cap=50.0`, `initial_max_scan_price=300.0`, `notify_reasons` dict, `repair_defaults: dict[Category, tuple[float, float]]`, and secrets `ebay_client_id`, `ebay_client_secret`, `anthropic_api_key`, `discord_webhook_url` (all `str | None`).
  - `load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> Config` — YAML keys override defaults (dicts merge one level deep); env supplies secrets; missing file → defaults.

- [ ] **Step 1: Write `pyproject.toml`** with the Tech Stack deps, `[project.optional-dependencies] dev = ["pytest", "respx"]`, `[tool.pytest.ini_options] testpaths=["tests"]`. Run `pip install -e '.[dev]'`; expect success.
- [ ] **Step 2: Write failing tests** in `tests/test_config.py`:

```python
def test_defaults_without_file():
    cfg = load_config(None, env={})
    assert cfg.sell_fee_rate == 0.1325 and cfg.min_lot_profit == 50
    assert cfg.sell_shipping == {"gpu": 20, "cpu": 8, "console": 30, "controller": 10}
    assert cfg.repair_defaults["console"] == (25, 0.60)
    assert cfg.notify_reasons["resale_deal"] is False
    assert cfg.anthropic_api_key is None

def test_yaml_overrides_merge_one_level(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text("min_profit: 40\nsell_shipping: {gpu: 25}\n")
    cfg = load_config(p, env={})
    assert cfg.min_profit == 40
    assert cfg.sell_shipping == {"gpu": 25, "cpu": 8, "console": 30, "controller": 10}

def test_secrets_from_env():
    cfg = load_config(None, env={"DISCORD_WEBHOOK_URL": "https://d/x", "ANTHROPIC_API_KEY": "k"})
    assert cfg.discord_webhook_url == "https://d/x" and cfg.anthropic_api_key == "k"
```

- [ ] **Step 3:** Run `pytest tests/test_config.py -v`; expect FAIL (import error).
- [ ] **Step 4: Implement** `models.py` and `config.py` per Interfaces.
- [ ] **Step 5:** Run `pytest -v`; expect PASS.
- [ ] **Step 6: Commit** `feat: project skeleton, models and config`.

---

### Task 2: Database layer

**Files:**
- Create: `dealfinder/db.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `Listing`, `Classified`, `Scored` (Task 1).
- Produces:
  - `connect(path: str | Path) -> sqlite3.Connection` — `row_factory=sqlite3.Row`, `check_same_thread=False`, `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, creates every §13 table with `CREATE TABLE IF NOT EXISTS`. `listings` has `UNIQUE(source, source_id)`; `notifications` has `UNIQUE(listing_id, reason)`; `scans` has `UNIQUE(listing_id)`. List columns (`image_urls`, scan `items`) are JSON text.
  - `get_setting(conn, key: str, default: str | None = None) -> str | None`; `set_setting(conn, key: str, value: str) -> None`
  - `upsert_listing(conn, listing: Listing, c: Classified, scored: Scored | None, now: datetime) -> int` — inserts or updates by `(source, source_id)`; keeps `first_seen` and `dismissed`; sets `last_seen=now`; returns row id.
  - `get_listing(conn, listing_id: int) -> sqlite3.Row | None`
  - `query_listings(conn, kinds: Sequence[str], now: datetime, *, category: str | None = None, source: str | None = None, platform: str | None = None, ending_within_h: float | None = None, new_since: datetime | None = None, target_only: bool = False, photo_finds_only: bool = False, deals_only: bool = True) -> list[sqlite3.Row]` — excludes dismissed and ended (`end_time < now`) rows; joins `scans` for `is_rare_find/is_lot_find`; orders by `is_target_hit DESC, photo_find DESC, rank_score DESC`. `deals_only` keeps rows with `is_deal=1` or a photo find.
  - `dismiss_listing(conn, listing_id: int) -> None`
  - `start_run(conn, source: str, now: datetime) -> int`; `finish_run(conn, run_id: int, status: Literal["ok","error","site_change"], listings_seen: int, error: str | None, now: datetime) -> None`; `latest_runs(conn) -> list[sqlite3.Row]` (latest per source).

- [ ] **Step 1: Write failing tests:**
  - `test_upsert_preserves_first_seen_and_dismissed` — upsert at t1, dismiss, upsert again at t2 with a new price → same id, `first_seen == t1`, `dismissed == 1`, `price` updated, `last_seen == t2`.
  - `test_query_hides_ended_and_dismissed` — three rows (ended, dismissed, live deal) → only the live one.
  - `test_query_orders_target_hits_first` — target hit with rank 1 sorts above non-target with rank 100.
  - `test_query_filters` — `category`, `source`, `ending_within_h=12`, `new_since` each narrow correctly.
  - `test_settings_roundtrip` and `test_latest_runs_one_per_source`.
- [ ] **Step 2:** Run `pytest tests/test_db.py -v`; expect FAIL.
- [ ] **Step 3: Implement** `db.py`.
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: sqlite schema and listing queries`.

---

### Task 3: Title normalization and quantity parser

**Files:**
- Create: `dealfinder/text.py`, `tests/test_text.py`

**Interfaces:**
- Produces:
  - `normalize(title: str) -> str` — lowercase; `&`→` and `; replace `-`, `/`, `,`, `|`, `™`, `®` with spaces except inside model tokens; collapse whitespace. Split GPU suffixes from numbers (`4070ti`→`4070 ti`, `4070tisuper`→`4070 ti super`, `7900xtx`→`7900 xtx`, `9070xt`→`9070 xt`, `rtx4090`→`rtx 4090`), but keep CPU tokens joined (`7800x3d`, `13700kf`, `7900x`). Rule: split `xt`, `xtx`, `gre`, `ti`, `super` suffixes and `rtx`/`gtx`/`rx` prefixes only.
  - `LOT_WORDS = ("lot", "box of", "collection", "bulk", "wholesale")`
  - `parse_quantity(title_norm: str, mask_patterns: Iterable[str] = ()) -> int | None` — returns explicit count, `None` if a lot word appears with no count, else `1`.
  - `pick_most_specific(matches: list[tuple[T, re.Match]]) -> T | None` — `None` for no matches; if one match's span contains every other's, return it; otherwise `None` (ambiguous). Matches of the same object count once.
- Quantity algorithm (not determined by the signature):
  1. Replace every match of each `mask_patterns` regex, plus the built-in masks `pci ?e?( ?\d\.\d)? ?x ?\d+` and `\b\d+ ?(gb|tb|mb|mhz|ghz|hz|w|watt|pin|bit)\b`, with ` _ `.
  2. Try forms in order and return the first hit within 2..200: `lot of (\d+|word)`, `qty:? ?(\d+)`, `\((\d{1,3})\)`, `(\d{1,3}|word) (_ )?(consoles?|systems?|controllers?|games?|carts?|cartridges?|discs?|units?|pcs|pieces|gpus?|cpus?|cards?|processors?)\b`, `\b(\d{1,3}) ?x\b(?! ?(hdmi|dp|displayport|fans?|pin|usb|slots?))`, `\bx ?(\d{1,3})\b(?! ?(hdmi|dp|displayport|fans?|pin|usb|slots?))`. Number words: one…twenty.
  3. No hit: `None` if any `LOT_WORDS` entry appears as a whole word/phrase, else `1`.

- [ ] **Step 1: Write failing tests** — `test_quantity_forms` parametrized:

| title (normalized, mask from `[r"\b(ps2|playstation 2)\b", r"\bxbox 360\b"]`) | expected |
|---|---|
| `lot of 5 ps2 consoles for parts` | 5 |
| `5x xbox 360 controllers` | 5 |
| `ps2 slim x3 untested` | 3 |
| `(12) nintendo ds games` | 12 |
| `qty 4 rtx 3060` | 4 |
| `five ps2 consoles` | 5 |
| `20 gamecube games` | 20 |
| `playstation 2 console slim` | 1 |
| `xbox 360 controller white` | 1 |
| `rtx 3080 pcie 4.0 x16 12gb` | 1 |
| `rx 6800 2x hdmi 3x fans` | 1 |
| `marvels spider man 2 ps5 bundle` | 1 |
| `box of video games` | None |
| `huge lot ps2 games` | None |

  Also `test_normalize` (`"RTX4070Ti SUPER 16GB"`→`"rtx 4070 ti super 16gb"`, `"Ryzen 7 7800X3D"`→`"ryzen 7 7800x3d"`, `"Radeon RX 7900XTX"`→`"radeon rx 7900 xtx"`) and `test_pick_most_specific` (contained span wins; disjoint spans → None; empty → None).
- [ ] **Step 2:** Run `pytest tests/test_text.py -v`; expect FAIL.
- [ ] **Step 3: Implement** `text.py`.
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: title normalization and quantity parser`.

---

### Task 4: Catalog loader, overrides and custom entries

**Files:**
- Create: `dealfinder/catalog.py`, `tests/test_catalog.py`, `tests/fixtures/catalog/{gpus,cpus,consoles,controllers,bulk,platforms}.yaml` (2–3 small entries each)
- Modify: `dealfinder/db.py` (override and custom-entry queries)

**Interfaces:**
- Consumes: `Product`, `BulkProduct`, `Platform`, `Config` (Task 1); `connect`, settings (Task 2).
- Produces:
  - `Catalog(products: list[Product], bulk: list[BulkProduct], platforms: list[Platform])` with `enabled_products()`, `enabled_bulk()`, `enabled_platforms()`, `product(id)`, `bulk_product(id)`, `platform(id)`, and `mask_patterns() -> list[str]` (every enabled platform `match` plus every bulk console/controller `match`).
  - `load_seed(catalog_dir: Path) -> Catalog` — reads the six YAML files; `repair_cost`/`success_rate` may be `null`. Raises `ValueError` naming the file and id for a duplicate id or a regex that fails `re.compile`.
  - `load_catalog(catalog_dir: Path, conn, cfg: Config) -> Catalog` — seed + `custom_products` / `custom_bulk_products`; then applies `product_overrides` (enabled, max_price), `bulk_overrides` (enabled, repair_cost, success_rate), `platform_overrides` (enabled); fills null repair values from `cfg.repair_defaults[category]`; appends one `BulkProduct` per gpu/cpu product (`id=f"bulk-{p.id}"`, `source_product_id=p.id`, same `match`/`ebay_query`).
  - db additions: `set_override(conn, table: Literal["product_overrides","bulk_overrides","platform_overrides"], item_id: str, **fields) -> None`, `get_overrides(conn, table) -> dict[str, sqlite3.Row]`, `add_custom(conn, kind: Literal["product","bulk"], data: dict) -> None`, `list_custom(conn, kind) -> list[dict]`.

- [ ] **Step 1: Write failing tests:** `test_load_seed_reads_all_files`, `test_bad_regex_names_file_and_id`, `test_duplicate_id_rejected`, `test_overrides_applied` (disable one product, set a max_price, set a repair_cost), `test_null_repair_uses_category_default`, `test_gpu_cpu_products_become_bulk_products`, `test_custom_product_included`, `test_mask_patterns_contains_platforms`.
- [ ] **Step 2:** Run `pytest tests/test_catalog.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: catalog loading with overrides and custom entries`.

---

### Task 5: Single-item matcher

**Files:**
- Create: `dealfinder/matcher.py`, `tests/test_matcher.py`

**Interfaces:**
- Consumes: `normalize`, `pick_most_specific` (Task 3); `Product`, `Condition` (Task 1).
- Produces:
  - `GLOBAL_EXCLUDES: list[str]` and `CATEGORY_EXCLUDES: dict[Category, list[str]]` — the exact §6.4 lists as word-bounded regexes.
  - `detect_condition(title_norm: str, condition_id: str | None) -> Condition | None` — eBay ids `"1000"`, `"1500"` → `new`; `"7000"` → `None` (reject); any other id → `used`. With no id: `new` if `sealed|new in box|\bnib\b|\bbnib\b|brand new|factory sealed`, else `used`.
  - `match_single(title: str, products: Sequence[Product], condition_id: str | None = None) -> tuple[Product, Condition] | None` — normalize → global excludes → candidate matches via `pick_most_specific` over all enabled products → category + product excludes for the winner → condition.
- Tests use small inline `Product` lists (seed collisions are Task 7).

- [ ] **Step 1: Write failing tests:**
  - `test_ti_super_beats_ti` (`"MSI RTX 4070 Ti Super 16GB"` → `4070-ti-super`) and `test_plain_ti` (`"RTX 4070 Ti 12GB"` → `4070-ti`).
  - `test_ambiguous_rejected` (`"RTX 3080 or 3090 trade"` → None).
  - `test_global_excludes` parametrized over `for parts`, `not working`, `untested`, `as-is`, `box only`, `empty box`, `bent pins`, `engineering sample`, `ES`, `read description` → None.
  - `test_category_excludes` (`"RTX 4090 laptop"` → None, `"RTX 3080 waterblock"` → None, `"i9 13900K mobo combo"` → None, `"DualSense 30th Anniversary faceplate only"` → None) and `test_console_bundle_allowed` (`"PS5 Spider-Man 2 Limited Edition Bundle"` matches).
  - `test_condition` (`"... factory sealed"` → new; ebay `"3000"` → used; ebay `"7000"` → None).
- [ ] **Step 2:** Run `pytest tests/test_matcher.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: single-item matcher`.

---

### Task 6: Lot matchers and classifier

**Files:**
- Create: `dealfinder/lots.py`, `dealfinder/classify.py`, `tests/test_lots.py`, `tests/test_classify.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4, 5.
- Produces (`lots.py`):
  - `GAME_WORDS = r"\b(games?|carts?|cartridges?|discs?)\b"`, `HARDWARE_WORDS = r"\b(consoles?|systems?|controllers?|dualshock|dualsense|joy ?cons?|gamepads?|handhelds?)\b"`
  - `HARDWARE_LOT_EXCLUDES`, `GAME_LOT_EXCLUDES` — the §6.5 and §6.6 lists.
  - `match_hardware_lot(title_norm: str, bulk: Sequence[BulkProduct], quantity: int | None, min_qty: int) -> tuple[BulkProduct, int | None] | None` — `None` when excluded, mixed (`pick_most_specific` returns None with ≥2 matches), no match, or `quantity` is an int below `min_qty`.
  - `match_game_lot(title_norm: str, platforms: Sequence[Platform], quantity: int | None, min_qty: int) -> tuple[Platform, int | None] | None` — same rules with platforms.
- Produces (`classify.py`):
  - `classify(listing: Listing, catalog: Catalog, cfg: Config) -> Classified | None` — §6.3 routing: game path if a platform matches and `GAME_WORDS` matches and `HARDWARE_WORDS` doesn't; a title with both game and hardware words and a quantity ≠ 1 is skipped (console + games bundle); hardware path when `parse_quantity(..., catalog.mask_patterns())` is ≥ 2 or `None`; else single path. Pickup-only (`shipping_offered=False`) → `None`. Lot results with `quantity=None` get `kind="scan_only"`. `category` is the product's or bulk product's category; `None` for game lots.

- [ ] **Step 1: Write failing tests** (inline catalog with ps2/ps3/xbox/xbox-360/ds/3ds platforms and ps2-console/xbox-360-controller bulk products):
  - `test_hardware_lot_for_parts_allowed` (`"Lot of 5 PS2 Slim consoles for parts"` → hardware_lot, ps2-console, 5).
  - `test_hardware_lot_below_min` (`"2x PS2 consoles"` → None).
  - `test_hardware_lot_shells_excluded` (`"lot of 6 ps2 shells only"` → None).
  - `test_mixed_hardware_lot_skipped` (`"lot 3 ps2 and 2 xbox 360 consoles"` → None).
  - `test_game_lot` (`"Lot of 20 PS2 games"` → game_lot, ps2, 20) and `test_game_lot_untested_excluded`, `test_game_lot_repro_excluded`, `test_game_lot_below_min` (`"3 ds games"` → None).
  - `test_platform_specificity` (`"15 Xbox 360 games"` → xbox-360; `"12 3DS games"` → 3ds).
  - `test_ps2_game_lot_not_a_console` (`"PS2 game lot 10 games"` → game_lot, never hardware).
  - `test_console_with_games_skipped` (`"PS2 console with 10 games"` → None).
  - `test_unknown_count_is_scan_only` (`"Box of PS2 games"` → scan_only, ps2, None).
  - `test_pickup_only_dropped` and `test_single_path` (`"RTX 4090 Founders Edition"` → single).
- [ ] **Step 2:** Run `pytest tests/test_lots.py tests/test_classify.py -v`; expect FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: lot matchers and listing classifier`.

---

### Task 7: Seed catalogs

**Files:**
- Create: `catalog/gpus.yaml`, `catalog/cpus.yaml`, `catalog/consoles.yaml`, `catalog/controllers.yaml`, `catalog/bulk.yaml`, `catalog/platforms.yaml`, `tests/test_seed_catalog.py`

**Interfaces:**
- Consumes: `load_seed` (Task 4), `match_single` (Task 5), `classify` (Task 6).
- Content (ids are kebab-case, e.g. `rtx-4070-ti-super`, `ryzen-7800x3d`, `i7-13700kf`):
  - **GPUs:** RTX 3050, 3060 8GB, 3060 12GB, 3060 Ti, 3070, 3070 Ti, 3080 10GB, 3080 12GB, 3080 Ti, 3090, 3090 Ti; 4060, 4060 Ti 8GB, 4060 Ti 16GB, 4070, 4070 Super, 4070 Ti, 4070 Ti Super, 4080, 4080 Super, 4090; 5050, 5060, 5060 Ti 8GB, 5060 Ti 16GB, 5070, 5070 Ti, 5080, 5090. RX 6400, 6500 XT, 6600, 6600 XT, 6650 XT, 6700, 6700 XT, 6750 XT, 6800, 6800 XT, 6900 XT, 6950 XT; 7600, 7600 XT, 7700 XT, 7800 XT, 7900 GRE, 7900 XT, 7900 XTX; 9060 XT 8GB, 9060 XT 16GB, 9070, 9070 XT. Arc B570, B580. Where VRAM variants exist the plain pattern rejects the other size (e.g. 3060 12GB requires `12 ?gb`; 3060 8GB requires `8 ?gb`; a 3060 title with no size matches neither). AMD patterns require `rx` or `radeon` before the number.
  - **CPUs:** Intel 12400, 12400F, 12600K, 12600KF, 12700, 12700K, 12700KF, 12900K, 12900KF, 12900KS; 13400, 13400F, 13600K, 13600KF, 13700, 13700K, 13700KF, 13900K, 13900KF, 13900KS; 14400, 14400F, 14600K, 14600KF, 14700, 14700K, 14700KF, 14900K, 14900KF, 14900KS; Core Ultra 245K, 245KF, 265K, 265KF, 285K. AMD 7600, 7600X, 7700, 7700X, 7800X3D, 7900, 7900X, 7900X3D, 7950X, 7950X3D; 9600, 9600X, 9700X, 9800X3D, 9900X, 9900X3D, 9950X, 9950X3D; 5600X3D, 5700X3D, 5800X3D. AMD CPU patterns must not match when preceded by `rx` or followed by ` xt`.
  - **Limited-edition consoles:** PS5 30th Anniversary, PS5 Pro 30th Anniversary, PS5 Spider-Man 2 Limited Edition bundle, Xbox Series X Halo Infinite Limited Edition, Xbox Series X 2TB Galaxy Black Special Edition, Switch OLED Zelda Tears of the Kingdom Edition, Switch OLED Pokémon Scarlet & Violet Edition, Switch OLED Splatoon 3 Edition, Switch OLED Mario Red Edition, PlayStation Portal 30th Anniversary.
  - **Limited-edition controllers:** DualSense 30th Anniversary, DualSense Edge 30th Anniversary, DualSense God of War Ragnarök, DualSense Spider-Man 2, DualSense Astro Bot, Xbox Wireless Controller Starfield, Xbox Elite Series 2 Halo Infinite, Switch Pro Controller Zelda Tears of the Kingdom.
  - **Before adding any limited edition, confirm it is a real release with a web search; drop any that can't be confirmed** (§5.1).
  - **bulk.yaml:** consoles ps1, ps2, ps3, ps4, ps5, psp, ps-vita, xbox, xbox-360, xbox-one, xbox-series-x, xbox-series-s, gamecube, wii, wii-u, switch, switch-lite, switch-oled, switch-2, ds, ds-lite, dsi, 3ds, new-3ds, 2ds; controllers dualshock-2, dualshock-3, dualshock-4, dualsense, xbox-controller, xbox-360-controller, xbox-wireless-controller (One/Series), joy-con, switch-pro-controller, gamecube-controller, wii-remote. All `repair_cost`/`success_rate` null. Console patterns require a §5.2 hardware word.
  - **platforms.yaml:** nes, snes, n64, gamecube, wii, wii-u, switch, switch-2, gameboy, gameboy-color, gameboy-advance, ds, 3ds, genesis, saturn, dreamcast, game-gear, ps1, ps2, ps3, ps4, ps5, psp, ps-vita, xbox, xbox-360, xbox-one, xbox-series, atari-2600; each with `ebay_lot_query` `"<name> game lot"`.

- [ ] **Step 1: Write failing tests** against the real `catalog/` dir:
  - `test_seed_loads` (no errors; ≥ 50 GPUs, ≥ 50 CPUs, ≥ 25 platforms).
  - `test_single_collisions` parametrized title → expected id: `"AMD Ryzen 9 7900X"`→`ryzen-7900x`; `"Sapphire RX 7900 XT 20GB"`→`rx-7900-xt`; `"RX 7900XTX Nitro"`→`rx-7900-xtx`; `"Ryzen 7 7800X3D"`→`ryzen-7800x3d`; `"Ryzen 7 7700X"`→`ryzen-7700x`; `"i7-13700KF"`→`i7-13700kf`; `"Intel Core i7 13700K"`→`i7-13700k`; `"RTX 3060 12GB"`→`rtx-3060-12gb`; `"RTX 3060 Ti"`→`rtx-3060-ti`; `"RTX 5060 Ti 16GB"`→`rtx-5060-ti-16gb`; `"RTX 4080 Super"`→`rtx-4080-super`.
  - `test_lot_collisions`: `"Lot of 4 Nintendo Switch Lite consoles"`→switch-lite; `"3 Nintendo Switch 2 consoles"`→switch-2; `"lot 5 Nintendo 3DS XL systems"`→3ds (not ds); `"10 Nintendo DS games"`→ds platform; `"8 Xbox One games"`→xbox-one; `"6 original Xbox games"`→xbox.
  - `test_every_pattern_matches_its_own_name` — for every entry, `match` matches `normalize(name)`.
- [ ] **Step 2:** Run `pytest tests/test_seed_catalog.py -v`; expect FAIL.
- [ ] **Step 3: Write the six YAML files.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS. Fix patterns, not tests, on failure.
- [ ] **Step 5: Commit** `feat: seed catalogs for products, bulk lots and platforms`.

---

### Task 8: HTTP base and eBay Browse adapter

**Files:**
- Create: `dealfinder/sources/__init__.py`, `dealfinder/sources/base.py`, `dealfinder/sources/ebay.py`, `tests/test_http.py`, `tests/test_ebay.py`, `tests/fixtures/ebay/search.json`, `tests/fixtures/ebay/item.json`

**Interfaces:**
- Produces (`base.py`):
  - `class SourceError(Exception)`, `class SourceBlocked(SourceError)`.
  - `class HttpClient(min_interval_s: float = 2.0, timeout_s: float = 20.0, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic)` with `request(method: str, url: str, **kwargs) -> httpx.Response` — waits so the same host is hit at most every `min_interval_s`; retries once after 2 s on `httpx.TransportError` or 5xx; raises `SourceBlocked` on 429 or when `is_bot_block(resp)`; raises `SourceError` on any other non-2xx. Sends a desktop-browser `User-Agent`.
  - `is_bot_block(resp: httpx.Response) -> bool` — 403, or HTML containing `captcha`, `Pardon Our Interruption`, or `cf-chl`.
  - `class Source(Protocol)`: `name: str`; `search(self, query: str) -> list[Listing]`; `photos(self, source_id: str, known: list[str]) -> list[str]`.
- Produces (`ebay.py`):
  - `TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"`, `SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"`, `ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/{item_id}"`.
  - `class EbaySource(http: HttpClient, client_id: str, client_secret: str, clock: Callable[[], datetime])`, `name="ebay"`; `search(query, fixed_price_only: bool = False) -> list[Listing]`; `photos(source_id, known) -> list[str]` (item detail `image` + `additionalImages`, max 8).
  - Token: client-credentials POST (basic auth, scope `https://api.ebay.com/oauth/api_scope`), cached until 60 s before `expires_in`.
  - Search params: `q`, `limit=200`, `filter=buyingOptions:{AUCTION|FIXED_PRICE},deliveryOptions:{SHIP_TO_HOME},itemLocationCountry:US` (`FIXED_PRICE` only when `fixed_price_only`); header `X-EBAY-C-MARKETPLACE-ID: EBAY_US`.
  - `parse_search(data: dict) -> list[Listing]` — per `itemSummaries[]`: `source_id=itemId`, `price` = `currentBidPrice.value` if `"AUCTION" in buyingOptions` and present, else `price.value`; `shipping` = first `shippingOptions[]` cost when `shippingCostType == "FIXED"`, `None` when `"CALCULATED"`; `shipping_offered=True`; `image_urls` from `image.imageUrl` + `additionalImages`; `end_time` from `itemEndDate`; `bid_count=bidCount or 0`; `condition_id=conditionId`; `url=itemWebUrl`. Items missing a price are skipped.

- [ ] **Step 1: Write fixtures** (3 summaries: auction with bid + fixed shipping, BIN with calculated shipping, item missing price) and failing tests: `test_parse_search_fields`, `test_auction_uses_current_bid`, `test_calculated_shipping_is_none`, `test_missing_price_skipped`, `test_token_cached` (respx: two searches → one token call), `test_photos_from_item_detail`. In `test_http.py`: `test_throttles_same_host` (fake clock/sleep records a 2.0 s wait), `test_retries_once_on_500`, `test_429_raises_blocked`, `test_captcha_page_raises_blocked`.
- [ ] **Step 2:** Run `pytest tests/test_http.py tests/test_ebay.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: rate-limited http client and ebay browse adapter`.

---

### Task 9: eBay sold-listings parser

**Files:**
- Create: `dealfinder/sources/ebay_sold.py`, `tests/test_ebay_sold.py`, `tests/fixtures/ebay_sold/s_item.html`, `tests/fixtures/ebay_sold/s_card.html`

**Interfaces:**
- Consumes: `HttpClient` (Task 8), `Comp` (Task 1).
- Produces:
  - `sold_url(query: str) -> str` — `https://www.ebay.com/sch/i.html?_nkw=<quoted>&LH_Sold=1&LH_Complete=1&LH_PrefLoc=1&_ipg=240`.
  - `parse_sold(html: str) -> list[Comp]` with selectolax, supporting both layouts with selectors in one dict: legacy `li.s-item` (`.s-item__title`, `.s-item__price`, `.s-item__shipping`/`.s-item__logisticsCost`, `.s-item__caption--signal`/`.POSITIVE` for "Sold Oct 3, 2026", `.SECONDARY_INFO` for condition, `a.s-item__link`) and current `li.s-card` (`.s-card__title`, `.s-card__price`, attribute rows containing "delivery"/"shipping", caption "Sold …", subtitle condition, `a.su-link`). Skips the "Shop on eBay" placeholder, price ranges ("to"), and unparseable prices. Shipping "Free" or missing → 0.
  - `class SoldFetcher(http: HttpClient)`: `fetch(query: str) -> list[Comp]`.

- [ ] **Step 1: Write fixtures** (hand-built HTML, ~6 items each, including a placeholder item, a price range and a free-shipping item) and failing tests: `test_parse_legacy_layout`, `test_parse_card_layout`, `test_price_includes_shipping` (`$100.00` + `+$12.50 delivery` → 112.50), `test_skips_placeholder_and_ranges`, `test_sold_date_and_condition_parsed`, `test_sold_url_encodes_query`.
- [ ] **Step 2:** Run `pytest tests/test_ebay_sold.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: ebay sold listings parser`.

---

### Task 10: ShopGoodwill and HiBid adapters

**Files:**
- Create: `dealfinder/sources/shopgoodwill.py`, `dealfinder/sources/hibid.py`, `tests/test_shopgoodwill.py`, `tests/test_hibid.py`, `tests/fixtures/shopgoodwill/search.json`, `tests/fixtures/shopgoodwill/item.json`, `tests/fixtures/hibid/search.json`

**Interfaces:**
- Consumes: `HttpClient`, `Source` (Task 8).
- Produces (`shopgoodwill.py`), with the constant block at the top:
  - `SEARCH_URL = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"`, `ITEM_URL = "https://buyerapi.shopgoodwill.com/api/ItemDetail/GetItemDetailModelByItemId/{item_id}"`, `LISTING_URL = "https://shopgoodwill.com/item/{item_id}"`, `TIMEZONE = "America/Los_Angeles"`.
  - `build_search_body(query: str, page: int = 1, page_size: int = 40) -> dict` — `{"searchText": query, "page": page, "pageSize": page_size, "closedAuctions": False, "sortColumn": "1", "sortDescending": False}`.
  - `parse_search(data: dict) -> list[Listing]` — items at `data["searchResults"]["items"]`; fields `itemId`, `title`, `currentPrice`, `numBids`, `endTime` (naive local Pacific time → UTC), `imageURL`, `pickupOnly`; `shipping=None`; `shipping_offered = not pickupOnly and "pickup only" not in title.lower()`; `is_auction=True`.
  - `parse_item_detail(data: dict) -> list[str]` — `imageUrlString` split on `;`, max 8.
  - `class ShopGoodwillSource(http)`, `name="shopgoodwill"`.
- Produces (`hibid.py`):
  - `GRAPHQL_URL = "https://hibid.com/graphql"`, `LOT_URL = "https://hibid.com/lot/{lot_id}"`, `LOT_SEARCH_QUERY` (a `lotSearch(input: {searchText, ...}, pageNumber, pageLength)` query selecting `pagedResults.results { id lead quantity shippingOffered lotState { highBid minBid bidCount timeLeftSeconds isClosed } auction { buyerPremium shippingOffered } pictures { fullSizeLocation } }`).
  - `parse_search(data: dict, now: datetime) -> list[Listing]` — results at `data["data"]["lotSearch"]["pagedResults"]["results"]`; skip `isClosed`; `price = highBid if highBid > 0 else minBid`; `end_time = now + timeLeftSeconds`; `buyer_premium_pct = buyerPremium / 100` when present; `shipping_offered = lot.shippingOffered or auction.shippingOffered`; `shipping=None`; photos from `pictures[].fullSizeLocation` (max 8).
  - `class HiBidSource(http, clock)`, `name="hibid"`; `photos()` returns `known`.

- [ ] **Step 1: Write fixtures** (3–4 items each, one pickup-only, one closed HiBid lot) and failing tests: `test_sgw_parse_fields`, `test_sgw_end_time_pacific_to_utc` (`"2026-10-25T19:30:00"` → `2026-10-26T02:30:00Z`), `test_sgw_pickup_only_flagged`, `test_sgw_item_photos`, `test_hibid_parse_fields`, `test_hibid_min_bid_when_no_bids`, `test_hibid_premium_percent`, `test_hibid_closed_skipped`, `test_hibid_no_shipping_flagged`.
- [ ] **Step 2:** Run tests; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: shopgoodwill and hibid adapters`.

---

### Task 11: Market value service

**Files:**
- Create: `dealfinder/market.py`, `tests/test_market.py`
- Modify: `dealfinder/db.py` (market queries)

**Interfaces:**
- Consumes: `SoldFetcher` (Task 9), `EbaySource` (Task 8), `match_single`, `detect_condition` (Task 5), `match_hardware_lot`, `match_game_lot` (Task 6), `parse_quantity`, `normalize` (Task 3), `Catalog` (Task 4).
- Produces:
  - `iqr_median(values: Sequence[float]) -> tuple[float, int] | None` — drop values outside [Q1 − 1.5·IQR, Q3 + 1.5·IQR] (quartiles via `statistics.quantiles(n=4, method="inclusive")`; with < 4 values, no trimming); return `(median, kept_count)`; empty → `None`.
  - `label_condition(label: str | None) -> Condition | None` — "Brand New", "New (Other)", "Open Box", "New" → new; "Pre-Owned", "Used", "Refurbished" variants → used; "Parts Only", "For parts" → None.
  - `class MarketService(conn, cfg: Config, sold: SoldFetcher, ebay: EbaySource | None, clock: Callable[[], datetime])`:
    - `product_value(p: Product, condition: Condition) -> MarketValue | None` — key `p.id`; a comp counts when `match_single(comp.title, [all enabled products])` returns `p`, and its condition is `label_condition(comp.condition_label)`, falling back to the title-detected condition when the label is missing; parts-only labels are dropped.
    - `bulk_value(b: BulkProduct) -> MarketValue | None` — `b.source_product_id` → `product_value(source, "used")`; else key `b.id`, condition `"used"`, comps = bulk regex matches with quantity 1 and label not parts.
    - `per_game_value(platform: Platform) -> MarketValue | None` — key `platform.id`, condition `"per_game"`, values = sold price ÷ count for sold game lots of that platform with a readable count ≥ 2.
    - `item_price(platform: str, title: str, completeness: Completeness) -> MarketValue | None` — key `f"{platform}|{title}"` lowercased, condition = completeness (`unknown` priced as `loose`); query `"{platform} {title}"` plus `" complete"` for `cib` and `" sealed"` for `sealed`; comps must contain the platform text and every title word of length ≥ 3 except `the`, `and`, `for`, `of`; exclude `lot`, `case only`, `manual only`, `box only`, `repro`, `reproduction`, `for parts`. 30-day TTL; min 3 comps.
    - All: return a cached value younger than the TTL; otherwise fetch, filter, `iqr_median`, mark `low_confidence` below the min comp count, save value + comps. On `SourceError` or zero comps for product/bulk values, fall back to `ebay.search(query, fixed_price_only=True)` filtered the same way, `method="active_bin"`, `low_confidence=True`. No value at all → `None` (and nothing cached).
  - db additions: `save_market_value(conn, mv: MarketValue, comps: list[Comp], table: Literal["market_values","item_prices"] = "market_values")`, `get_market_value(conn, key, condition, table=...) -> MarketValue | None`, `get_comps(conn, key, condition) -> list[Comp]`.

- [ ] **Step 1: Write failing tests** with a fake `SoldFetcher` returning canned `Comp` lists:
  - `test_iqr_median_trims_outlier` (`[100,105,110,95,900]` → `(102.5, 4)`), `test_iqr_median_small_sample`, `test_iqr_median_empty`.
  - `test_product_value_filters_variants_and_condition` (comps for 4070 Ti, 4070 Ti Super, "for parts", used and new → only matching used comps counted).
  - `test_low_confidence_below_five`.
  - `test_cache_hit_within_ttl` (second call doesn't fetch) and `test_cache_expired_refetches` (clock + 25 h).
  - `test_fallback_to_active_bin` (sold raises `SourceError`) → `method == "active_bin"`, `low_confidence`.
  - `test_per_game_value` (sold lots "Lot of 10 PS2 games" $50, "20 ps2 games" $80, "ps2 game lot" (no count) $30 → values [5.0, 4.0]).
  - `test_bulk_value_for_gpu_uses_product_value`.
  - `test_item_price_title_filter_and_30_day_ttl` ("Chrono Trigger" + snes: keeps "Chrono Trigger SNES cart", drops "Chrono Trigger SNES repro" and "SNES game lot chrono trigger").
- [ ] **Step 2:** Run `pytest tests/test_market.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: market value service with caching and fallback`.

---

### Task 12: Scoring

**Files:**
- Create: `dealfinder/scoring.py`, `tests/test_scoring.py`

**Interfaces:**
- Consumes: `Listing`, `Product`, `BulkProduct`, `Platform`, `MarketValue`, `Scored`, `Config`.
- Produces:
  - `total_cost(listing: Listing, cfg: Config, est_shipping: float) -> tuple[float, bool]` — §8 formula; premium = `listing.buyer_premium_pct` if not None else `cfg.buyer_premium_pct[listing.source]`; handling = `cfg.handling_fee[listing.source]`; shipping = `listing.shipping` or `est_shipping` (estimated flag).
  - `time_weight(listing: Listing, now: datetime) -> float` — 1.0 if not auction or ends ≤ 12 h; 0.7 if ≤ 48 h; else 0.4.
  - `score_single(listing, product: Product, value: MarketValue | None, cfg, now) -> Scored` (§8.1; no value → profit/discount `None`, deal only via target hit).
  - `score_hardware_lot(listing, bulk: BulkProduct, qty: int, unit_value: MarketValue | None, cfg, now) -> Scored` (§8.2; estimated buy shipping = `buy_shipping_estimate[category] * qty`).
  - `score_game_lot(listing, qty: int, per_game: MarketValue | None, cfg, now) -> Scored` (§8.3; estimated buy shipping = `lot_ship_base + lot_ship_per_game * qty`).
  - `rank_score = max(profit or 0, 0) * time_weight`.

- [ ] **Step 1: Write failing tests** with exact numbers:
  - `test_total_cost_hibid_premium`: hibid, price 100, shipping 15, premium from listing 0.18 → 133.0, not estimated.
  - `test_total_cost_estimated_shipping`: shopgoodwill gpu, price 200, shipping None → 200 + 20 + 5 = 225.0, estimated.
  - `test_single_resale_deal`: market 500, cost 300 gpu → resale_net = 500×0.8675 − 20 = 413.75, profit 113.75, discount 0.40, deal.
  - `test_single_target_hit_without_value`: max_price 350, cost 340, value None → target hit, deal, profit None.
  - `test_single_not_deal_low_discount`: market 500, cost 420 → not deal.
  - `test_hardware_lot`: 5 PS2 consoles, unit value 80, success 0.6, repair 25, cost 100 → unit_net = 80×0.8675 − 30 = 39.40; revenue = 5×0.6×39.40 = 118.20; profit = 118.20 − 125 − 100 = −106.80; not deal.
  - `test_game_lot`: 20 games, per-game 6.00, cost 40 → resale_net = 120×0.8675 − (8 + 8) = 88.10; profit 48.10 → not deal (< 50); with cost 30 → profit 58.10, discount 0.75, deal.
  - `test_time_weight` (BIN 1.0; ends in 6 h 1.0; 30 h 0.7; 72 h 0.4).
- [ ] **Step 2:** Run `pytest tests/test_scoring.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: deal scoring for single items and lots`.

---

### Task 13: Budget gate and spend tracking

**Files:**
- Create: `dealfinder/budget.py`, `tests/test_budget.py`
- Modify: `dealfinder/db.py` (`record_spend`, `spend_between`)

**Interfaces:**
- Consumes: settings (Task 2), `Config`.
- Produces:
  - `Caps(daily: float, monthly: float, max_scan_price: float)`; `GateDecision(allowed: bool, reason: str)`.
  - `class Budget(conn, cfg: Config, clock: Callable[[], datetime])` — `clock` returns an aware local-time datetime.
    - `scan_enabled() -> bool` (setting `scan_enabled`, default `"false"`); `set_scan_enabled(on: bool)`.
    - `paused() -> bool`; `set_paused(on: bool)` (setting `scheduler_paused`).
    - `caps() -> Caps` (settings fall back to `cfg.initial_*`); `set_caps(daily: float | None = None, monthly: float | None = None, max_scan_price: float | None = None)` — rejects negatives with `ValueError`.
    - `spent_today() -> float`; `spent_this_month() -> float` — local midnight / 1st of month, converted to UTC for the query.
    - `can_auto_scan(total_cost: float) -> GateDecision` — reasons, checked in order: `"scanning is off"`, `"over max scan price"`, `"daily cap reached"`, `"monthly cap reached"`, else allowed `"ok"`. Cap check: `spent + cfg.scan_cost_ceiling > cap` → denied.
    - `can_manual_scan() -> GateDecision` — cap checks only.
    - `record(listing_id: int, input_tokens: int, output_tokens: int, cost_usd: float) -> None`.
    - `lock: threading.Lock` — shared by every scan path (Task 16).
  - db additions: `record_spend(conn, listing_id, input_tokens, output_tokens, cost_usd, at: datetime)`, `spend_between(conn, start: datetime, end: datetime) -> float`.

- [ ] **Step 1: Write failing tests:**
  - `test_off_by_default` → `can_auto_scan(10).reason == "scanning is off"`.
  - `test_price_filter` (on, cost 301 → "over max scan price").
  - `test_daily_cap_with_ceiling` (spend 4.80 today, cap 5, ceiling 0.25 → "daily cap reached"; spend 4.70 → allowed).
  - `test_monthly_cap` (49.80 earlier this month → denied).
  - `test_manual_ignores_switch_and_price_but_not_caps`.
  - `test_day_and_month_reset` (spend yesterday at 23:59 local doesn't count today; spend on the 31st doesn't count on the 1st).
  - `test_set_caps_validates`.
- [ ] **Step 2:** Run `pytest tests/test_budget.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: scan budget gate and spend tracking`.

---

### Task 14: Vision scanner and find evaluation

**Files:**
- Create: `dealfinder/vision.py`, `dealfinder/finds.py`, `tests/test_vision.py`, `tests/test_finds.py`, `tests/fixtures/vision/ok.json`, `tests/fixtures/vision/photo.jpg` (small generated JPEG)

**Interfaces:**
- Consumes: `HttpClient` (Task 8), `ScanItem`, `ScanResult`, `MarketValue`, `Config`.
- Produces (`vision.py`):
  - `SCAN_SCHEMA: dict` — the §11.4 schema as JSON Schema with `additionalProperties: False` and all fields `required` (nullable fields as `["string","null"]` / `["integer","null"]`; enums for `kind`, `completeness`, `confidence`).
  - `PROMPT: str` — §11.3 instructions plus the listing title; tells the model to report only what is readable and to estimate the unit count when the title gives none.
  - `resize_jpeg(data: bytes, max_edge: int = 1568) -> bytes` — Pillow; converts to RGB; never upscales.
  - `compute_cost(response, requested_model: str, prices: dict[str, tuple[float, float]]) -> tuple[int, int, float]` — sums `usage.iterations` entries when present (else top-level `usage`); prices each entry by its `model` attribute if in `prices`, else at the highest configured rate (conservative).
  - `class ScanDeferred(Exception)` — rate-limited; try on a later refresh, nothing recorded.
  - `class VisionScanner(client, http: HttpClient, cfg: Config)` — `client` is an `anthropic.Anthropic(max_retries=1)` or a fake with `.beta.messages.create(**kwargs)`.
    - `scan(image_urls: list[str], title: str) -> ScanResult` — downloads up to 8 photos (a failed download skips that photo); none usable → `ScanResult(status="failed", error="no usable photos", cost 0)`; sends one request with the exact Global Constraints arguments, images first as base64 `image/jpeg` blocks, then the prompt text; `stop_reason == "refusal"` → `status="refused"` with cost; otherwise parses the first `text` block as JSON into `ScanItem`s. `anthropic.RateLimitError` → raise `ScanDeferred`; other `anthropic.APIStatusError` and `anthropic.APIConnectionError` → `status="failed"` with the error string and zero cost; invalid JSON → `status="failed"` with cost.
- Produces (`finds.py`):
  - `FindEval(identified_value: float, is_rare_find: bool, is_lot_find: bool, rare_items: list[tuple[ScanItem, float]], priced: list[tuple[ScanItem, MarketValue | None]])`
  - `evaluate_finds(result: ScanResult, price_of: Callable[[ScanItem], MarketValue | None], total_cost: float, cfg: Config) -> FindEval` — counts only items with confidence high/medium whose price is not `None` and not `low_confidence`; item value = `price.value * item.quantity`; rare = any unit price ≥ `rare_item_threshold`; lot find = `identified_value >= 1.5 * total_cost and identified_value >= total_cost + min_lot_profit`.

- [ ] **Step 1: Write failing tests:**
  - `test_request_arguments` — fake client captures kwargs: `model == "claude-opus-5-5"`, `max_tokens == 16000`, `betas == ["server-side-fallback-2026-07-01"]`, `fallbacks == "default"`, `output_config["effort"] == "medium"`, `output_config["format"]["type"] == "json_schema"`, first content blocks are `image` with `media_type == "image/jpeg"`.
  - `test_parses_items_and_cost` — fixture JSON in a `text` block, usage 10,000 in / 2,000 out → cost 0.08.
  - `test_cost_sums_iterations_and_prices_unknown_model_high`.
  - `test_refusal_status_keeps_cost`; `test_rate_limit_raises_deferred`; `test_api_error_failed_zero_cost`; `test_no_photos_failed_without_request`.
  - `test_resize_caps_long_edge` (3000×1000 → 1568×523) and `test_resize_never_upscales`.
  - `finds`: `test_rare_find` (one high-confidence item priced 60 → rare), `test_low_confidence_item_ignored`, `test_low_confidence_price_ignored`, `test_lot_find_threshold` (cost 40, items worth 95 → lot find (≥ 60 and ≥ 90); worth 85 → not).
- [ ] **Step 2:** Run `pytest tests/test_vision.py tests/test_finds.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: claude photo scanner and find evaluation`.

---

### Task 15: Discord notifier

**Files:**
- Create: `dealfinder/notify.py`, `tests/test_notify.py`
- Modify: `dealfinder/db.py` (`claim_notification`, `mark_notification`)

**Interfaces:**
- Consumes: `get_listing` (Task 2), `Config`.
- Produces:
  - `Reason = Literal["photo_find","target_hit","hardware_lot_deal","game_lot_deal","resale_deal"]`
  - `build_payload(row: sqlite3.Row, reason: Reason, details: list[str], now: datetime) -> dict` — `{"embeds": [{"title": <title ≤ 256 chars>, "url": row["url"], "description": "\n".join(details) (≤ 4000 chars), "thumbnail": {"url": first image}, "fields": [{"name": "Total cost", "value": "$123.45", "inline": True}, {"name": "Time left", "value": "5h 12m" | "Buy It Now", "inline": True}, {"name": "Site", ...}]}]}`.
  - `class Notifier(conn, cfg: Config, http: httpx.Client, sleep=time.sleep, clock=...)`:
    - `enabled() -> bool` (webhook set).
    - `notify(listing_id: int, reason: Reason, details: list[str]) -> bool` — returns False without sending when the reason is off, no webhook, the listing has ended, or `claim_notification` already exists; spaces sends ≥ 1 s apart; on 429 sleeps `retry_after` (JSON body, seconds) and retries once; marks status `sent` or `failed`; never raises on HTTP errors.
  - db additions: `claim_notification(conn, listing_id, reason, now) -> bool` (INSERT OR IGNORE; True if inserted); `mark_notification(conn, listing_id, reason, status: str)`; `notification_failures_since(conn, since) -> int`.

- [ ] **Step 1: Write failing tests** (respx on the webhook): `test_payload_contents`, `test_sends_once_per_reason` (second call and a call after re-upserting with a new price → no request), `test_disabled_reason_not_sent` (`resale_deal`), `test_ended_listing_not_sent`, `test_429_retry_after`, `test_failure_marked_not_raised`, `test_spacing_between_sends`.
- [ ] **Step 2:** Run `pytest tests/test_notify.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: discord notifier with dedupe`.

---

### Task 16: Refresh pipeline

**Files:**
- Create: `dealfinder/pipeline.py`, `tests/test_pipeline.py`
- Modify: `dealfinder/db.py` (`save_scan`, `get_scan`, `scan_candidates`)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `RefreshReport(per_source: dict[str, tuple[str, int]], scanned: int, notified: int)` — status and listing count per source.
  - `ebay_queries(catalog: Catalog) -> list[str]` — enabled product `ebay_query`s + `f"{b.ebay_query} lot"` for enabled bulk products without `source_product_id` + enabled platform `ebay_lot_query`s, de-duplicated, order preserved.
  - `rotation_slice(queries: list[str], index: int, slices: int) -> list[str]` — the `index % slices`-th of `slices` near-equal chunks; `slices = ceil(ebay_full_pass_hours * 60 / refresh_minutes)`; index persisted in setting `ebay_rotation_index`.
  - `class Pipeline(conn, cfg, catalog_loader: Callable[[], Catalog], sources: dict[str, Source], market: MarketService, scanner: VisionScanner | None, budget: Budget, notifier: Notifier | None, clock)`:
    - `refresh() -> RefreshReport` — per source: `start_run`; run its queries (eBay: rotation slice; others: `cfg.broad_queries[name]`); a `SourceBlocked`/`SourceError` ends that source with status `error`; a source whose queries all return HTTP 200 with a non-empty body but zero parsed listings is `site_change`. Each listing: `classify` → value → score (scan_only rows stored unscored) → `upsert_listing`; collect notification reasons (single target hit → `target_hit`; single resale deal → `resale_deal`; hardware/game lot deal → matching reason). Then, unless `scanner` is None, auto-scan `scan_candidates(conn, now)` in rank order while `budget.can_auto_scan(total_cost)` allows. Then send notifications for live listings.
    - `scan_listing(listing_id: int, manual: bool) -> ScanResult | None` — holds `budget.lock`; gate via `can_manual_scan()` or `can_auto_scan(total_cost)`; returns None if denied or already scanned; gets photos via `sources[row.source].photos(source_id, image_urls)`, or the stored `image_urls` when that source isn't configured; runs the scanner; records spend for any non-zero cost; prices items with `market.item_price`; `evaluate_finds`; for scan_only rows with `unit_count_estimate` ≥ the lot minimum, re-scores as the matching lot kind; `save_scan`; notifies `photo_find` with the rare items or the lot value as details. `ScanDeferred` → returns None, nothing saved.
  - db additions: `save_scan(conn, listing_id, result: ScanResult, eval: FindEval | None, now)`, `get_scan(conn, listing_id) -> sqlite3.Row | None`, `scan_candidates(conn, now) -> list[sqlite3.Row]` (kinds hardware_lot / game_lot / scan_only, live, not dismissed, no `scans` row, ordered by rank_score desc).

- [ ] **Step 1: Write failing tests** with fake sources, a fake `MarketService` (fixed values), a fake scanner and a mocked webhook:
  - `test_refresh_stores_and_scores` (one single deal, one game lot deal, one pickup-only dropped, one junk title dropped).
  - `test_source_error_isolated` (HiBid raises `SourceBlocked`; eBay rows still stored; run statuses `error` and `ok`).
  - `test_zero_parsed_is_site_change`.
  - `test_ebay_rotation_covers_all_queries` (6 refreshes visit every query exactly once).
  - `test_no_auto_scan_when_off` (scanner never called) and `test_auto_scan_when_on_respects_cap` (cap allows exactly two scans at ceiling 0.25 with recorded cost 0.20 each and daily cap 0.60).
  - `test_concurrent_scans_serialized` (two threads call `scan_listing` with remaining budget for one → scanner called once).
  - `test_manual_scan_when_off` (switch off, manual scan runs).
  - `test_scan_only_rescored_from_estimate`.
  - `test_photo_find_notifies_once` (two refreshes → one webhook call).
  - `test_ended_listing_not_scanned_or_notified`.
- [ ] **Step 2:** Run `pytest tests/test_pipeline.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: refresh pipeline with gated photo scanning`.

---

### Task 17: Web app, scheduler, header controls and Deals page

**Files:**
- Create: `dealfinder/__main__.py`, `dealfinder/web/__init__.py`, `dealfinder/web/app.py`, `dealfinder/web/routes.py`, `dealfinder/web/templates/{base,deals}.html`, `dealfinder/web/templates/partials/{header_controls,listing_rows}.html`, `dealfinder/web/static/style.css`, `tests/test_web.py`

**Interfaces:**
- Consumes: `Budget`, `Pipeline`, `query_listings`, `latest_runs`, `load_catalog`, `Config`.
- Produces:
  - `create_app(cfg: Config, *, build_pipeline: Callable[[sqlite3.Connection], Pipeline] | None = None, start_scheduler: bool = True) -> FastAPI` — default `build_pipeline` wires real sources (eBay only when both eBay keys are set), `MarketService`, `VisionScanner` (only with `ANTHROPIC_API_KEY`), `Notifier` (only with webhook). Each request opens its own connection via `db.connect(cfg.db_path)`. A `threading.Lock` prevents overlapping refreshes. With `start_scheduler`, an APScheduler `BackgroundScheduler` runs a refresh every `cfg.refresh_minutes` unless `budget.paused()`, and is shut down on app shutdown.
  - `__main__.py`: `load_config(Path("config.yaml") if it exists)` → `uvicorn.run(create_app(cfg), host="127.0.0.1", port=8000)`.
  - Routes:
    - `GET /` → Deals page (single kind, §14 columns and filters via query params `category`, `source`, `ending_within_h`, `new_only`, `target_only`). Rendering the page sets setting `last_visit` after computing NEW badges from the previous value.
    - `POST /controls/scan` (form `on=true|false`), `POST /controls/pause` (form `on`), `POST /controls/caps` (form `daily`, `monthly`, `max_scan_price`; invalid → 400 with message) → return the `header_controls` partial.
    - `POST /refresh` → starts a refresh in a background thread; returns "Refreshing…" or "Refresh already running".
    - `POST /listing/{id}/dismiss` → empty 200 (HTMX removes the row).
  - Header partial shows: scanning switch, "spent today $x.xx / this month $y.yy", caps form, pause switch, Refresh button, last successful run per source, and a banner for: any latest run with status `error` or `site_change`; notification failures in the last 24 h; missing eBay keys / Anthropic key / webhook.
  - `base.html` loads `https://unpkg.com/htmx.org@2.0.4` and `style.css`; layout works at 360 px width.

- [ ] **Step 1: Write failing tests** with `TestClient`, a temp DB and a fake `build_pipeline`, `start_scheduler=False`:
  - `test_deals_page_lists_deals` (seed rows via `upsert_listing`; target hit shows 🎯; dismissed row absent).
  - `test_filters_query_params`.
  - `test_new_badge_uses_previous_visit`.
  - `test_toggle_scan_persists` (POST on → `Budget.scan_enabled()` True; partial contains "on").
  - `test_caps_validation` (negative → 400).
  - `test_pause_toggle`.
  - `test_refresh_runs_pipeline_once_when_double_clicked`.
  - `test_banner_on_site_change` and `test_banner_missing_keys`.
  - `test_dismiss`.
- [ ] **Step 2:** Run `pytest tests/test_web.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS. Then `python -m dealfinder` with no keys; open `http://127.0.0.1:8000/` → page renders with the missing-keys banner and scanning off. Stop it.
- [ ] **Step 5: Commit** `feat: dashboard shell, controls and deals page`.

---

### Task 18: Lot pages, listing detail and catalog page

**Files:**
- Create: `dealfinder/web/templates/{hardware_lots,game_lots,listing,catalog}.html`
- Modify: `dealfinder/web/routes.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: Task 17 app; `get_listing`, `get_comps`, `get_scan`, `Pipeline.scan_listing`, `set_override`, `add_custom`, `load_catalog`.
- Produces routes:
  - `GET /lots/hardware` — hardware_lot rows: product, quantity, cost per unit, expected profit, success rate and repair cost used; `?photo_finds_only=1` filter; 💎 on finds.
  - `GET /lots/games` — game_lot rows: platform, count, cost per game vs market per game, profit; filters `platform`, `ending_within_h`, `photo_finds_only`.
  - `GET /listing/{id}` — cost breakdown, comps behind the value, scan section (photos beside a table of items: title, platform, completeness, confidence, value), "Scan this lot" button for unscanned candidates.
  - `POST /listing/{id}/scan` — calls `scan_listing(id, manual=True)` in a background thread; responds with "Scanning…", or the gate reason ("daily cap reached") when denied.
  - `GET /catalog` — three sections (products, bulk products, platforms) with enable switch, max price / repair cost / success rate inputs, current market values; forms for a custom product and a custom bulk product (regex validated with `re.compile`; error shown inline).
  - `POST /catalog/{table}/{id}` (form fields per table) and `POST /catalog/custom/{kind}`.

- [ ] **Step 1: Write failing tests:** `test_hardware_lots_page`, `test_game_lots_page_per_game_columns`, `test_listing_detail_shows_comps_and_scan_items`, `test_manual_scan_denied_shows_reason`, `test_manual_scan_runs_when_switch_off`, `test_catalog_override_saved`, `test_custom_product_bad_regex_rejected`, `test_custom_product_added`.
- [ ] **Step 2:** Run `pytest tests/test_web.py -v`; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `pytest -v`; expect PASS.
- [ ] **Step 5: Commit** `feat: lot pages, listing detail and catalog editor`.

---

### Task 19: README, example config and end-to-end check

**Files:**
- Create: `README.md`, `config.example.yaml`, `tests/test_config_example.py`

**Interfaces:**
- Consumes: `load_config`, `create_app`.

- [ ] **Step 1: Write failing test** `test_example_config_loads_and_matches_defaults` — `load_config(Path("config.example.yaml"), env={})` equals `load_config(None, env={})` (the example documents every default without changing any).
- [ ] **Step 2:** Run it; expect FAIL (file missing).
- [ ] **Step 3: Write `config.example.yaml`** — every `Config` field with its default and a one-line comment.
- [ ] **Step 4: Write `README.md`** covering: install (`pip install -e .`), running (`python -m dealfinder`), getting eBay developer keys, creating a Discord webhook, setting `ANTHROPIC_API_KEY`, **setting a monthly spend limit for the key in the Anthropic Console** (§11.7), how photo scanning is off by default and where the switch and caps are, cost per scan ($0.05–0.12), the fragile parts (ShopGoodwill/HiBid shapes, eBay sold page), and how to capture a real response (browser dev tools → Network → copy response) into `tests/fixtures/<site>/` and fix the adapter's constant block.
- [ ] **Step 5:** Run `pytest -v`; expect all PASS. Run `python -m dealfinder`, load each page (`/`, `/lots/hardware`, `/lots/games`, `/catalog`) and confirm a 200 with no server errors in the log. Stop it.
- [ ] **Step 6: Commit** `docs: readme and example config`.
