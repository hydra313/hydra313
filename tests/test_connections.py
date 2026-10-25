import os

import pytest

from dealfinder.connections import load_saved, save_discord, validate_webhook
from dealfinder.config import Config


@pytest.mark.parametrize('url', ['http://127.0.0.1/api/webhooks/1/token',
    'https://discord.com.evil.test/api/webhooks/1/token', 'https://discord.com@evil.test/x',
    'https://discord.com/api/webhooks/1/token?redirect=https://evil.test'])
def test_webhook_cannot_target_arbitrary_hosts(url):
    with pytest.raises(ValueError):
        validate_webhook(url)


@pytest.mark.skipif(os.name != 'nt',reason='Uses Windows protected storage')
def test_connection_survives_restart_without_plaintext_in_files_or_database(tmp_path):
    cfg=Config(db_path=str(tmp_path/'test.db'))
    url='https://discord.com/api/webhooks/123456789012345678/'+'secret-token-'*4
    save_discord(cfg,url)
    assert cfg.discord_webhook_url == url
    saved=list(tmp_path.iterdir())
    assert len(saved) == 1 and b'secret-token' not in saved[0].read_bytes()
    restarted=Config(db_path=cfg.db_path)
    load_saved(restarted)
    assert restarted.discord_webhook_url == url
    save_discord(restarted,'')
    load_saved(cfg)
    assert cfg.discord_webhook_url is None
