# Special-edition console catalog audit

Audit date: October 23, 2026.

The catalog grew from **29 to 1,774 console identities**: the original 29,
eight additional publisher-confirmed entries, two separately identified factory
colorways, and 1,735 additional identities derived from a collector reference
snapshot. Existing IDs, overrides and price
targets are preserved. No database reset is required.

## Coverage and sources

The [Consolevariations public directory](https://consolevariations.com/browse/type/consoles)
and its [public search index](https://consolevariations.com/media/search-index.json)
were used to audit 3,734 records marked as official/licensed consoles with a
limited, exclusive, sweepstake or prize designation. Only factual names, platform
labels, release classifications, bundle flags and source identifiers are stored;
descriptions, images and site scripts are not copied. The snapshot records its
date and the downloaded index's SHA-256 hash. Descriptions are checked during
snapshot creation for authenticity investigations, unreleased/prototype status,
and unofficial modifications; only the resulting review flags are retained.
An official release label does not override an explicit authenticity warning.

The resulting catalog spans Nintendo, PlayStation, Xbox, Sega and other older
hardware, including home systems and handhelds. New identities include Gears of
War and Modern Warfare Xbox 360 systems; God of War, Spider-Man, Kingdom Hearts
and Death Stranding PS4 Pro systems; numerous Game Boy, DS, 3DS, PSP and Vita
editions; Switch variants; and promotional systems.

Publisher references checked during the audit include:

- [Sony: Ghost of Yotei Gold and Black PS5 systems](https://blog.playstation.com/2025/07/10/first-look-ps5-ghost-of-yotei-limited-edition-console-designs/).
- [Sony: God of War PS4 Pro](https://blog.playstation.com/archive/2018/03/06/introducing-the-god-of-war-limited-edition-ps4-pro-bundle/).
- [Sony: Batman Arkham Knight PS4](https://blog.playstation.com/archive/2015/03/31/limited-edition-batman-arkham-knight-ps4-unveiled/).
- [Xbox: Gears of War 3 Xbox 360](https://news.xbox.com/en-us/2011/06/06/announcing-the-xbox-360-gears-of-war-3-limited-edition-console/).
- [Xbox: Series X25 announcement](https://news.xbox.com/en-us/2026/08/26/pre-orders-begin-today-xbox-25th-anniversary-collection/). Its console release is November 13, 2026; the catalog identifies it as an announced release rather than implying it is already available.
- [Nintendo: Switch Lite lineup](https://www.nintendo.com/my/hardware/detail/switch-lite/index.html), covering Hyrule and the two Animal Crossing Aloha designs.
- [Nintendo: Switch Lite Zacian and Zamazenta](https://www.nintendo.com/ph/hardware/switch/modal/lineup_lite.html?width=960) and [Dialga and Palkia](https://www.nintendo.com/us/store/products/nintendo-switch-lite-dialga-palkia-edition/).
- [Nintendo: Animal Crossing Switch](https://www.nintendo.com/en-ca/store/products/nintendo-switch-animal-crossing-new-horizons-edition/).
- [Nintendo: Switch 2 Zelda 40th Anniversary](https://www.nintendo.com/us/store/products/nintendo-switch-2-the-legend-of-zelda-40th-anniversary-edition-121642/). Its listed release is October 29, 2026, after this audit date. It is present as an announced edition.

The eight publisher-confirmed additions are Ghost of Yotei Gold and Black, five Switch Lite
editions (Hyrule, Zacian/Zamazenta, Dialga/Palkia, Isabelle Aloha, Timmy/Tommy
Aloha), and Series X25. Their entries link to the relevant publisher sources. Two additional curated
factory-color identities separate the orange Pikachu N64 and blue Hello Kitty
Dreamcast from the older generic/blue N64 and generic/pink Dreamcast entries.

## Every reference record is accounted for

| Disposition | Records |
| --- | ---: |
| New edition identity | 1,735 |
| Already covered by a curated identity | 76 |
| Regional/duplicate hardware signature | 175 |
| Software/packaging bundle; distinctive hardware requires review | 1,510 |
| Source authenticity, unofficial or unreleased status requires review | 20 |
| No distinctive edition identifier | 24 |
| Accessory/game rather than identifiable edition hardware | 40 |
| Inconsistent or ambiguous platform metadata | 62 |
| Multiple-design record; individual hardware requires review | 21 |
| Prototype, custom, signed, accessory or display record | 55 |
| Secondary bundle/accessory description requires review | 16 |
| **Total** | **3,734** |

This is a dated, source-backed expansion, not a claim that every special console
ever produced has been independently authenticated. Collector metadata can be
wrong, regional packaging is not always a different hardware edition, and some
records represent several designs. Ambiguous records remain in the audit instead
of being given a made-up identity or valuation. Unlicensed aftermarket designs
are excluded from active matching. Rare promotional hardware still needs its
physical provenance checked before purchase.

Hardware signatures group regional packaging; they do not enforce region locks,
voltage or box completeness when selecting market comparisons. Those details
can change value and still need checking against the auction and its comparisons.

## Matching and usability

- An edition needs its correct hardware family and distinctive words. Pro/Slim,
  original/New, XL/LL, Lite/OLED and successive generations stay separate.
- Curated aliases retain stable IDs. The older Pikachu 3DS XL is now supported
  separately from the existing New 3DS XL Pikachu entry.
- Promotional records require a promotional/prize/contest marker or an explicit
  limited-edition-console title; an ordinary game bundle is insufficient.
- Games, controllers, docks, replacement shells, custom modifications, empty
  boxes and broken single items are rejected. Accessories after "with" may
  accompany a complete console.
- Edition names containing "Collection" and Final Fantasy X/X-2 are handled as
  names rather than accidental lot quantities. Explicit lot counts still work.
- Disabling a specific reference edition cannot silently classify it as a
  less-specific different edition.
- Each sourced product has a reference link. Catalog search supports names,
  identifiers and platform aliases; products are paged in groups of 200. Custom
  products remain visible on the first page.
- New reference editions share eBay discovery queries by hardware family,
  rather than creating thousands of separate searches. Broad source queries
  now include older hardware families. Source adapters still fetch one page per
  search, so catalog coverage does not imply exhaustive auction coverage.
- No prices, profits or target thresholds are invented. Values still come from
  the existing market service; source failures and missing values remain visible.

## Reproducing the audit

From the repository with its installed Python environment:

```
python scripts/audit-console-catalog.py
```

This writes `console-reference-audit.csv` (one disposition per source record)
and `console-watchlist.csv` (all console identities) to the ignored `.audit/`
folder. `--output` selects another directory.

To update the snapshot, download the public index, then run:

```
python scripts/update-console-reference.py path/to/search-index.json --as-of YYYY-MM-DD
```

Inspect the diff, preserve reviewed identity mappings/release notes, run the
console and full regression suites, and regenerate the audit. No website data
is downloaded automatically by catalog loading or matching.

Validation includes every new identity's own name, complete console
classification, known missing releases, accessory/game/custom negatives,
original/New model collisions, real lot counts, disabled-edition behavior,
bounded discovery searches, catalog search/pagination, and the existing suite.

The final full regression run passed **916 tests**. Live browser checks verified
200-row pagination, a page-two transition, keyword search, publisher reference
links, and the narrow layout without horizontal page overflow.
