from __future__ import annotations

import json
from pathlib import Path

from app.db import FIXTURES_DIR, connect

# 默认交班日历窗：每天 00:00–24:00（即“全天可办”）。管理员可在班次页收窄。
DEFAULT_WINDOWS = [
    {"weekday": d, "start": "00:00", "end": "24:00"} for d in range(1, 8)
]


SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  keeper TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '空闲'
);
CREATE TABLE IF NOT EXISTS shift_windows (
  id INTEGER PRIMARY KEY,
  shift_id INTEGER NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
  weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shift_windows_shift ON shift_windows(shift_id, weekday);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY,
  biz_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  owner_shift_id INTEGER REFERENCES shifts(id),
  status TEXT NOT NULL DEFAULT '在途',
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS handovers (
  id INTEGER PRIMARY KEY,
  from_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  to_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  status TEXT NOT NULL,
  batch_count INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS handover_items (
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  PRIMARY KEY (handover_id, batch_id)
);
-- 完成（及发起）裁决所用的双方日历窗快照：只允许插入，禁止更新/删除，
-- 保证事后改窗不会把历史完成单改判。
CREATE TABLE IF NOT EXISTS handover_window_snapshots (
  id INTEGER PRIMARY KEY,
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  phase TEXT NOT NULL CHECK (phase IN ('发起', '完成')),
  decision_at TEXT NOT NULL,
  from_shift_id INTEGER NOT NULL,
  to_shift_id INTEGER NOT NULL,
  from_windows_json TEXT NOT NULL,
  to_windows_json TEXT NOT NULL,
  from_in_window INTEGER NOT NULL,
  to_in_window INTEGER NOT NULL,
  in_window INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_window_snap_unique
  ON handover_window_snapshots(handover_id, phase);
CREATE TRIGGER IF NOT EXISTS trg_window_snapshot_no_update
BEFORE UPDATE ON handover_window_snapshots
BEGIN
  SELECT RAISE(ABORT, '交班日历窗快照不可修改');
END;
CREATE TRIGGER IF NOT EXISTS trg_window_snapshot_no_delete
BEFORE DELETE ON handover_window_snapshots
BEGIN
  SELECT RAISE(ABORT, '交班日历窗快照不可删除');
END;
CREATE TABLE IF NOT EXISTS reconcile_reports (
  id INTEGER PRIMARY KEY,
  handover_id INTEGER REFERENCES handovers(id),
  before_count INTEGER NOT NULL,
  after_from_count INTEGER NOT NULL,
  after_to_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def _insert_windows(conn, shift_id: int, windows: list[dict]) -> None:
    for w in windows:
        conn.execute(
            """
            INSERT INTO shift_windows(shift_id, weekday, start_time, end_time)
            VALUES (?, ?, ?, ?)
            """,
            (shift_id, int(w["weekday"]), str(w["start"]), str(w["end"])),
        )


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        seeded = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'window_defaults_seeded'"
        ).fetchone()
        n = conn.execute("SELECT COUNT(*) AS c FROM shifts").fetchone()["c"]
        if not n:
            seed_path = FIXTURES_DIR / "seed.json"
            data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
            for s in data["shifts"]:
                conn.execute(
                    "INSERT INTO shifts(id, name, keeper, status) VALUES (?, ?, ?, ?)",
                    (s["id"], s["name"], s["keeper"], s["status"]),
                )
                _insert_windows(conn, s["id"], s.get("windows") or DEFAULT_WINDOWS)
            for b in data["batches"]:
                conn.execute(
                    "INSERT INTO batches(id, biz_key, title, owner_shift_id, status, note) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        b["id"],
                        b["biz_key"],
                        b["title"],
                        b["owner_shift_id"],
                        b["status"],
                        b.get("note", ""),
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO app_meta(key, value) VALUES ('window_defaults_seeded', '1')"
            )
        elif not seeded:
            # 老库升级：功能上线前没有日历窗，补一组“全天”默认窗；
            # 之后管理员清空窗口（无窗=禁止交班）不会被重启重置。
            shifts = conn.execute("SELECT id FROM shifts").fetchall()
            for s in shifts:
                existing = conn.execute(
                    "SELECT COUNT(*) AS c FROM shift_windows WHERE shift_id = ?",
                    (s["id"],),
                ).fetchone()["c"]
                if not existing:
                    _insert_windows(conn, s["id"], DEFAULT_WINDOWS)
            conn.execute(
                "INSERT OR REPLACE INTO app_meta(key, value) VALUES ('window_defaults_seeded', '1')"
            )
        conn.commit()
