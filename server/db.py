import os
import sqlite3
import threading

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "taxi.db"))
_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")

_conn.executescript(
    """
CREATE TABLE IF NOT EXISTS users(
  tg_id INTEGER PRIMARY KEY,
  name TEXT, username TEXT, phone TEXT,
  is_driver INTEGER DEFAULT 0, online INTEGER DEFAULT 0,
  car TEXT, plate TEXT,
  lat REAL, lng REAL, loc_ts INTEGER,
  created INTEGER
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL,
  from_text TEXT, to_text TEXT, price REAL,
  status TEXT,
  driver_id INTEGER, eta_min INTEGER, target_driver_id INTEGER,
  from_lat REAL, from_lng REAL, to_lat REAL, to_lng REAL, km REAL,
  commission REAL,
  created INTEGER, accepted_at INTEGER, arrived_at INTEGER, picked_at INTEGER, done_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS favorites(
  client_id INTEGER, driver_id INTEGER, PRIMARY KEY(client_id, driver_id)
);
CREATE TABLE IF NOT EXISTS notifs(
  order_id INTEGER, chat_id INTEGER, message_id INTEGER, PRIMARY KEY(order_id, chat_id)
);
CREATE TABLE IF NOT EXISTS declines(
  order_id INTEGER, driver_id INTEGER, PRIMARY KEY(order_id, driver_id)
);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  driver_id INTEGER, kind TEXT, amount REAL, order_id INTEGER, created INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS pay_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  driver_id INTEGER, amount REAL, status TEXT, created INTEGER
);
"""
)


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]


def q1(sql, args=()):
    with _lock:
        r = _conn.execute(sql, args).fetchone()
        return dict(r) if r else None


def x(sql, args=()):
    """Run a write. Returns (rowcount, lastrowid)."""
    with _lock:
        c = _conn.execute(sql, args)
        return c.rowcount, c.lastrowid
