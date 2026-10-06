"""Durable discovery queue and conservative notification delivery states."""
import json
from pathlib import Path
import sqlite3
import time

from .models import Listing


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                state TEXT NOT NULL, discovered REAL NOT NULL,
                retry_at REAL NOT NULL DEFAULT 0, error TEXT,
                enriched INTEGER NOT NULL DEFAULT 0, scope TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        # A crash after POST but before commit could already have delivered.
        # Never automatically resend these entries: prioritize no duplicates.
        self.db.execute("UPDATE listings SET state='uncertain', error='Interrupted delivery' WHERE state='sending'")
        self.db.commit()

    def close(self):
        self.db.close()

    def initialized(self, fingerprint: str) -> bool:
        return self.db.execute("SELECT 1 FROM metadata WHERE key=?", (fingerprint,)).fetchone() is not None

    def discover(self, listings: list[Listing], fingerprint: str, seed: bool = False):
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO listings(id,payload,state,discovered,scope) VALUES(?,?,?,?,?)",
                [(item.id, json.dumps(item.to_dict()), "suppressed" if seed else "pending", time.time(), fingerprint)
                 for item in listings],
            )
            # If an overlapping active search finds an unsent listing owned by a
            # removed/changed search, move it to the active queue. Sent/uncertain
            # IDs stay untouched, and retry deadlines survive reassignment.
            self.db.executemany(
                "UPDATE listings SET scope=?,payload=?,enriched=0 WHERE id=? AND state='pending' AND scope<>?",
                [(fingerprint, json.dumps(item.to_dict()), item.id, fingerprint) for item in listings],
            )
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES(?, '1')", (fingerprint,))

    def pending(self, limit: int, fingerprint: str) -> list[tuple[Listing, bool]]:
        rows = self.db.execute(
            "SELECT payload,enriched FROM listings WHERE state='pending' AND retry_at<=? AND scope=? ORDER BY discovered,id LIMIT ?",
            (time.time(), fingerprint, limit),
        ).fetchall()
        return [(Listing(**json.loads(row["payload"])), bool(row["enriched"])) for row in rows]

    def enrich(self, listing: Listing):
        with self.db:
            self.db.execute("UPDATE listings SET payload=?,enriched=1 WHERE id=?",
                            (json.dumps(listing.to_dict()), listing.id))

    def claim(self, item_id: str) -> bool:
        with self.db:
            return self.db.execute("UPDATE listings SET state='sending' WHERE id=? AND state='pending'",
                                   (item_id,)).rowcount == 1

    def finish(self, item_id: str, state: str, error: str | None = None, delay: float = 0):
        # Sent is permanent: later/stale updates must never queue this ID again.
        with self.db:
            self.db.execute("UPDATE listings SET state=?,error=?,retry_at=? WHERE id=? AND state<>'sent'",
                            (state, error, time.time() + delay, item_id))

    def status(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT id,state,error FROM listings ORDER BY discovered DESC")]

    def retry_uncertain(self, item_id: str) -> bool:
        with self.db:
            return self.db.execute(
                "UPDATE listings SET state='pending',retry_at=0,error=NULL WHERE id=? AND state='uncertain'",
                (item_id,),
            ).rowcount == 1
