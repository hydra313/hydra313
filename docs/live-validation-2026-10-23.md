# Live validation: October 23, 2026

Environment: Windows, Python 3.13.15, a fresh editable installation with the dev
dependencies. No eBay, Anthropic or Discord credentials were configured. Photo
scanning remained off and no paid scans or Discord messages were sent.

## Confirmed failures and fixes

- ShopGoodwill returned HTTP 200 with zero listings for every original search.
  Adding explicit low/high price bounds returned 36 RTX listings. The corrected
  body also uses `searchClosedAuctions`, a numeric sort column, and
  `searchNoPickupOnly` to request shippable items.
- ShopGoodwill thumbnails contain backslashes and detail photos are relative to
  `imageServer`. Both are normalized into usable HTTP(S) URLs; unsafe or
  unresolved paths are discarded. Minimal public response fixtures cover the
  exact formats seen live.
- ShopGoodwill Buy Now listings now use `buyNowPrice` and are not treated as
  auctions. This case has a regression test; the live RTX sample contained
  auctions and hybrid listings rather than a Buy Now-only listing.
- The live title `Intel Core i7-7700 SR338 3.60GHz Desktop Processor` was
  classified as an AMD Ryzen 7700. Explicit conflicting manufacturers now reject
  a CPU match, including generated bulk products and comparable-sale matching.
- The live Xbox Controller S title ending in `for Xbox Console Black` was
  classified as a console lot. Compatibility phrases no longer decide the lot's
  hardware type. A `3pc Sony PlayStation Console Lot` now yields a quantity of
  three instead of requiring a photo scan to count it.
- Unpriced listings and unknown-quantity lots could not be reached from the
  dashboard because every list required a deal score. The opt-in **Show all
  listings** filter exposes them without marking them as bargains. Existing
  category, source, platform, photo-find, dismissal and expiry filters still apply.
- A refresh updated the status header but left listing rows unchanged. Completion
  now triggers an HTMX row refresh using the current filters.
- eBay sold-price failures were only in the logs. They now appear in the header.
  A bot block suspends further sold requests for the current refresh instead of
  repeating the blocked request for every product. The next refresh can recover.
- Manual-scan requests could report “Scanning” when no API key was available or
  the listing was ineligible. The endpoint now checks eligibility before starting
  a background thread; the pipeline rechecks dismissal under the scan lock.
- HiBid GraphQL errors in HTTP 200 responses now raise a source error instead of
  silently producing an empty search.

## Verification

- Original baseline: 817 passing tests.
- Updated suite: 840 passing tests, including 23 added regression cases.
- `git diff --check`: clean.
- A corrected live refresh returned 511 search results across queries (including
  repeats) and stored 95 distinct relevant listings before the CPU matching fix.
  Three stored single-item CPUs exposed the manufacturer collision above.
- A fresh run after the manufacturer fix stored 92 relevant listings (9 single
  items, 12 hardware lots, 31 game lots, 40 unknown-quantity lots). No Intel
  title was classified as Ryzen.
- Browser checks covered navigation, showing real results, pausing/resuming,
  refreshing, and saving/clearing a catalog price while preserving its enabled
  checkbox. Refresh completion updated the rows without a page reload and kept
  the Show all listings filter checked.

On this Windows environment, sandboxed TestClient runs stalled while opening
loopback sockets. The complete suite passed outside that sandbox; this was an
environment restriction rather than an application failure.

## Remaining limits

- HiBid returned HTTP 403 (Cloudflare); its assumed schema could not be validated.
- eBay's sold-listings page returned HTTP 403. No live sold valuations were
  obtained. The authenticated eBay Browse fallback also needs credentials.
- Anthropic scans, provider billing/fallback behavior, and actual Discord delivery
  were exercised only through the existing mocked tests, not live credentials.
- Search adapters still fetch their first results page only. This is a partial
  view of each marketplace, especially for broad queries.
- Listings without an explicit shipping amount use estimates. Confirm condition,
  shipping, fees and final auction price on the marketplace before bidding.
