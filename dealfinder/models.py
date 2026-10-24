from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Category = Literal["gpu", "cpu", "console", "controller"]
Condition = Literal["new", "used"]
Completeness = Literal["loose", "cib", "sealed", "unknown"]
ListingKind = Literal["single", "hardware_lot", "game_lot", "scan_only"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class Product:
    id: str
    category: Category
    name: str
    ebay_query: str
    match: str
    exclude: tuple[str, ...] = ()
    max_price: float | None = None
    enabled: bool = True
    required_tokens: tuple[str, ...] = ()  # sourced console edition identifiers
    source_url: str | None = None
    discovery_query: str | None = None  # share searches across a console family
    promotional: bool = False


@dataclass(frozen=True)
class BulkProduct:
    id: str
    category: Category
    name: str
    ebay_query: str
    match: str
    repair_cost: float
    success_rate: float
    enabled: bool = True
    source_product_id: str | None = None


@dataclass(frozen=True)
class Platform:
    id: str
    name: str
    match: str
    ebay_lot_query: str
    enabled: bool = True


@dataclass
class Listing:
    source: str
    source_id: str
    title: str
    url: str
    price: float
    # shipping=None with shipping_offered=True means "ships, cost unknown";
    # shipping_offered=False means pickup only.
    shipping: float | None
    shipping_offered: bool
    image_urls: list[str]
    is_auction: bool
    end_time: datetime | None
    bid_count: int = 0
    buyer_premium_pct: float | None = None
    condition_id: str | None = None
    observed_at: datetime | None = None  # browser snapshot time; never renewed by refresh


@dataclass
class Classified:
    kind: ListingKind
    key: str  # product, bulk-product or platform id
    condition: Condition | None
    quantity: int | None  # None = unknown
    category: Category | None


@dataclass
class Comp:
    title: str
    price: float  # includes shipping
    sold_date: date | None
    url: str
    condition_label: str | None


@dataclass
class MarketValue:
    key: str
    condition: str
    value: float
    comp_count: int
    method: Literal["sold", "active_bin"]
    low_confidence: bool
    computed_at: datetime


@dataclass
class Scored:
    total_cost: float
    shipping_estimated: bool
    profit: float | None
    discount_pct: float | None
    is_target_hit: bool
    is_deal: bool
    rank_score: float


@dataclass
class ScanItem:
    kind: str
    platform: str
    title: str
    edition: str | None
    completeness: Completeness
    quantity: int
    confidence: Confidence
    photo_index: int  # position in the listing's image_urls; -1 = unknown photo


@dataclass
class ScanResult:
    status: Literal["ok", "refused", "failed"]
    items: list[ScanItem]
    unit_count_estimate: int | None
    notes: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None = None
