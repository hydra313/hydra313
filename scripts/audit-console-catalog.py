"""Export one disposition for every sourced console record, plus the watchlist."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dealfinder.catalog import load_seed
from dealfinder.console_editions import audit_console_editions, REFERENCE_BASE, REFERENCE_FILE

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=ROOT / '.audit')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
catalog = load_seed(ROOT / 'catalog')
curated = [p for p in catalog.products if not p.required_tokens]
counts, decisions = audit_console_editions(ROOT / 'catalog' / REFERENCE_FILE, curated)
data = json.loads((ROOT / 'catalog' / REFERENCE_FILE).read_text(encoding='utf-8'))
by_slug = {row['slug']: row for row in data['records']}

def cell(value):
    text = str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@')) else text

with (args.output / 'console-reference-audit.csv').open('w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(['Source record', 'Platform', 'Release type', 'Special status',
                     'Disposition or catalog ID', 'Source review flags', 'Source URL'])
    for slug, decision in decisions:
        row = by_slug[slug]
        writer.writerow(map(cell, [row['name'], row['platform'], row['release'], row['limited'],
                                   decision, ', '.join(row.get('review_flags', [])), REFERENCE_BASE + slug]))
with (args.output / 'console-watchlist.csv').open('w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(['Catalog ID', 'Name', 'Promotional', 'Reference URL', 'Discovery query'])
    for product in catalog.products:
        if product.category == 'console':
            writer.writerow(map(cell, [product.id, product.name, product.promotional,
                                       product.source_url or '', product.discovery_query or product.ebay_query]))
print(json.dumps({'as_of': data['as_of'], 'source_records': len(decisions),
                  'console_editions': sum(p.category == 'console' for p in catalog.products),
                  'dispositions': counts}, indent=2))
