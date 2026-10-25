"""Per-catalog-item Discord subscriptions, separate from search enablement."""
import json
from dealfinder import db

def subscriptions(conn):
    try:
        value=json.loads(db.get_setting(conn,'discord_item_subscriptions','[]'))
        return set(v for v in value if isinstance(v,str)) if isinstance(value,list) else set()
    except (ValueError,TypeError):
        return set()

def set_subscription(conn,table,key,on):
    selected=subscriptions(conn)
    identity=f'{table}:{key}'
    selected.add(identity) if on else selected.discard(identity)
    db.set_setting(conn,'discord_item_subscriptions',json.dumps(sorted(selected)))

def accepts(conn,row):
    if db.get_setting(conn,'discord_item_scope','all') != 'selected':
        return True
    table='product' if row['kind']=='single' else 'platform' if row['category'] is None else 'bulk'
    return f"{table}:{row['key']}" in subscriptions(conn)
