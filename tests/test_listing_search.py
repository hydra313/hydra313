from datetime import datetime, timezone

import pytest

from dealfinder.web.browse import BrowseFilters, browse, metadata


def item(id, *, price=100, profit=40, sold=200, tone='good', condition='used', end=None):
    return dict(id=id, title=f'Item {id}', product='GeForce RTX 4070', price=price,
                total_cost=price+20, profit=profit, condition=condition,
                rating=dict(tone=tone, average=sold), end_at=end)


def test_brand_and_console_metadata_preserves_platform_boundaries():
    assert metadata('Sony PlayStation 5 Pro 30th Anniversary', '')['console'] == 'PlayStation 5 Pro'
    assert metadata('Nintendo Switch 2 Pokemon', '')['console'] == 'Nintendo Switch 2'
    assert metadata('DualSense Spider-Man 2', '')['console'] == 'PlayStation 5'
    assert metadata('GeForce RTX 4070', 'MSI graphics card')['brands'] == ['MSI', 'NVIDIA']
    assert metadata('Ryzen 7 7800X3D', '')['brands'] == ['AMD']


def test_combined_search_brand_console_and_condition_filters():
    a = {**item(1), 'product':'Sony PlayStation 5 Spider-Man 2', 'title':'Marvel Spider-Man bundle'}
    b = {**item(2), 'product':'Nintendo Switch 2', 'title':'Mario bundle'}
    result, options = browse([a,b], BrowseFilters(q='SPIDER man', brand='sony', console='PlayStation 5', condition='used'))
    assert [d['id'] for d in result] == [1]
    assert options['brands'] == ['Nintendo', 'Sony']
    assert len(options['consoles']) == 2
    assert browse([a,b], BrowseFilters(q="' OR 1=1 --"))[0] == []


@pytest.mark.parametrize('sort,expected', [
    ('price',[2,1,3]), ('profit',[1,2,3]), ('margin',[1,2,3]),
    ('sold',[2,1,3]), ('rating',[1,2,3]), ('condition',[2,1,3]), ('time',[2,1,3]),
])
def test_sorting_has_real_numeric_order_and_unknowns_last(sort, expected):
    rows = [item(1, price=100, profit=80, sold=200, end=datetime(2030,1,2,tzinfo=timezone.utc)),
            item(2, price=300, profit=10, sold=400, tone='average', condition='new',
                 end=datetime(2030,1,3,tzinfo=timezone.utc)),
            item(3, price=50, profit=None, sold=None, tone='unknown', condition='unknown')]
    assert [d['id'] for d in browse(rows, BrowseFilters(sort=sort, direction='desc'))[0]] == expected
    ascending = browse(rows, BrowseFilters(sort=sort, direction='asc'))[0]
    if sort not in ('price',):
        assert ascending[-1]['id'] == 3


def test_price_bounds_rating_and_invalid_sort_do_not_drop_unpriced_listings_by_default():
    rows = [item(1), item(2,price=500,tone='overpriced')]
    assert [d['id'] for d in browse(rows, BrowseFilters(min_price='200', max_price='600'))[0]] == [2]
    assert [d['id'] for d in browse(rows, BrowseFilters(rating='good'))[0]] == [1]
    assert [d['id'] for d in browse(rows, BrowseFilters(sort='__import__', min_price='nan'))[0]] == [1,2]
