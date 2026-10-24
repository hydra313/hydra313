"""Opt-in smoke test of the shipped capture code in a real hidden browser."""
import os
from pathlib import Path
from datetime import datetime, timezone
import pytest
from dealfinder.collector import CAPTURE_JS, capture_problem
from dealfinder.sources.ebay_browser import parse_active_page
from dealfinder.sources.hibid_browser import parse_browser_page
from dealfinder.sources.ebay_sold import parse_sold

pytestmark=pytest.mark.skipif(os.environ.get('CONSOLEHAWK_BROWSER_SMOKE')!='1',reason='Opt-in browser smoke test')

@pytest.mark.parametrize('site,fixture', [('ebay','ebay/live-ps2-editions.html'),('ebay','ebay/live-halo-auctions.html'),('hibid','hibid/browser_search.html'),('ebay','ebay_sold/s_card.html')])
def test_capture_script_preserves_public_results_without_account_data(site,fixture):
 from playwright.sync_api import sync_playwright
 html=(Path(__file__).parent/'fixtures'/fixture).read_text(encoding='utf-8')
 url='https://www.ebay.com/sch/i.html?_nkw=ps2' if site=='ebay' else 'https://hibid.com/lots?q=ps2'
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='msedge',headless=True,chromium_sandbox=True)
  page=browser.new_page()
  page.route('**/*',lambda route:route.fulfill(body=html+'<form><input value="private-password"></form><div>private-account-name</div>',content_type='text/html') if route.request.is_navigation_request() else route.abort())
  page.goto(url)
  captured=page.evaluate(CAPTURE_JS.read_text(encoding='utf-8'),site)
  browser.close()
 assert not capture_problem(captured,site)
 assert 'private-password' not in captured['html'] and 'private-account-name' not in captured['html']
 at=datetime.now(timezone.utc)
 if 'ebay_sold' in fixture: assert parse_sold(captured['html'])
 elif site=='ebay': assert parse_active_page(captured['html'],at).listings
 else: assert parse_browser_page(captured['html'],at).listings
