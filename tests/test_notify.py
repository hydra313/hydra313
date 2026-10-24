import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from dealfinder import db
from dealfinder.config import Config
from dealfinder.models import Classified, Listing, Scored
from dealfinder.notify import Notifier, build_payload

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
WEBHOOK = "https://discord.com/api/webhooks/123/SECRET-TOKEN"


@respx.mock
def test_runtime_connection_change_is_effective_and_redacted(conn,http,caplog):
    cfg=Config()
    notifier=Notifier(conn,cfg,http,clock=lambda:T0)
    assert not notifier.enabled()
    cfg.discord_webhook_url=WEBHOOK
    route=respx.post(WEBHOOK).respond(204)
    caplog.set_level(logging.INFO,logger='httpx')
    assert notifier.notify(add(conn,make_listing()),'target_hit',[])
    assert route.call_count == 1 and 'SECRET-TOKEN' not in caplog.text
    cfg.discord_webhook_url=None
    assert not notifier.enabled()


class FakeTime:
    """Injected clock + sleep: sleeping advances the clock instead of waiting."""

    def __init__(self):
        self.now = T0
        self.sleeps: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def make_listing(price=100.0, end_time=None, is_auction=None, title="RTX 4070 Founders"):
    return Listing(
        source="ebay", source_id="1", title=title, url="https://example.com/1",
        price=price, shipping=None, shipping_offered=True,
        image_urls=["https://img/1.jpg", "https://img/2.jpg"],
        is_auction=end_time is not None if is_auction is None else is_auction,
        end_time=end_time,
    )


def add(conn, listing, now=T0):
    c = Classified(kind="single", key="rtx-4070", condition="used", quantity=1, category="gpu")
    scored = Scored(total_cost=123.45, shipping_estimated=False, profit=40.0,
                    discount_pct=0.25, is_target_hit=False, is_deal=True, rank_score=1.0)
    return db.upsert_listing(conn, listing, c, scored, now)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "d.sqlite")
    yield connection
    connection.close()


@pytest.fixture
def ft():
    return FakeTime()


@pytest.fixture
def http():
    with httpx.Client() as client:
        yield client


@pytest.fixture
def cfg():
    return Config(discord_webhook_url=WEBHOOK)


def make_notifier(conn, cfg, http, ft):
    return Notifier(conn, cfg, http, sleep=ft.sleep, clock=ft.clock)


def status_of(conn, lid, reason):
    row = conn.execute(
        "SELECT status FROM notifications WHERE listing_id = ? AND reason = ?", (lid, reason)
    ).fetchone()
    return row["status"] if row else None


def test_payload_contents(conn):
    lid = add(conn, make_listing(end_time=T0 + timedelta(hours=5, minutes=12, seconds=30)))
    payload = build_payload(db.get_listing(conn, lid), "target_hit", ["Line one", "Line two"], T0)
    (embed,) = payload["embeds"]
    assert embed["title"] == "RTX 4070 Founders"
    assert embed["url"] == "https://example.com/1"
    assert embed["description"] == "Line one\nLine two"
    assert embed["thumbnail"] == {"url": "https://img/1.jpg"}
    fields = {f["name"]: f for f in embed["fields"]}
    assert fields["Total cost"] == {"name": "Total cost", "value": "$123.45", "inline": True}
    assert fields["Time left"]["value"] == "5h 12m"
    assert fields["Time left"]["inline"] is True
    assert fields["Site"]["value"] == "eBay"


def test_payload_buy_it_now_and_limits(conn):
    lid = add(conn, make_listing(is_auction=False, title="x" * 400))
    row = db.get_listing(conn, lid)
    payload = build_payload(row, "target_hit", ["y" * 5000], T0)
    embed = payload["embeds"][0]
    assert len(embed["title"]) == 256
    assert len(embed["description"]) == 4000
    assert {f["name"]: f["value"] for f in embed["fields"]}["Time left"] == "Buy It Now"


def test_payload_without_images(conn):
    lid = add(conn, make_listing())
    conn.execute("UPDATE listings SET image_urls = '[]' WHERE id = ?", (lid,))
    payload = build_payload(db.get_listing(conn, lid), "target_hit", [], T0)
    assert "thumbnail" not in payload["embeds"][0]


@respx.mock
def test_sends_once_per_reason(conn, cfg, http, ft):
    route = respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing())
    n = make_notifier(conn, cfg, http, ft)
    assert n.enabled()
    assert n.notify(lid, "target_hit", ["a"]) is True
    assert route.call_count == 1
    assert status_of(conn, lid, "target_hit") == "sent"
    assert n.notify(lid, "target_hit", ["a"]) is False
    assert add(conn, make_listing(price=50.0)) == lid  # price drop re-upserts the same row
    assert n.notify(lid, "target_hit", ["a"]) is False
    assert route.call_count == 1
    # a different reason on the same listing is its own notification
    assert n.notify(lid, "photo_find", ["b"]) is True
    assert route.call_count == 2


