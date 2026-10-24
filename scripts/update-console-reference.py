"""Build the factual reference snapshot from a downloaded public search index.

No network access or runtime catalog mutation. Inspect the resulting Git diff and
run the console tests before publishing a new snapshot.
"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dealfinder.console_editions import reference_review_flags

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('index', type=Path)
parser.add_argument('--as-of', required=True, type=date.fromisoformat)
parser.add_argument('--output', type=Path,
                    default=Path(__file__).resolve().parents[1] / 'catalog/console_editions.json')
args = parser.parse_args()
raw = args.index.read_bytes()
records = json.loads(raw)
if not isinstance(records, list):
    raise ValueError('The public search index must be a JSON list')
data = json.loads(args.output.read_text(encoding='utf-8')) if args.output.exists() else {}
data.update(source='https://consolevariations.com/media/search-index.json',
            as_of=args.as_of.isoformat(), index_sha256=hashlib.sha256(raw).hexdigest(),
            scope='Source-marked official/licensed console records with special-release status. '
                  'Eligibility and exclusions are documented in docs/console-catalog-audit.md.',
            records=[{'slug': row['s'], 'name': row['n'], 'platform': row['p'],
                      'release': row['rt'], 'limited': row['le'], 'bundle': bool(row.get('bu')),
                      'review_flags': reference_review_flags(row.get('de') or '')}
                     for row in records if row.get('y') == 'consoles'
                     and row.get('rt') in ('official', 'licensed')
                     and row.get('le') in ('yes', 'exclusive', 'sweepstake', 'prize')])
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print(f'Saved {len(data["records"])} factual records to {args.output}')
