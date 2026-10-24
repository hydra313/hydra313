# ConsoleHawk refinement — October 24, 2026

This release follows the user's request to prioritize limited-edition consoles,
exclude accessories, make alerts selectable, remove paid photo analysis and
simplify the interface. It supersedes the original photo-analysis UI and
all-results default requirements.

## Changes

- eBay keyless collection supports auctions and Buy It Now. Auction pages use
  ending-time order and require a visible closing countdown. Default discovery
  no longer requests only fixed-price listings or US-only location. USD and
  shipping evidence guards remain. Card captures grow from 60 to 240 per search;
  full pages queue up to five pages, bounded to avoid monopolizing the monitor.
- Console-focused browser discovery includes explicit Halo 3 and PS2 searches
  plus every enabled sourced console-family query. Other hardware still rotates.
  HiBid and ShopGoodwill also expand their configured queries with those families.
  ShopGoodwill follows up to five 40-item pages instead of stopping after page one.
- Search text/platform filters queue both eBay buying formats without network
  work in the page request. Repeated auto-refreshes cannot reset the queue.
  Platform aliases and family-aware matching avoid PS4-with-2-controllers results
  when searching PS2. Numeric/model words match complete tokens.
- PS2 matching accepts Hyakushiki, Sakura Pink, normal box/controller bundles and
  SCPH-7xxxx/9xxxx Slim identification. Factory identity and accessory guards remain.
- Fans, loose buttons, protective cases, replacement parts, fight sticks, leverless
  controls and other nontraditional game controllers are excluded before any lot
  routing. The same guard hides historical bad rows and invalidates contaminated
  comparison caches without deleting their evidence. Ordinary black Xbox bundles
  with Halo games cannot inherit the limited-edition valuation. Explicitly used
  titles override a conflicting new condition.
- Per-item Discord selections are separate from catalog search enablement. Check
  Alert me and Save in Catalog, then choose Selected catalog items only and the
  desired reason types under Connections. Unselected items never consume their
  notification claim; deduplication, dismissed/ended/stale checks remain enforced.
- Default listing views show only fresh, reliable good sold-based deals. Unknown,
  low-margin and overpriced rows need explicit inclusion. Current sold averages
  determine displayed single-item profit, including current fee/shipping assumptions.
- Photos are 120px desktop/150px mobile, with larger text and fewer table columns.
  Metadata sits under titles; secondary filters collapse. Redundant source links,
  Target hits filter, paid scan/budget controls and scan detail section are removed.
  Legacy scan action URLs return 410, and no production factory creates an API scanner.
- Idle header polls no longer invalidate rows every 3/10 seconds. Revision checks,
  per-request value reuse and cached pure title checks reduce repeated work.
  Deals paginates by 100, with counts and navigation, preserving page/filter state.

## Verification

- 1,049 Python tests passed, plus 12 JavaScript helper workflow tests.
- New failures were reproduced before the matching, auctions, subscriptions,
  default visibility, paging, search-alias and used-condition fixes.
- Public rendered eBay auction fixture: 123 cards, 121 usable auctions with visible
  countdowns. Missing closing times and ambiguous prices remain excluded.
- Public PS2 sample: 155 cards, 147 usable, 89 classified matches after seller-title
  fixes (73 before). Related results can contain other console families; the catalog
  matcher, generation-aware search and UI family filter constrain displayed matches.
- Fresh end-to-end PS2 import into the actual app: 157 cards, 148 usable, 90 matches,
  HTTP 200, about 1.18 seconds. No notifications or scans run from this import path.
- Current real DB retained (~5,200 listings, ~9,200 comps). Profile over 3,705 single
  items: about 10.73s before optimization; 2.20s cold/1.55s warm after value/title reuse.
  These are local data-preparation measurements, not marketplace response-time promises.
- Live UI check: 43 relevant PS2 consoles in the explicit all-results view. The
  default page showed 63 good deals and zero average/overpriced/unknown rows.
  At 390px viewport width, images measured 150px and no page overflow occurred.
- Existing secrets, local DB, catalog overrides and connection settings preserved.

## Remaining operational limits

Browser helper 1.3.0 files have replaced the existing installed folder at
`C:\Downloads\dealfinder-browser-helper`, with a backup in the work directory.
The installed Chrome extension still needs Reload, then Connections → Connect both
sites. Windows UI automation stopped because it could not confidently verify the
current browser URL, so that final Chrome step could not be completed automatically.
No new host permissions were added in this update.

Searches rotate every 30 seconds and have bounded pages. Coverage spans the known
catalog and family queries; it is not a guarantee that every edition/listing on the
internet has been discovered. HiBid still requires visible USD, shipping and closing
information. Verification/sign-in is completed by the user. Direct eBay sold HTTP
requests and legacy HiBid API can be blocked; browser collection remains the route.
Unknown/weak sold evidence is shown honestly and cannot produce a default green deal.
