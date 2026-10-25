# HiBid automatic search — October 24, 2026

Browser helper 1.2 now rotates HiBid searches automatically. Open Connections,
update/reload the extension, allow HiBid permission and click Connect HiBid (or
Connect both sites). It captures only the helper-owned search tab, sanitizes the
visible listing fields and submits them through capability-protected local routes.
Other browsing tabs and authentication/session fields are never captured.

One query is checked every 30 seconds across enabled sites. Verification stops
only the affected site and preserves its tab for the user. Empty searches continue
the rotation. Captures keep their real observation time and expire after 30 minutes.
Only USD lots with shipping, a payable bid and a closing countdown are eligible.

Live check on October 24: 69 rendered RTX search cards exposed the expected title,
money, countdown, shipping and bid selectors. The automated workflow is covered by
Python route/security tests and browser-helper tests. Direct API access remains
blocked. The one-time updated extension permission must be granted in the user's
browser before unattended HiBid collection can run.

The original manual import remains an optional fallback, documented below.

## Original manual-import validation — October 23, 2026

Direct HiBid HTTP requests returned Cloudflare HTTP 403 while a normal browser
could load search results. The app now defaults to importing rendered browser
cards instead of repeatedly calling the blocked API.

## Use it

1. Open the dashboard's **HiBid import** page.
2. Open a search link and wait for HiBid's results. Complete any verification
   yourself in that browser tab.
3. Copy the loaded page with Ctrl+A / Ctrl+C and paste into the import box, or
   save it as Webpage, Complete and upload the HTML file (maximum 5 MB).
4. Confirm the capture time and click **Import HiBid results**.
5. Open imported listings. Refresh the browser page and repeat when prices expire.

The default lifetime is 30 minutes, configurable from 1 to 120 minutes. Refreshes
and rescoring preserve the original capture time. Older captures cannot overwrite
newer database prices. Expired captures are hidden and cannot trigger scans or
alerts. Importing itself never starts paid scans or sends alerts.

## Live evidence

A real HiBid RTX search was copied from the browser and pasted through the app:

- 68 rendered cards parsed.
- 3 usable active USD lots with shipping.
- 1 catalog match: Gigabyte GeForce RTX 5070 Ti WINDFORCE 16G, bid $625.
- 48 cards excluded for pickup-only or unknown shipping, 16 for missing USD
  prices, and 1 for a missing closing countdown.
- The matched listing appeared in the dashboard with a Browser snapshot badge
  and estimated total cost $738.75 (configured shipping and buyer premium).

The parser fixture contains five sanitized real cards. Regression coverage also
checks copied fragments, non-USD exclusions, minimum bids, malformed pages,
URL validation, stale data, uploads, CSRF and large clipboard HTML.

## Limits

Captures require user interaction; this does not provide unattended HiBid search
or bypass verification. Only loaded results are imported. Cards with unknown
shipping, other currencies, upcoming/closed auctions or no countdown are skipped.
Shipping, tax and buyer premiums must be verified against the auction terms.

Only normalized listing fields are stored, using existing database tables; no
schema migration or database reset is needed. Raw HTML, scripts, cookies and
browser session data are not saved in the database. External links are restricted
to HiBid; image URLs are restricted to its image CDN.

eBay sold-price requests remain blocked (HTTP 403/418 in live checks), so a
listing without a market value must not be treated as a confirmed bargain.
