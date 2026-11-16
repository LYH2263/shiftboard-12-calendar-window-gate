from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import HTTPException

from app.db import connect, rows_to_dicts, transaction
from app.services import windows as win
from app.services.windows import WEEKDAY_NAMES


def now_local() -> datetime:
    """交班日历窗按本地墙钟裁决；抽成函数便于测试注入时钟。"""
    return datetime.now()


def _now_text(at: datetime | None = None) -> str:
    return (at or now_local()).strftime("%Y-%m-%dT%H:%M:%S")


def _local_text(at: datetime) -> str:
    return f"{at:%Y-%m-%d %H:%M}（{WEEKDAY_NAMES[at.isoweekday() - 1]}）"


def _load_windows(conn, shift_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT weekday, start_time, end_time
        FROM shift_windows WHERE shift_id = ?
        ORDER BY weekday, start_time, end_time
        """,
        (shift_id,),
    ).fetchall()
    return win.normalize_windows(
        [
            {"weekday": r["weekday"], "start": r["start_time"], "end": r["end_time"]}
            for r in rows
        ]
    )


def _shift_view(conn, row, at: datetime) -> dict:
    w = _load_windows(conn, row["id"])
    view = dict(row)
    view["windows"] = win.public_windows(w)
    view["windows_text"] = win.describe_windows(w)
    view["in_window"] = win.in_window(w, at)
    return view


def list_shifts() -> list[dict]:
    at = now_local()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM shifts ORDER BY id").fetchall()
        return [_shift_view(conn, r, at) for r in rows]


def update_shift_windows(shift_id: int, raw_windows: list[dict]) -> dict:
    try:
        windows = win.normalize_windows(raw_windows)
    except ValueError as exc:
        raise HTTPException(400, f"交班日历窗不合法：{exc}") from None
    with connect() as conn:
        shift = conn.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        if not shift:
            raise HTTPException(404, "班次不存在")
        conn.execute("DELETE FROM shift_windows WHERE shift_id = ?", (shift_id,))
        for w in windows:
            conn.execute(
                """
                INSERT INTO shift_windows(shift_id, weekday, start_time, end_time)
                VALUES (?, ?, ?, ?)
                """,
                (shift_id, w["weekday"], w["start"], w["end"]),
            )
        conn.commit()
        return _shift_view(conn, shift, now_local())


def list_batches(owner_shift_id: int | None = None) -> list[dict]:
    with connect() as conn:
        if owner_shift_id is None:
            rows = conn.execute(
                """
                SELECT b.*, s.name AS owner_shift_name
                FROM batches b
                LEFT JOIN shifts s ON s.id = b.owner_shift_id
                ORDER BY b.id
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT b.*, s.name AS owner_shift_name
                FROM batches b
                LEFT JOIN shifts s ON s.id = b.owner_shift_id
                WHERE b.owner_shift_id = ?
                ORDER BY b.id
                """,
                (owner_shift_id,),
            ).fetchall()
        return rows_to_dicts(rows)


def _parse_snapshot(row) -> dict:
    d = dict(row)
    d["from_windows"] = json.loads(d.pop("from_windows_json"))
    d["to_windows"] = json.loads(d.pop("to_windows_json"))
    d["from_windows_text"] = win.describe_windows(
        win.normalize_windows(d["from_windows"])
    )
    d["to_windows_text"] = win.describe_windows(win.normalize_windows(d["to_windows"]))
    d["in_window"] = bool(d["in_window"])
    d["from_in_window"] = bool(d["from_in_window"])
    d["to_in_window"] = bool(d["to_in_window"])
    return d


def list_handovers() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name,
                   ws.decision_at AS finish_decision_at,
                   ws.in_window AS finish_in_window,
                   ws.reason AS finish_window_reason,
                   ws.from_windows_json AS finish_from_windows_json,
                   ws.to_windows_json AS finish_to_windows_json,
                   ss.decision_at AS start_decision_at,
                   ss.in_window AS start_in_window
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            LEFT JOIN handover_window_snapshots ws
              ON ws.handover_id = h.id AND ws.phase = '完成'
            LEFT JOIN handover_window_snapshots ss
              ON ss.handover_id = h.id AND ss.phase = '发起'
            ORDER BY h.id DESC
            """
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            if d["finish_from_windows_json"] is not None:
                d["finish_from_windows_text"] = win.describe_windows(
                    win.normalize_windows(json.loads(d.pop("finish_from_windows_json")))
                )
                d["finish_to_windows_text"] = win.describe_windows(
                    win.normalize_windows(json.loads(d.pop("finish_to_windows_json")))
                )
                d["finish_in_window"] = bool(d["finish_in_window"])
            else:
                d.pop("finish_from_windows_json", None)
                d.pop("finish_to_windows_json", None)
            if d.get("start_in_window") is not None:
                d["start_in_window"] = bool(d["start_in_window"])
            out.append(d)
        return out


def get_handover(handover_id: int) -> dict:
    with connect() as conn:
        h = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            WHERE h.id = ?
            """,
            (handover_id,),
        ).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        items = conn.execute(
            """
            SELECT b.*
            FROM handover_items hi
            JOIN batches b ON b.id = hi.batch_id
            WHERE hi.handover_id = ?
            ORDER BY b.id
            """,
            (handover_id,),
        ).fetchall()
        snaps = conn.execute(
            """
            SELECT * FROM handover_window_snapshots
            WHERE handover_id = ? ORDER BY id
            """,
            (handover_id,),
        ).fetchall()
        out = dict(h)
        out["items"] = rows_to_dicts(items)
        out["window_snapshots"] = [_parse_snapshot(s) for s in snaps]
        out["start_snapshot"] = next(
            (s for s in out["window_snapshots"] if s["phase"] == "发起"), None
        )
        out["finish_snapshot"] = next(
            (s for s in out["window_snapshots"] if s["phase"] == "完成"), None
        )
        return out


def dashboard() -> dict:
    at = now_local()
    with connect() as conn:
        shift_rows = conn.execute("SELECT * FROM shifts ORDER BY id").fetchall()
        shifts = [_shift_view(conn, r, at) for r in shift_rows]
        inflight = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE status = '在途'"
        ).fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM handovers WHERE status = '进行中'"
        ).fetchone()["c"]
        last = conn.execute(
            "SELECT * FROM handovers ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "班次数": len(shifts),
            "在途批次数": inflight,
            "进行中交班": active,
            "最近交班": dict(last) if last else None,
            "班次": shifts,
        }


def _next_shared(
    from_windows: list[dict], to_windows: list[dict], at: datetime, limit: int = 3
) -> list[dict]:
    intervals = win.shared_intervals(
        from_windows, to_windows, at, at + timedelta(days=7)
    )
    return [
        {
            "start": s.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": e.strftime("%Y-%m-%dT%H:%M:%S"),
            "text": win.format_interval(s, e),
        }
        for s, e in intervals[:limit]
    ]


def _window_verdict(
    fs, ts, from_windows: list[dict], to_windows: list[dict], at: datetime
) -> dict:
    """按「交班方日历窗 ∩ 接班方日历窗」在裁决时刻求值，两侧都必须检查。"""
    shared = win.shared_intervals(from_windows, to_windows, at, at + timedelta(seconds=1))
    allowed = bool(shared)
    from_in = win.in_window(from_windows, at)
    to_in = win.in_window(to_windows, at)
    reasons: list[str] = []
    if not from_windows:
        reasons.append(f"交班方「{fs['name']}」未配置交班日历窗")
    elif not from_in:
        reasons.append(
            f"交班方「{fs['name']}」不在日历窗内（{win.describe_windows(from_windows)}）"
        )
    if not to_windows:
        reasons.append(f"接班方「{ts['name']}」未配置交班日历窗")
    elif not to_in:
        reasons.append(
            f"接班方「{ts['name']}」不在日历窗内（{win.describe_windows(to_windows)}）"
        )
    if allowed:
        reason = ""
    else:
        reason = "；".join(reasons) + f"。裁决时刻本地墙钟 {_local_text(at)}，须同时落在双方日历窗交集内。"
        upcoming = _next_shared(from_windows, to_windows, at)
        if upcoming:
            reason += f"最近可办：{upcoming[0]['text']}。"
        else:
            reason += "未来 7 天内没有双方都可行的交班窗。"
    return {
        "allowed": allowed,
        "from_in_window": from_in,
        "to_in_window": to_in,
        "reason": reason,
        "decision_at": _now_text(at),
        "from_windows": win.public_windows(from_windows),
        "to_windows": win.public_windows(to_windows),
    }


def window_status(from_shift_id: int, to_shift_id: int) -> dict:
    at = now_local()
    with connect() as conn:
        fs = conn.execute("SELECT * FROM shifts WHERE id = ?", (from_shift_id,)).fetchone()
        ts = conn.execute("SELECT * FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not fs or not ts:
            raise HTTPException(404, "班次不存在")
        from_windows = _load_windows(conn, from_shift_id)
        to_windows = _load_windows(conn, to_shift_id)
        verdict = _window_verdict(fs, ts, from_windows, to_windows, at)
        upcoming = _next_shared(from_windows, to_windows, at)
        return {
            "at": _now_text(at),
            "at_text": _local_text(at),
            "from": {
                "shift_id": fs["id"],
                "name": fs["name"],
                "windows": win.public_windows(from_windows),
                "windows_text": win.describe_windows(from_windows),
                "in_window": verdict["from_in_window"],
            },
            "to": {
                "shift_id": ts["id"],
                "name": ts["name"],
                "windows": win.public_windows(to_windows),
                "windows_text": win.describe_windows(to_windows),
                "in_window": verdict["to_in_window"],
            },
            "in_window": verdict["allowed"],
            "reason": verdict["reason"],
            "next_shared": upcoming,
        }


def _insert_snapshot(
    conn,
    handover_id: int,
    phase: str,
    verdict: dict,
    from_shift_id: int,
    to_shift_id: int,
) -> None:
    # 快照表带 “禁止 UPDATE / DELETE” 触发器：完成裁决所用窗从此固化。
    conn.execute(
        """
        INSERT INTO handover_window_snapshots(
          handover_id, phase, decision_at, from_shift_id, to_shift_id,
          from_windows_json, to_windows_json,
          from_in_window, to_in_window, in_window, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            handover_id,
            phase,
            verdict["decision_at"],
            from_shift_id,
            to_shift_id,
            json.dumps(verdict["from_windows"], ensure_ascii=False),
            json.dumps(verdict["to_windows"], ensure_ascii=False),
            1 if verdict["from_in_window"] else 0,
            1 if verdict["to_in_window"] else 0,
            1 if verdict["allowed"] else 0,
            verdict["reason"],
        ),
    )


