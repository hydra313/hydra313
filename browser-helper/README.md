# Dealfinder eBay browser helper

1. Extract this folder somewhere permanent.
2. In Chrome or Edge, open the Extensions page, enable Developer mode, and use
   Load unpacked to select this folder (the folder containing manifest.json).
3. Open http://127.0.0.1:8000/sources/ebay in that browser, reload, and click
   Connect and start. Keep the browser and Dealfinder running.

The helper checks one catalog query every 30 seconds, with one background tab
at a time. Chrome may delay alarms during sleep or heavy throttling. It stops
on verification, unreadable search cards, or an import failure. Complete any
verification yourself in the tab it leaves open, then reconnect. App Pause
pauses new searches. Stop browser helper disables the helper's own loop.

Only visible USD Buy It Now search cards are collected. Auction closing times
and sold-price comparisons are not supplied by this helper. A missing resale
estimate is not evidence that an item is a deal. Confirm prices before buying.

Permissions are limited to eBay and your local app, plus local storage and
alarms. It reads no cookies, account pages, payment data or passwords; it places
no orders or bids and sends captures only to 127.0.0.1:8000. Captures contain
listing title, price, purchasing/shipping/condition text, item URL and image URL.
Your other eBay tabs are not imported. No remote code or telemetry is loaded.
