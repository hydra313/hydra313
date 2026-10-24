from pathlib import Path
from dataclasses import replace

import pytest

from dealfinder.catalog import load_seed
from dealfinder.matcher import match_single
from dealfinder.console_editions import REFERENCE_FILE, audit_console_editions
from dealfinder.console_editions import reference_review_flags
from dealfinder.models import Product
from dealfinder.pipeline import ebay_queries
from dealfinder.classify import classify
from dealfinder.config import Config
from dealfinder.models import Listing

CATALOG = Path(__file__).resolve().parents[1] / 'catalog'


@pytest.mark.parametrize('title,edition', [
    ('Xbox 360 Gears of War 3 Limited Edition Console', 'Gears of War 3'),
    ('Xbox 360 Modern Warfare 3 Limited Edition Console', 'Modern Warfare 3'),
    ('Xbox 360 Halo 4 Limited Edition Console', 'Halo 4'),
    ('Xbox One S Gears of War 4 Limited Edition Console', 'Gears of War 4'),
    ('PS4 Pro God of War Limited Edition Console', 'God of War'),
    ('PS4 Pro Spider-Man Limited Edition Console', 'Spider-Man'),
    ('PS4 Pro Kingdom Hearts 3 Limited Edition Console', 'Kingdom Hearts III'),
    ('Nintendo Switch Animal Crossing New Horizons Console', 'Animal Crossing New Horizons'),
    ('New Nintendo 3DS XL Samus Edition Console', 'Samus'),
    ('New Nintendo 3DS XL Hyrule Edition Console', 'Hyrule'),
    ('Game Boy Micro Mother 3 Console', 'Mother 3'),
    ('Sega Dreamcast Code Veronica Console', 'Code Veronica'),
])
def test_missing_edition_examples(title, edition):
    result = match_single(title, load_seed(CATALOG).products)
    assert result is not None
    assert edition.casefold() in result[0].name.casefold()


def test_reference_expands_beyond_the_old_29_consoles():
    assert sum(p.category == 'console' for p in load_seed(CATALOG).products) > 1500


def test_original_and_new_pikachu_3ds_xl_remain_separate():
    products = load_seed(CATALOG).products
    old = match_single('Nintendo 3DS XL Pikachu Yellow Edition Console', products)
    new = match_single('New Nintendo 3DS XL Pikachu Yellow Edition Console', products)
    assert old is not None and new is not None
    assert old[0].id != new[0].id
    assert old[0].id.startswith('cv-')
    assert new[0].id == 'new-3ds-xl-pikachu-yellow'


def test_every_added_identity_reaches_single_console_classification():
    catalog = load_seed(CATALOG)
    for product in catalog.products:
        if product.required_tokens:
            listing = Listing('shopgoodwill', '1', product.name, 'https://example.com',
                              10, None, True, [], False, None)
            result = classify(listing, catalog, Config())
            assert result is not None and result.kind == 'single' and result.key == product.id, product.id


@pytest.mark.parametrize('title', [
    'PS4 Pro God of War controller for console',
    'PS4 Pro God of War limited edition game',
    'PS4 Pro black console with God of War game',
    'PS4 Pro God of War custom shell console',
    'New Nintendo 3DS XL Samus replacement housing',
    'Nintendo Switch Animal Crossing New Horizons dock only',
    'Game Boy Micro Mother 3 replica console',
    'Xbox One Dead Rising 3 console DEAD not working',
    'Xbox 360 Halo 3 ODST Limited Edition game',
    'Nintendo 64 Pikachu Orange Edition Controller for Console',
    'Sega Dreamcast Hello Kitty Blue Controller for Console',
    'PS5 Console with Ghost of Yotei Gold Controller',
    'Nintendo Switch Lite Console with Hyrule Carrying Case',
    'Nintendo 64 Orange Console with Pikachu Controller',
])
def test_new_editions_reject_games_accessories_and_customs(title):
    assert match_single(title, load_seed(CATALOG).products) is None


def test_dead_in_a_theme_is_distinct_from_a_broken_console():
    result = match_single('Xbox One Dead Rising 3 Promotional Console', load_seed(CATALOG).products)
    assert result is not None and 'Dead Rising 3' in result[0].name


def test_audit_accounts_for_every_reference_record():
    import json
    seed = load_seed(CATALOG)
    counts, decisions = audit_console_editions(CATALOG / REFERENCE_FILE,
                                              [p for p in seed.products if not p.required_tokens])
    data = json.loads((CATALOG / REFERENCE_FILE).read_text(encoding='utf-8'))
    assert sum(counts.values()) == len(decisions) == len(data['records'])
    assert counts['Software/packaging bundle; distinctive hardware requires review'] > 1000


