from datetime import datetime, timedelta, timezone

import pytest

from dealfinder import db
from dealfinder.config import Config
from dealfinder.sources.base import SourceError
from dealfinder.sources.ebay_sold_browser import BrowserSoldFetcher, save_sold_capture

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def sold_card(number='123456789123', date='Oct 23, 2026', extra='', price='$700.00'):
    return f'''<li class="s-card"><a class="s-card__link" href="https://www.ebay.com/itm/{number}"></a>
    <div class="s-card__title">RTX 4090</div><div class="s-card__price">{price}</div>
    <div class="s-card__subtitle">Pre-Owned</div><div class="s-card__caption">Sold {date}</div>
    <div class="s-card__attribute-row">Free shipping</div>{extra}</li>'''


class NoNetwork:
    def request(self, *args, **kwargs):
        raise AssertionError('browser evidence must avoid a network request')


def test_sold_capture_deduplicates_and_preserves_evidence(tmp_path):
    conn=db.connect(tmp_path/'test.db')
    assert save_sold_capture(conn,'rtx 4090',sold_card()*2,NOW) == 1
    fetcher=BrowserSoldFetcher(NoNetwork(),conn,Config(),lambda:NOW)
    comps=fetcher.fetch('rtx 4090')
    assert len(comps)==1 and comps[0].price==700
    assert comps[0].sold_date.isoformat()=='2026-10-23'
    assert fetcher.captured_after('rtx 4090',NOW-timedelta(seconds=1))
    conn.close()


@pytest.mark.parametrize('kwargs', [dict(date='Oct 25, 2026'),dict(date='Jan 1, 2025'),
    dict(extra='<div>Best offer accepted</div>'),dict(price='C $700.00'),
    dict(extra='<script>private cookies</script>',date='not a date')])
def test_sold_import_rejects_unknown_sale_prices_old_or_future_sales(tmp_path,kwargs):
    conn=db.connect(tmp_path/'test.db')
    assert save_sold_capture(conn,'rtx 4090',sold_card(**kwargs),NOW)==0
    conn.close()


def test_login_page_cannot_replace_captured_evidence(tmp_path):
    conn=db.connect(tmp_path/'test.db')
    save_sold_capture(conn,'rtx 4090',sold_card(),NOW)
    with pytest.raises(ValueError,match='Sign in'):
        save_sold_capture(conn,'rtx 4090','<h1>Sign in</h1>',NOW)
    assert len(BrowserSoldFetcher(NoNetwork(),conn,Config(),lambda:NOW).fetch('rtx 4090'))==1
    conn.close()


def test_new_browser_evidence_bypasses_cached_active_estimate(tmp_path):
    from dealfinder.market import MarketService
    from dealfinder.catalog import Catalog
    from dealfinder.models import Product, MarketValue
    conn=db.connect(tmp_path/'test.db')
    p=Product('4090','gpu','RTX 4090','rtx 4090',r'\brtx 4090\b')
    db.save_market_value(conn,MarketValue('4090','used',1200,8,'active_bin',True,NOW-timedelta(minutes=5)),[])
    save_sold_capture(conn,'rtx 4090',sold_card(),NOW)
    service=MarketService(conn,Config(),BrowserSoldFetcher(NoNetwork(),conn,Config(),lambda:NOW),None,
                          Catalog(products=[p],bulk=[],platforms=[]),lambda:NOW)
    value=service.product_value(p,'used')
    assert value.value==700 and value.method=='sold_average'
    conn.close()


def test_blocked_http_query_does_not_hide_other_browser_sold_evidence(tmp_path):
    from dealfinder.market import MarketService
    from dealfinder.catalog import Catalog
    from dealfinder.models import Product
    from dealfinder.sources.base import SourceBlocked
    class Blocked:
        def request(self,*args,**kwargs):
            raise SourceBlocked('HTTP 403')
    conn=db.connect(tmp_path/'test.db')
    p=Product('4090','gpu','RTX 4090','rtx 4090',r'\brtx 4090\b')
    other=Product('4080','gpu','RTX 4080','rtx 4080',r'\brtx 4080\b')
    save_sold_capture(conn,'rtx 4090',sold_card(),NOW)
    service=MarketService(conn,Config(),BrowserSoldFetcher(Blocked(),conn,Config(),lambda:NOW),None,
                          Catalog(products=[p,other],bulk=[],platforms=[]),lambda:NOW)
    assert service.product_value(other,'used') is None
    assert service.product_value(p,'used').value==700
    conn.close()
