from dealfinder.web.browse import visible_deals

def test_default_visibility_requires_good_verified_deal_and_all_is_explicit():
    items=[{'title':'console','rating':{'tone':tone}} for tone in ('good','average','overpriced','unknown')]
    assert [d['rating']['tone'] for d in visible_deals(items,False)] == ['good']
    assert visible_deals(items,True) == items


def test_console_name_search_does_not_match_a_different_generation_with_two_controllers():
    from dealfinder.web.browse import browse,BrowseFilters
    def item(product,title):
        return dict(product=product,title=title,price=100,profit=None,total_cost=120,rating={'tone':'unknown','average':None})
    items=[item('Sony PlayStation 2 Slim Pink Console','PS2 Pink Slim Console'),
           item('Sony PlayStation 4 Console','PlayStation 4 Console with 2 controllers')]
    found,_=browse(items,BrowseFilters(q='PlayStation 2'))
    assert len(found)==1 and found[0]['console']=='PlayStation 2 Slim'


def test_console_search_accepts_platform_aliases_in_seller_titles():
    from dealfinder.web.browse import browse,BrowseFilters
    item=dict(product='Sony PlayStation 2 Slim Pink Console',title='Sony PlayStation 2 Pink Slim Console',price=100,profit=None,total_cost=120,rating={'tone':'unknown','average':None})
    assert browse([item],BrowseFilters(q='PS2'))[0] == [item]
