"""Reserve login attempts before password hashing, across all web processes."""
import hashlib
import hmac
import ipaddress
import math
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db import LoginLimit, Session, engine, now

WINDOW_SECONDS = 60
CLIENT_ATTEMPTS = 5
CATALOG_ATTEMPTS = 20


def trusted_proxy_networks(value):
    networks = []
    for entry in value.split(','):
        if not entry.strip():
            continue
        try:
            network = ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError as exc:
            raise RuntimeError('TRUSTED_PROXY_CIDRS must contain proxy IP addresses or CIDR ranges') from exc
        if network.prefixlen == 0:
            raise RuntimeError('TRUSTED_PROXY_CIDRS must not trust the entire Internet')
        networks.append(network)
    return tuple(networks)


def _address(value):
    address = ipaddress.ip_address(value)
    if '%' in str(address):
        raise ValueError('Scoped client addresses are not supported')
    return getattr(address, 'ipv4_mapped', None) or address


def client_address(peer, forwarded_for, trusted):
    """Walk from the socket peer to the first untrusted hop, never the leftmost claim."""
    try:
        address = _address(peer or '')
    except ValueError:
        return 'unknown'
    if not any(address in network for network in trusted) or not forwarded_for:
        return str(address)
    hops = forwarded_for.split(',')
    if len(hops) > 16:
        return str(address)
    try:
        parsed = [_address(hop.strip()) for hop in hops]
    except ValueError:
        return str(address)
    for hop in reversed(parsed):
        if not any(address in network for network in trusted):
            break
        address = hop
    return str(address)


def _ensure_bucket(db, key, timestamp):
    values = dict(key=key, window_start=timestamp, attempts=0)
    if engine.dialect.name == 'mysql':
        statement = mysql_insert(LoginLimit).values(**values)
        statement = statement.on_duplicate_key_update(key=statement.inserted.key)
    elif engine.dialect.name == 'sqlite':
        statement = sqlite_insert(LoginLimit).values(**values).on_conflict_do_nothing(index_elements=['key'])
    else:
        raise RuntimeError('Login rate limiting requires MariaDB/MySQL or SQLite')
    db.execute(statement)
    return db.scalar(select(LoginLimit).where(LoginLimit.key == key).with_for_update())


def _remaining(bucket, timestamp):
    return max(0, math.ceil(WINDOW_SECONDS - (timestamp - bucket.window_start).total_seconds()))


def reserve_login_attempt(address, secret):
    """Return seconds to wait, or zero after atomically consuming both budgets.

    The global upsert locks one row first on MariaDB and obtains SQLite's write
    lock, so concurrent requests cannot exceed either budget. Commit before the
    expensive password check. Rejected requests do not extend the cooldown.
    """
    client_key = 'ip:' + hmac.new(secret.encode(), address.encode(), hashlib.sha256).hexdigest()
    with Session.begin() as db:
        catalog = _ensure_bucket(db, 'global', now())
        timestamp = now()
        remaining = _remaining(catalog, timestamp)
        if not remaining:
            catalog.window_start, catalog.attempts = timestamp, 0
            db.execute(delete(LoginLimit).where(LoginLimit.key != 'global',
                                               LoginLimit.window_start < timestamp - timedelta(hours=1)))
        elif catalog.attempts >= CATALOG_ATTEMPTS:
            return remaining

        client = _ensure_bucket(db, client_key, timestamp)
        remaining = _remaining(client, timestamp)
        if not remaining:
            client.window_start, client.attempts = timestamp, 0
        elif client.attempts >= CLIENT_ATTEMPTS:
            return remaining
        catalog.attempts += 1
        client.attempts += 1
    return 0
