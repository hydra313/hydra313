# Hardware-only scope update

Game lots have been retired from ConsoleHawk. Their navigation, listing template,
catalog controls, alert option and discovery queries are removed. Existing config
queries for game lots are filtered out, old bookmarks redirect to Deals, and old
platform overrides cannot reactivate game-lot collection. Platform names remain
internal so console model numbers and bundled-game quantities are interpreted
correctly. Historical records stay in the database.

The screenshot examples are rejected throughout ingestion, saved search results,
sold-comparison validation and notification delivery:

- ASUS ROG XG Mobile, external GPU docks/enclosures and eGPU systems.
- Cameras and camcorders, including Vivitar PZ 3090 and Nikon Coolpix model numbers
  that overlap graphics-card names.
- Mobile computers such as HP Omen, Razer Blade and Dell XPS, even without the word
  “laptop” in the title; mobile HX processor titles are also excluded.
- Controllers cannot inherit a complete console's valuation. The observed
  Killscreen PS5 Pro 30th Anniversary gamepad no longer matches a full PS5 console.

Empty-box/manual listings, cracked displays sold as complete single consoles, and game collector editions masquerading as consoles are also excluded.

Shared title and category checks also hide old mistakes without deleting records.
Contaminated valuation caches are ignored and rebuilt from matching evidence.
Traditional supported controllers and complete consoles bundled with a controller
remain covered by regression tests. Hardware-lot discovery is retained.

Verification: the exact screenshot titles first failed new regression tests, then
passed after the fixes. Live searches with “Include unpriced and lower-margin
listings” enabled returned zero Vivitar, XG Mobile and eGPU results. The retired
Game lots page redirects, and the sidebar now has Deals, Bulk lots, Catalog and
Connections. No browser-helper update or new API key is needed for this change.

Validation: 1,072 Python tests and 12 browser-helper workflow tests passed.