def start_handover(from_shift_id: int, to_shift_id: int) -> dict:
    if from_shift_id == to_shift_id:
        raise HTTPException(400, "交班双方不能是同一班次")
    at = now_local()
    with transaction() as conn:
        fs = conn.execute("SELECT * FROM shifts WHERE id = ?", (from_shift_id,)).fetchone()
        ts = conn.execute("SELECT * FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not fs or not ts:
            raise HTTPException(404, "班次不存在")
        active = conn.execute(
            "SELECT id FROM handovers WHERE status = '进行中' LIMIT 1"
        ).fetchone()
        if active:
            raise HTTPException(409, f"已有进行中的交班单#{active['id']}，禁止并行交班")
        batches = conn.execute(
            "SELECT id FROM batches WHERE owner_shift_id = ? AND status = '在途' ORDER BY id",
            (from_shift_id,),
        ).fetchall()
        if not batches:
            raise HTTPException(400, "交班方没有在途批次可交接")
        # 发起裁决：必须同时落在交班方窗 ∩ 接班方窗，禁止只看交班方。
        from_windows = _load_windows(conn, from_shift_id)
        to_windows = _load_windows(conn, to_shift_id)
        verdict = _window_verdict(fs, ts, from_windows, to_windows, at)
        if not verdict["allowed"]:
            raise HTTPException(400, f"不在交班日历窗内，禁止发起：{verdict['reason']}")
        cur = conn.execute(
            """
            INSERT INTO handovers(from_shift_id, to_shift_id, status, batch_count, started_at)
            VALUES (?, ?, '进行中', ?, ?)
            """,
            (from_shift_id, to_shift_id, len(batches), _now_text(at)),
        )
        hid = cur.lastrowid
        for b in batches:
            conn.execute(
                "INSERT INTO handover_items(handover_id, batch_id) VALUES (?, ?)",
                (hid, b["id"]),
            )
        _insert_snapshot(conn, hid, "发起", verdict, from_shift_id, to_shift_id)
        conn.execute("UPDATE shifts SET status = '交班中' WHERE id = ?", (from_shift_id,))
        conn.execute("UPDATE shifts SET status = '接班中' WHERE id = ?", (to_shift_id,))
    return get_handover(hid)


def _release_shifts(conn, from_shift_id: int, to_shift_id: int) -> None:
    conn.execute("UPDATE shifts SET status = '值班中' WHERE id = ?", (from_shift_id,))
    conn.execute("UPDATE shifts SET status = '空闲' WHERE id = ?", (to_shift_id,))


def _record_report(conn, handover_id: int, before: int, after_from: int, after_to: int,
                   consistent: bool, detail: dict, at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO reconcile_reports(
          handover_id, before_count, after_from_count, after_to_count,
          consistent, detail_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            handover_id,
            before,
            after_from,
            after_to,
            1 if consistent else 0,
            json.dumps(detail, ensure_ascii=False),
            _now_text(at),
        ),
    )


def _fail_out_of_window(
    conn, h, items_count: int, verdict: dict, from_count: int, to_count: int, at: datetime
) -> None:
    reason = f"完成时已出交班日历窗，未过户，可重新发起：{verdict['reason']}"
    conn.execute(
        "UPDATE handovers SET status = '失败', finished_at = ?, reason = ? WHERE id = ?",
        (_now_text(at), reason, h["id"]),
    )
    _release_shifts(conn, h["from_shift_id"], h["to_shift_id"])
    _record_report(
        conn,
        h["id"],
        items_count,
        from_count,
        to_count,
        False,
        {
            "交班前在途数": items_count,
            "交班后交班方在途": from_count,
            "交班后接班方在途": to_count,
            "失败原因": "完成时已出交班日历窗",
            "日历窗裁决": {
                "裁决时刻": verdict["decision_at"],
                "交班方在窗": verdict["from_in_window"],
                "接班方在窗": verdict["to_in_window"],
                "交班方窗口": win.describe_windows(
                    win.normalize_windows(verdict["from_windows"])
                ),
                "接班方窗口": win.describe_windows(
                    win.normalize_windows(verdict["to_windows"])
                ),
            },
            "过户": "未发生（同事务检测出窗，零半截过户）",
        },
        at,
    )


def complete_handover(handover_id: int) -> dict:
    at = now_local()
    # 出窗失败闭：同一 BEGIN IMMEDIATE 事务内检测并裁决，任何异常整体回滚，
    # 保证不会出现“批次已过户一半、单据才判失败”的半截状态。
    with transaction() as conn:
        h = conn.execute("SELECT * FROM handovers WHERE id = ?", (handover_id,)).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        if h["status"] != "进行中":
            raise HTTPException(400, "只能完成进行中的交班单")
        fs = conn.execute(
            "SELECT * FROM shifts WHERE id = ?", (h["from_shift_id"],)
        ).fetchone()
        ts = conn.execute(
            "SELECT * FROM shifts WHERE id = ?", (h["to_shift_id"],)
        ).fetchone()
        items = conn.execute(
            "SELECT batch_id FROM handover_items WHERE handover_id = ? ORDER BY batch_id",
            (handover_id,),
        ).fetchall()
        before = len(items)
        # 完成时重读“当时”配置，并以完成时刻墙钟裁决；不做“发起时窗”的沿用。
        from_windows = _load_windows(conn, h["from_shift_id"])
        to_windows = _load_windows(conn, h["to_shift_id"])
        verdict = _window_verdict(fs, ts, from_windows, to_windows, at)
        _insert_snapshot(conn, handover_id, "完成", verdict, h["from_shift_id"], h["to_shift_id"])

        if not verdict["allowed"]:
            from_count = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["from_shift_id"],),
            ).fetchone()["c"]
            to_count = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["to_shift_id"],),
            ).fetchone()["c"]
            _fail_out_of_window(conn, h, before, verdict, from_count, to_count, at)
            hid = handover_id
        else:
            conn.execute("SAVEPOINT transfer")
            for it in items:
                conn.execute(
                    "UPDATE batches SET owner_shift_id = ? WHERE id = ? AND status = '在途'",
                    (h["to_shift_id"], it["batch_id"]),
                )
            after_from = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["from_shift_id"],),
            ).fetchone()["c"]
            after_to = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["to_shift_id"],),
            ).fetchone()["c"]
            consistent = after_from == 0 and after_to >= before
            if not consistent:
                # 过户后一致性不通过：整趟过户回滚到保存点，零半截过户。
                conn.execute("ROLLBACK TO transfer")
                conn.execute("RELEASE transfer")
                # 回滚后重算，报告数字必须反映真实落库状态。
                actual_from = conn.execute(
                    "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                    (h["from_shift_id"],),
                ).fetchone()["c"]
                actual_to = conn.execute(
                    "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                    (h["to_shift_id"],),
                ).fetchone()["c"]
                reason = "交班后批次归属与清单不一致，已回滚本次过户"
                conn.execute(
                    "UPDATE handovers SET status = '失败', finished_at = ?, reason = ? WHERE id = ?",
                    (_now_text(at), reason, handover_id),
                )
                _release_shifts(conn, h["from_shift_id"], h["to_shift_id"])
                _record_report(
                    conn,
                    handover_id,
                    before,
                    actual_from,
                    actual_to,
                    False,
                    {
                        "交班前在途数": before,
                        "交班后交班方在途": actual_from,
                        "交班后接班方在途": actual_to,
                        "失败原因": reason,
                        "过户": "已回滚（零半截过户）",
                    },
                    at,
                )
            else:
                conn.execute("RELEASE transfer")
                conn.execute(
                    """
                    UPDATE handovers
                    SET status = '已完成', finished_at = ?, batch_count = ?,
                        reason = ''
                    WHERE id = ?
                    """,
                    (_now_text(at), before, handover_id),
                )
                conn.execute(
                    "UPDATE shifts SET status = '已交班' WHERE id = ?", (h["from_shift_id"],)
                )
                conn.execute(
                    "UPDATE shifts SET status = '值班中' WHERE id = ?", (h["to_shift_id"],)
                )
                _record_report(
                    conn,
                    handover_id,
                    before,
                    after_from,
                    after_to,
                    True,
                    {
                        "交班前在途数": before,
                        "交班后交班方在途": after_from,
                        "交班后接班方在途": after_to,
                        "日历窗裁决": {
                            "裁决时刻": verdict["decision_at"],
                            "交班方在窗": verdict["from_in_window"],
                            "接班方在窗": verdict["to_in_window"],
                        },
                    },
                    at,
                )
            hid = handover_id
    return get_handover(hid)


def list_reconcile() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reconcile_reports ORDER BY id DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            d["一致"] = bool(d.pop("consistent"))
            out.append(d)
        return out
