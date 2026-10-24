# Dealfinder browser helper 1.2

1. Extract this folder somewhere permanent.
2. In Chrome or Edge, open Extensions, enable Developer mode and Load unpacked
   this folder. Existing users: replace the files, click Reload on the extension,
   and allow its new HiBid permission.
3. Open http://127.0.0.1:8000/connections in that browser, reload, and click
   Connect both sites. Individual Connect and Stop buttons are available too.

The helper checks one query every 30 seconds in one background tab, alternating
across enabled sites. eBay jobs alternate Buy It Now discovery and needed sold
comparisons. HiBid rotates configured catalog search words. No copying or manual
imports are needed. Keep this PC, browser and Dealfinder running. App Pause stops
new searches; browser sleep or throttling can delay checks.

Verification, unreadable cards or import failures stop only the affected site.
Its tab stays open for the user to finish verification; reconnect that site when
ready. The helper never solves verification or replays authentication tokens.

Permissions cover eBay, HiBid and the local app, plus storage and alarms. Only
helper-owned search tabs can send captures. Account pages, cookies, passwords,
payment details and ordinary browsing tabs are not captured. Captures contain
only visible listing facts and go only to 127.0.0.1:8000. No bids or orders are
placed and no remote code or telemetry is loaded.

HiBid accepts only USD lots with shipping and a closing countdown. Captures expire
after 30 minutes. eBay accepts fixed-price USD cards and dated sold prices from
the last 90 days; undisclosed accepted offers are excluded. Sold searches can
require user sign-in. Costs and premiums may be estimated; confirm before bidding.
