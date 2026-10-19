from __future__ import annotations
import contextlib
import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

def utc(ts):
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).isoformat().replace("+00:00", "Z")

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

class Store:
    """One guarded WAL connection; every evidence record is committed atomically."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, data TEXT NOT NULL,
            updated TEXT NOT NULL, PRIMARY KEY(kind,id));
          CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, endpoint TEXT,
            method TEXT, params TEXT, data BLOB, created REAL);
          CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, at TEXT,
            endpoint TEXT, method TEXT, params TEXT, status TEXT, detail TEXT, elapsed REAL);
        """)
        self.db.commit()

    def put(self, kind, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?)",
                            (kind, str(key), dumps(value), now()))

    def get(self, kind, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT data FROM records WHERE kind=? AND id=?",
                                  (kind, str(key))).fetchone()
        return json.loads(row[0]) if row else default

    def all(self, kind):
        with self.lock:
            rows = self.db.execute("SELECT data FROM records WHERE kind=? ORDER BY id", (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def replace_prefix(self, kind, prefix, rows):
        with self.lock, self.db:
            self.db.execute("DELETE FROM records WHERE kind=? AND substr(id,1,?)=?",(kind,len(prefix),prefix))
            self.db.executemany("INSERT OR REPLACE INTO records VALUES(?,?,?,?)",
                [(kind,str(row["id"]),dumps(row),now()) for row in rows])

    def clear(self, kind):
        with self.lock, self.db:
            self.db.execute("DELETE FROM records WHERE kind=?", (kind,))

    def request(self, endpoint, method, params, status, detail, elapsed):
        with self.lock, self.db:
            self.db.execute("INSERT INTO requests(at,endpoint,method,params,status,detail,elapsed) VALUES(?,?,?,?,?,?,?)",
                            (now(), endpoint, method, dumps(params), status, str(detail)[:3000], elapsed))

    def integrity(self):
        with self.lock:
            return self.db.execute("PRAGMA quick_check").fetchone()[0]

    def close(self):
        with self.lock:
            self.db.close()


def units(raw, decimals):
    """Exact fixed-point formatting; never route token quantities through floats."""
    n=int(raw)
    if not 0<=int(decimals)<=255:raise ValueError("Invalid decimals")
    sign="-" if n<0 else ""
    text=str(abs(n)).zfill(int(decimals)+1)
    if not decimals:return sign+text
    return sign+(text[:-decimals]+"."+text[-decimals:]).rstrip("0").rstrip(".")