@respx.mock
def test_disabled_reason_not_sent(conn, cfg, http, ft):
    route = respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing())
    assert make_notifier(conn, cfg, http, ft).notify(lid, "resale_deal", ["a"]) is False
    assert route.call_count == 0
    assert status_of(conn, lid, "resale_deal") is None


@respx.mock
def test_no_webhook_not_sent(conn, http, ft):
    route = respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing())
    n = make_notifier(conn, Config(discord_webhook_url=None), http, ft)
    assert n.enabled() is False
    assert n.notify(lid, "target_hit", ["a"]) is False
    assert route.call_count == 0
    assert status_of(conn, lid, "target_hit") is None


@respx.mock
def test_ended_listing_not_sent(conn, cfg, http, ft):
    route = respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing(end_time=T0 - timedelta(minutes=1)))
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is False
    assert route.call_count == 0
    assert status_of(conn, lid, "target_hit") is None  # not claimed either


@respx.mock
def test_429_retry_after(conn, cfg, http, ft):
    route = respx.post(WEBHOOK).mock(side_effect=[
        httpx.Response(429, json={"message": "rate limited", "retry_after": 2.5, "global": False}),
        httpx.Response(204),
    ])
    lid = add(conn, make_listing())
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is True
    assert route.call_count == 2
    assert ft.sleeps == [2.5]
    assert status_of(conn, lid, "target_hit") == "sent"


@respx.mock
def test_429_twice_fails_after_single_retry(conn, cfg, http, ft):
    route = respx.post(WEBHOOK).respond(429, json={"retry_after": 0.5})
    lid = add(conn, make_listing())
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is False
    assert route.call_count == 2
    assert status_of(conn, lid, "target_hit") == "failed"


@respx.mock
def test_failure_marked_not_raised(conn, cfg, http, ft, caplog):
    route = respx.post(WEBHOOK).respond(500)
    lid = add(conn, make_listing())
    n = make_notifier(conn, cfg, http, ft)
    with caplog.at_level(logging.DEBUG):
        assert n.notify(lid, "target_hit", ["a"]) is False
    assert status_of(conn, lid, "target_hit") == "failed"
    assert db.notification_failures_since(conn, T0 - timedelta(minutes=1)) == 1
    assert db.notification_failures_since(conn, T0 + timedelta(minutes=1)) == 0
    # stays claimed: no resend storm
    assert n.notify(lid, "target_hit", ["a"]) is False
    assert route.call_count == 1
    assert "SECRET-TOKEN" not in caplog.text


@respx.mock
def test_network_error_marked_not_raised(conn, cfg, http, ft, caplog):
    respx.post(WEBHOOK).mock(side_effect=httpx.ConnectError("boom"))
    lid = add(conn, make_listing())
    with caplog.at_level(logging.DEBUG):
        assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is False
    assert status_of(conn, lid, "target_hit") == "failed"
    assert "SECRET-TOKEN" not in caplog.text


@respx.mock
def test_spacing_between_sends(conn, cfg, http, ft):
    respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing())
    n = make_notifier(conn, cfg, http, ft)
    assert n.notify(lid, "target_hit", ["a"]) is True
    assert ft.sleeps == []  # first send does not wait
    ft.now += timedelta(seconds=0.4)
    assert n.notify(lid, "photo_find", ["a"]) is True
    assert ft.sleeps == [pytest.approx(0.6)]
    ft.now += timedelta(seconds=5)
    assert n.notify(lid, "hardware_lot_deal", ["a"]) is True
    assert len(ft.sleeps) == 1  # already more than a second apart


def test_claim_and_mark_notification(conn):
    lid = add(conn, make_listing())
    assert db.claim_notification(conn, lid, "target_hit", T0) is True
    assert db.claim_notification(conn, lid, "target_hit", T0) is False
    assert status_of(conn, lid, "target_hit") == "pending"
    db.mark_notification(conn, lid, "target_hit", "sent")
    assert status_of(conn, lid, "target_hit") == "sent"


@respx.mock
@pytest.mark.parametrize("body", ['{"retry_after": 1e9}', '{"retry_after": Infinity}',
                                  '{"retry_after": NaN}', '{"retry_after": 30.5}'])
def test_429_unusable_retry_after_fails_without_sleeping(conn, cfg, http, ft, body):
    route = respx.post(WEBHOOK).respond(
        429, content=body, headers={"content-type": "application/json"})
    lid = add(conn, make_listing())
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is False
    assert route.call_count == 1
    assert ft.sleeps == []
    assert status_of(conn, lid, "target_hit") == "failed"