def test_reference_searches_are_shared_by_hardware_family():
    catalog = load_seed(CATALOG)
    assert len(ebay_queries(catalog)) < 400
    assert len([p for p in catalog.products if p.required_tokens]) > 1500


def test_disabled_specific_edition_does_not_fall_back_to_a_different_console():
    parent = Product('parent', 'console', 'Halo console', 'halo console', r'\bxbox series x\b',
                     required_tokens=('halo',), discovery_query='Xbox Series X console')
    child = replace(parent, id='child', required_tokens=('halo', 'infinite'), enabled=False)
    assert match_single('Xbox Series X Halo Infinite Console', [parent, child]) is None


@pytest.mark.parametrize('title,expected', [
    ('PS5 Ghost of Yotei Gold Limited Edition Console', 'ps5-ghost-yotei-gold'),
    ('PS5 Ghost of Yōtei Black Limited Edition Console', 'ps5-ghost-yotei-black'),
    ('Nintendo Switch Lite Hyrule Edition', 'switch-lite-hyrule'),
    ('Nintendo Switch Lite Zacian and Zamazenta Edition', 'switch-lite-zacian-zamazenta'),
    ('Nintendo Switch Lite Dialga and Palkia Edition', 'switch-lite-dialga-palkia'),
    ('Nintendo Switch Lite Isabelle Aloha Edition', 'switch-lite-isabelle-aloha'),
    ('Nintendo Switch Lite Timmy and Tommy Aloha Edition', 'switch-lite-timmy-tommy-aloha'),
    ('Xbox Series X25 25th Anniversary Console', 'xbox-series-x25-25th-anniversary'),
])
def test_publisher_confirmed_names_and_aliases(title, expected):
    result = match_single(title, load_seed(CATALOG).products)
    assert result is not None and result[0].id == expected


@pytest.mark.parametrize('title,kind,quantity', [
    ('Lot of 3 Nintendo Switch Diablo III Eternal Collection Consoles', 'hardware_lot', 3),
    ('Collection of Nintendo Switch Diablo III Eternal Collection Consoles', 'scan_only', None),
])
def test_collection_edition_names_do_not_erase_real_lot_markers(title, kind, quantity):
    listing = Listing('shopgoodwill', '1', title, 'https://example.com',
                      10, None, True, [], False, None)
    result = classify(listing, load_seed(CATALOG), Config())
    assert result is not None and result.kind == kind and result.quantity == quantity


def test_source_authentication_warnings_are_not_lost_behind_official_labels():
    assert reference_review_flags('Official console, currently under CV investigation for its authentication') == ['authentication']
    assert reference_review_flags('Unreleased model; only prototypes are known') == ['unreleased']
    products = load_seed(CATALOG).products
    assert not any(p.id == 'cv-ique-game-boy-advance-sp-pikachu-pink-console' for p in products)
    assert not any(p.id == 'cv-microsoft-xbox-halo-special-orange-console' for p in products)


@pytest.mark.parametrize('title,expected', [
    ('Nintendo 64 Pikachu Orange Edition Console', 'n64-pikachu-orange'),
    ('Nintendo 64 Pikachu Blue Yellow Console', 'n64-pikachu'),
    ('Sega Dreamcast Hello Kitty Blue Console', 'dreamcast-hello-kitty-blue'),
    ('Sega Dreamcast Hello Kitty Pink Console', 'dreamcast-hello-kitty'),
    ('Sega Dreamcast Hello Kitty Pink Console with Blue Controller', 'dreamcast-hello-kitty'),
    ('Nintendo 64 Pikachu Blue Console with Orange Controller', 'n64-pikachu'),
])
def test_distinct_factory_colorways_are_not_mixed(title, expected):
    result = match_single(title, load_seed(CATALOG).products)
    assert result is not None and result[0].id == expected

@pytest.mark.parametrize('title, identity', [
 ('Sony PlayStation2 PS2 Hyakushiki Gold Limited Edition Console SCPH-55000 GU','cv-sony-playstation-2-golden-console'),
 ('Sony PlayStation 2 PS2 Console Sakura Pink SCPH-50000 Limited Edition Japan','cv-sony-playstation-2-50-millionth-edition-sakura-pink-console'),
 ('Sony PlayStation 2 PS2 Aqua Blue Console Box Set CIB Complete','cv-sony-playstation-2-aqua-blue-console-eu'),
 ('Sony Playstation2 SCPH-77000PK Pink Limited Console Tested','cv-sony-playstation-2-slim-pink-console-jp'),
 ('SONY Playstation2 SCPH-90000 Cinnabar Red Limited Edition Console Tested Working','cv-sony-playstation-2-slim-cinnabar-red-console'),
])
def test_real_ps2_seller_titles_do_not_require_reference_site_marketing_words(title, identity):
    result=match_single(title,load_seed(CATALOG).products)
    assert result is not None and result[0].id==identity
