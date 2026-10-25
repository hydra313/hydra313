# Sold averages and continuous discovery: validation

Validated October 24, 2026 on the user's Windows PC.

- Automatic source checks run every two minutes while the app runs. Refresh displays the source, query progress, new/updated counts, completion, and failures. Status continues polling between scheduled checks; listing rows update incrementally.
- eBay browser helper 1.1 supports one query every 30 seconds, alternating active Buy It Now discovery and needed sold-price evidence. It needs one-time Chrome/Edge installation and connection; the agent has not installed it into an external browser. Worker behavior is covered by eight JavaScript workflow tests.
- The user signed in to eBay in the in-app browser. A real rendered active page had 62 cards, 59 usable fixed-price records, and 52 catalog matches (51 singles, with the remaining match routed by classification).
- A real sold page had 242 cards including placeholders; 197 usable dated sold records were retained. Exact matching for the standard used RTX 4070 and outlier filtering retained 59 comparisons, averaging $613.64 including known shipping. The new/open-box sample had only one comparison and remains gray/low confidence.
- A live ShopGoodwill listing displayed $381 current price, $406 estimated total cost, $613.64 average sold value, $106.33 estimated profit, and a green 34%-below-average rating. Its evidence link opened all 59 retained comparisons. Higher-priced offers showed red when estimated profit was zero or negative. No synthetic prices were added to the user's database.
- Fresh values use an arithmetic mean after IQR filtering and the method `sold_average`. Older median caches remain labeled legacy values and cannot receive sold-average colors. Active asking-price estimates also remain separate. Unknown, sparse, stale, and unknown-quantity cases receive gray ratings.
- Percentage uses landed purchase cost, not just bid price. Profit also deducts configured selling fees, resale shipping, and applicable repair assumptions. Green requires configured profit and discount targets; yellow means a positive margin below those targets; red means no remaining estimated profit.
- Pick-up-only cards, hidden accepted-offer prices, duplicate sold IDs, mismatched models/conditions, and sold dates outside 90 days are excluded. Cookies, account pages and passwords are not captured or replayed. No purchases or bids occur.
- Photo scanning remains off. Spend and notification guards remain unchanged. HiBid still requires fresh manual captures.

Validation: 977 Python tests passed; eight browser-helper workflow tests passed. Live browser checks covered real listing import, sold import, progress feedback, green/red/unknown rendering, and the 59-row evidence table. The external Chrome/Edge installation and unattended helper run remain a one-time setup step for the user.