@respx.mock
def test_429_retry_after_at_cap_is_honored(conn, cfg, http, ft):
    respx.post(WEBHOOK).mock(side_effect=[
        httpx.Response(429, json={"retry_after": 30}), httpx.Response(204)])
    lid = add(conn, make_listing())
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is True
    assert ft.sleeps == [30.0]


@respx.mock
@pytest.mark.parametrize("resp_429", [
    httpx.Response(429, content=b"<html>slow down</html>"),
    httpx.Response(429, json={"retry_after": 0}),
    httpx.Response(429, json={"retry_after": -3}),
    httpx.Response(429, json={"message": "no retry_after"}),
])
def test_429_fallback_wait(conn, cfg, http, ft, resp_429):
    route = respx.post(WEBHOOK).mock(side_effect=[resp_429, httpx.Response(204)])
    lid = add(conn, make_listing())
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is True
    assert route.call_count == 2
    assert ft.sleeps == [1.0]


@respx.mock
def test_unexpected_error_after_claim_marks_failed(conn, cfg, http, ft, monkeypatch):
    route = respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing())

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("dealfinder.notify.build_payload", boom)
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is False
    assert status_of(conn, lid, "target_hit") == "failed"
    assert route.call_count == 0


@respx.mock
def test_bad_listing_data_marks_failed(conn, cfg, http, ft):
    route = respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing())
    conn.execute("UPDATE listings SET image_urls = '[]', total_cost = 'abc' WHERE id = ?", (lid,))
    # sqlite stores the text as-is; formatting it as a number must not escape notify()
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is False
    assert status_of(conn, lid, "target_hit") == "failed"
    assert route.call_count == 0


@respx.mock
def test_sleep_failure_marks_failed(conn, cfg, http, ft):
    respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing())

    def bad_sleep(seconds):
        raise OSError("interrupted")

    n = Notifier(conn, cfg, http, sleep=bad_sleep, clock=ft.clock)
    assert n.notify(lid, "target_hit", ["a"]) is True
    assert n.notify(lid, "photo_find", ["a"]) is False  # spacing sleep raises
    assert status_of(conn, lid, "photo_find") == "failed"


@respx.mock
def test_ended_buy_it_now_listing_not_sent(conn, cfg, http, ft):
    route = respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing(is_auction=False, end_time=T0 - timedelta(days=1)))
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", ["a"]) is False
    assert route.call_count == 0
    assert status_of(conn, lid, "target_hit") is None


@respx.mock
def test_empty_details_still_sends(conn, cfg, http, ft):
    route = respx.post(WEBHOOK).respond(204)
    lid = add(conn, make_listing())
    assert make_notifier(conn, cfg, http, ft).notify(lid, "target_hit", []) is True
    assert route.call_count == 1
    assert status_of(conn, lid, "target_hit") == "sent"

@respx.mock
def test_item_subscriptions_filter_before_claim_and_can_be_changed(conn,http):
    from dealfinder.subscriptions import set_subscription
    cfg=Config(discord_webhook_url=WEBHOOK)
    listing_id=add(conn,make_listing())
    db.set_setting(conn,'discord_item_scope','selected')
    notifier=Notifier(conn,cfg,http,clock=lambda:T0)
    route=respx.post(WEBHOOK).respond(204)
    assert not notifier.notify(listing_id,'target_hit',[])
    assert conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0] == 0
    set_subscription(conn,'product','rtx-4070',True)
    assert notifier.notify(listing_id,'target_hit',[])
    assert route.call_count == 1
    assert not notifier.notify(listing_id,'target_hit',[])


def test_retired_game_lot_alerts_cannot_be_reenabled_by_saved_preferences(conn,http):
    cfg=Config(discord_webhook_url=WEBHOOK)
    cfg.notify_reasons['game_lot_deal']=True
    lid=add(conn,make_listing())
    notifier=Notifier(conn,cfg,http,clock=lambda:T0)
    assert not notifier.notify(lid,'game_lot_deal',[])
    assert conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]==0


@pytest.mark.parametrize('title', [
 'Vintage Vivitar PZ 3090 35mm Point-and-Shoot Film Camera',
 'ASUS ROG XG Mobile eGPU Dock RTX 4090 16GB',
])
def test_saved_false_gpu_matches_cannot_claim_or_send_alerts(conn,http,title):
    lid=add(conn,make_listing(title=title))
    notifier=Notifier(conn,Config(discord_webhook_url=WEBHOOK),http,clock=lambda:T0)
    assert not notifier.notify(lid,'target_hit',[])
    assert conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]==0
