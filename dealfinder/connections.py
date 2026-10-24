"""User-entered connection secrets, protected by the current Windows account."""
from __future__ import annotations

import ctypes
import json
import os
import re
import threading
from pathlib import Path

from dealfinder.config import Config

LOCK = threading.Lock()
WEBHOOK = re.compile(r'https://(?:discord\.com|discordapp\.com)/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]{20,}')


def validate_webhook(url: str) -> str:
    url = url.strip()
    if not WEBHOOK.fullmatch(url):
        raise ValueError('Enter an HTTPS Discord webhook URL copied from your channel’s Integrations settings.')
    return url


def _path(cfg):
    return Path(cfg.db_path).with_suffix('.credentials')


def _crypt(data: bytes, *, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise ValueError('Saved connections need Windows protected storage. On other systems, use DISCORD_WEBHOOK_URL.')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,
                       ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
        raise ValueError('Windows could not unlock the saved connection for this user.')
    try:
        return ctypes.string_at(output.data,output.size)
    finally:
        kernel.LocalFree(output.data)


def load_saved(cfg: Config) -> None:
    path=_path(cfg)
    if not path.exists():
        return
    with LOCK:
        data=json.loads(_crypt(path.read_bytes(),decrypt=True))
    if 'discord_webhook_url' in data:
        cfg.discord_webhook_url=validate_webhook(data['discord_webhook_url']) if data['discord_webhook_url'] else None


def load_preferences(cfg: Config) -> None:
    from dealfinder import db
    conn=db.connect(cfg.db_path)
    try:
        data=json.loads(db.get_setting(conn,'discord_notify_reasons','{}'))
        if isinstance(data,dict):
            cfg.notify_reasons={key:data.get(key,default) if isinstance(data.get(key,default),bool) else default
                                for key,default in cfg.notify_reasons.items()}
    except (ValueError,TypeError):
        pass
    finally:
        conn.close()


def save_discord(cfg: Config, url: str) -> None:
    url=validate_webhook(url) if url else ''
    with LOCK:
        path=_path(cfg)
        data=json.loads(_crypt(path.read_bytes(),decrypt=True)) if path.exists() else {}
        data['discord_webhook_url']=url
        protected=_crypt(json.dumps(data).encode('utf-8'))
        temporary=path.with_suffix('.credentials.tmp')
        temporary.write_bytes(protected)
        temporary.replace(path)
        cfg.discord_webhook_url=url or None
