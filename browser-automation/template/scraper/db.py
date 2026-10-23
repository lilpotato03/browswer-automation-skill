"""SQLite storage: everything that must survive between runs lives here.

Tables
  runs        one row per run (when, what, how many new leads, errors)
  leads       one row per unique lead, de-duplicated by lead_key
  errors      what failed, with a screenshot path so it can be fixed later
  site_state  small per-site key/value memory (last page seen, cursors, ...)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    sites        TEXT,
    keywords     TEXT,
    new_leads    INTEGER DEFAULT 0,
    seen_leads   INTEGER DEFAULT 0,
    errors       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS leads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_key       TEXT NOT NULL UNIQUE,
    site           TEXT NOT NULL,
    keywords       TEXT NOT NULL DEFAULT '',
    data_json      TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    first_run_id   INTEGER NOT NULL,
    last_run_id    INTEGER NOT NULL,
    times_seen     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_leads_first_run ON leads(first_run_id);
CREATE INDEX IF NOT EXISTS idx_leads_site ON leads(site);
CREATE TABLE IF NOT EXISTS errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    site        TEXT,
    keyword     TEXT,
    message     TEXT,
    screenshot  TEXT,
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS site_state (
    site        TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (site, key)
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_lead_key(site: str, row: dict, key_fields: Iterable[str]) -> str:
    """Stable identity for a lead. Same key on a later run = not new."""
    parts = [site] + [str(row.get(f, "")).strip().lower() for f in key_fields]
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ── runs ────────────────────────────────────────────────────────────────
    def start_run(self, sites: list, keywords: list) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, sites, keywords) VALUES (?, ?, ?)",
            (now(), ", ".join(sites), ", ".join(keywords)),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str) -> None:
        stats = self.conn.execute(
            "SELECT "
            " (SELECT COUNT(*) FROM leads WHERE first_run_id = ?) AS new_leads,"
            " (SELECT COUNT(*) FROM leads WHERE last_run_id = ?) AS seen_leads,"
            " (SELECT COUNT(*) FROM errors WHERE run_id = ?) AS errors",
            (run_id, run_id, run_id),
        ).fetchone()
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, new_leads=?, seen_leads=?, errors=? WHERE id=?",
            (now(), status, stats["new_leads"], stats["seen_leads"], stats["errors"], run_id),
        )
        self.conn.commit()

    def run_history(self, limit: int = 60) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    def last_run_id(self) -> Optional[int]:
        row = self.conn.execute("SELECT MAX(id) AS id FROM runs").fetchone()
        return row["id"]

    # ── leads ───────────────────────────────────────────────────────────────
    def upsert_lead(self, run_id: int, site: str, keyword: str, row: dict,
                    key_fields: Iterable[str]) -> bool:
        """Insert or refresh a lead. Returns True if it is new."""
        key = make_lead_key(site, row, key_fields)
        ts = now()
        existing = self.conn.execute(
            "SELECT id, keywords, last_run_id, data_json FROM leads WHERE lead_key = ?", (key,)
        ).fetchone()
        if existing is None:
            self.conn.execute(
                "INSERT INTO leads (lead_key, site, keywords, data_json, first_seen_at,"
                " last_seen_at, first_run_id, last_run_id) VALUES (?,?,?,?,?,?,?,?)",
                (key, site, keyword, json.dumps(row, ensure_ascii=False), ts, ts, run_id, run_id),
            )
            self.conn.commit()
            return True

        kws = [k for k in existing["keywords"].split(", ") if k]
        if keyword and keyword not in kws:
            kws.append(keyword)
        bump = 0 if existing["last_run_id"] == run_id else 1
        # Blank values never wipe out data found earlier (e.g. a phone number
        # fetched from a detail page that was skipped on this run).
        merged = json.loads(existing["data_json"])
        merged.update({k: v for k, v in row.items() if v not in ("", None)})
        self.conn.execute(
            "UPDATE leads SET data_json=?, keywords=?, last_seen_at=?, last_run_id=?,"
            " times_seen = times_seen + ? WHERE id=?",
            (json.dumps(merged, ensure_ascii=False), ", ".join(kws), ts, run_id, bump, existing["id"]),
        )
        self.conn.commit()
        return False

    def leads(self, where: str = "", params: tuple = ()) -> list:
        sql = "SELECT * FROM leads " + (("WHERE " + where) if where else "") + " ORDER BY id"
        out = []
        for r in self.conn.execute(sql, params):
            d = dict(r)
            d["data"] = json.loads(d.pop("data_json"))
            out.append(d)
        return out

    # ── errors ──────────────────────────────────────────────────────────────
    def log_error(self, run_id: int, site: str, keyword: str, message: str,
                  screenshot: str = "") -> None:
        self.conn.execute(
            "INSERT INTO errors (run_id, site, keyword, message, screenshot, at) VALUES (?,?,?,?,?,?)",
            (run_id, site, keyword, message[:2000], screenshot, now()),
        )
        self.conn.commit()

    def errors_for_run(self, run_id: int) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM errors WHERE run_id = ? ORDER BY id", (run_id,))]

    # ── per-site memory ─────────────────────────────────────────────────────
    def state_get(self, site: str, key: str, default: Any = None) -> Any:
        row = self.conn.execute(
            "SELECT value FROM site_state WHERE site=? AND key=?", (site, key)).fetchone()
        return default if row is None else json.loads(row["value"])

    def state_set(self, site: str, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO site_state (site, key, value, updated_at) VALUES (?,?,?,?)"
            " ON CONFLICT(site, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (site, key, json.dumps(value, ensure_ascii=False), now()),
        )
        self.conn.commit()
