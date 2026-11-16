from pathlib import Path
import os
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DATA_DIR"] = str(ROOT / "backend" / "data_test")
os.environ["FIXTURES_DIR"] = str(ROOT / "fixtures")

db_path = Path(os.environ["DATA_DIR"]) / "app.db"
if db_path.exists():
    db_path.unlink()

from app.main import app  # noqa: E402
from app.services import handover as svc  # noqa: E402

ALL_DAY = [{"weekday": d, "start": "00:00", "end": "24:00"} for d in range(1, 8)]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _set_windows(client, shift_id: int, windows: list[dict]):
    r = client.put(f"/api/shifts/{shift_id}/windows", json={"windows": windows})
    assert r.status_code == 200, r.text
    return r.json()


def _set_all_day(client, *shift_ids: int):
    for sid in shift_ids:
        _set_windows(client, sid, ALL_DAY)


def _seed_batches(shift_id: int, count: int, prefix: str) -> list[int]:
    """测试夹具：给指定班次造在途批次，避免测试之间互相抢夺清单。"""
    ids: list[int] = []
    with sqlite3.connect(db_path) as raw:
        for i in range(count):
            cur = raw.execute(
                "INSERT INTO batches(biz_key, title, owner_shift_id, status) "
                "VALUES (?, ?, ?, '在途')",
                (f"{prefix}-{i}", f"测试批次 {prefix}-{i}", shift_id),
            )
            ids.append(cur.lastrowid)
        raw.commit()
    return ids


@pytest.fixture
def frozen_clock(monkeypatch):
    """固定本地墙钟：2026-11-16 是周一，10:00。"""
    from datetime import datetime

    fixed = datetime(2026, 11, 16, 10, 0, 0)
    monkeypatch.setattr(svc, "now_local", lambda: fixed)
    return fixed


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["项目"] == "交班台"


def test_handover_and_reconcile(client):
    shifts = client.get("/api/shifts").json()
    assert len(shifts) >= 2
    batches = client.get("/api/batches", params={"owner_shift_id": 1}).json()
    assert any(b["status"] == "在途" for b in batches)

    started = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 1, "to_shift_id": 2},
    )
    assert started.status_code == 200, started.text
    hid = started.json()["id"]
    assert started.json()["status"] == "进行中"

    # 并行交班应被拒绝
    again = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 1, "to_shift_id": 3},
    )
    assert again.status_code == 409

    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "已完成"

    after = client.get("/api/batches", params={"owner_shift_id": 1}).json()
    assert all(b["status"] != "在途" or b["owner_shift_id"] != 1 for b in after) or not any(
        b["status"] == "在途" for b in after
    )
    to_batches = client.get("/api/batches", params={"owner_shift_id": 2}).json()
    assert sum(1 for b in to_batches if b["status"] == "在途") >= 4

    reports = client.get("/api/reconcile").json()
    assert reports
    assert reports[0]["一致"] is True


def test_window_config_validation_and_cross_midnight(client, frozen_clock):
    # 非法时刻 / 非法星期 / 重复窗都拒绝。
    bad = [
        {"windows": [{"weekday": 1, "start": "8点", "end": "09:00"}]},
        {"windows": [{"weekday": 1, "start": "09:00", "end": "09:00"}]},
        {"windows": [
            {"weekday": 1, "start": "08:00", "end": "09:00"},
            {"weekday": 1, "start": "08:00", "end": "09:00"},
        ]},
    ]
    for payload in bad:
        r = client.put("/api/shifts/1/windows", json=payload)
        assert r.status_code == 400, payload
        assert "日历窗" in r.json()["detail"]
    # 星期越界由请求体校验拦下（422）
    r = client.put(
        "/api/shifts/1/windows",
        json={"windows": [{"weekday": 0, "start": "08:00", "end": "09:00"}]},
    )
    assert r.status_code == 422
    r = client.put(
        "/api/shifts/1/windows",
        json={"windows": [{"weekday": 8, "start": "08:00", "end": "09:00"}]},
    )
    assert r.status_code == 422

    # 跨午夜窗：周一 22:00–06:00；裁决时刻周一 10:00 → 交班方出窗。
    shift = _set_windows(client, 1, [{"weekday": 1, "start": "22:00", "end": "06:00"}])
    assert shift["in_window"] is False
    # 接班方周一/周二全天：当前 10:00 在窗，但交集要到交班方 22:00 开窗才开始。
    _set_windows(
        client,
        2,
        [{"weekday": 1, "start": "00:00", "end": "24:00"},
         {"weekday": 2, "start": "00:00", "end": "24:00"}],
    )
    status = client.get(
        "/api/handovers/window-status", params={"from_shift_id": 1, "to_shift_id": 2}
    ).json()
    assert status["from"]["in_window"] is False
    assert status["to"]["in_window"] is True
    assert status["in_window"] is False
    assert status["next_shared"][0]["text"] == "2026-11-16 22:00–2026-11-17 06:00"
    _set_all_day(client, 1, 2)


def test_start_requires_both_sides_in_window(client, frozen_clock):
    _seed_batches(1, 2, "WIN")
    # 交班方在窗外、接班方全天：拒绝。
    _set_windows(client, 1, [{"weekday": 1, "start": "08:00", "end": "09:00"}])
    _set_windows(client, 2, ALL_DAY)
    r = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2})
    assert r.status_code == 400
    assert "交班方" in r.json()["detail"]

    # 交班方全天、接班方在窗外：必须同样拒绝，禁止只检查交班方。
    _set_windows(client, 1, ALL_DAY)
    _set_windows(client, 2, [{"weekday": 1, "start": "08:00", "end": "09:00"}])
    r = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2})
    assert r.status_code == 400
    assert "接班方" in r.json()["detail"]

    status = client.get(
        "/api/handovers/window-status",
        params={"from_shift_id": 1, "to_shift_id": 2},
    ).json()
    assert status["from"]["in_window"] is True
    assert status["to"]["in_window"] is False
    assert status["in_window"] is False
    assert "接班方" in status["reason"]

    # 未配置窗 = 禁止交班，而不是全天可办。
    _set_windows(client, 2, [])
    r = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2})
    assert r.status_code == 400
    assert "未配置" in r.json()["detail"]

    _set_all_day(client, 1, 2)


def test_complete_out_of_window_fails_without_transfer(client, frozen_clock):
    _set_all_day(client, 2, 3)
    _seed_batches(2, 3, "OUT")
    started = client.post(
        "/api/handovers/start", json={"from_shift_id": 2, "to_shift_id": 3}
    )
    assert started.status_code == 200, started.text
    hid = started.json()["id"]

    # 发起后改窗：完成时刻已出窗。
    _set_windows(client, 2, [{"weekday": 1, "start": "23:00", "end": "23:59"}])
    before = client.get("/api/batches", params={"owner_shift_id": 2}).json()
    before_inflight = sorted(b["id"] for b in before if b["status"] == "在途")
    assert before_inflight

    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "失败"
    assert "日历窗" in body["reason"]
    assert body["finished_at"]
    # 零过户：批次的归属与状态完全未变。
    after = client.get("/api/batches", params={"owner_shift_id": 2}).json()
    assert sorted(b["id"] for b in after if b["status"] == "在途") == before_inflight
    moved = client.get("/api/batches", params={"owner_shift_id": 3}).json()
    assert all(b["status"] != "在途" for b in moved)

    # 完成裁决快照固化：记录双方窗定义与裁决时刻，且出窗。
    finish = body["finish_snapshot"]
    assert finish is not None and finish["phase"] == "完成"
    assert finish["in_window"] is False
    assert finish["decision_at"].startswith("2026-11-16T10:00:00")
    assert finish["from_windows"] == [{"weekday": 1, "start": "23:00", "end": "23:59"}]
    assert finish["to_windows"] == ALL_DAY

    # 失败单不得占用进行中名额，班次状态复位，可重新发起。
    shifts = {s["id"]: s for s in client.get("/api/shifts").json()}
    assert shifts[2]["status"] == "值班中"
    assert shifts[3]["status"] == "空闲"
    reports = client.get("/api/reconcile").json()
    assert reports[0]["handover_id"] == hid
    assert reports[0]["一致"] is False

    # 可重试：改回窗内后重新发起并完成，整批过户。
    _set_all_day(client, 2, 3)
    retry = client.post("/api/handovers/start", json={"from_shift_id": 2, "to_shift_id": 3})
    assert retry.status_code == 200, retry.text
    retry_id = retry.json()["id"]
    assert retry_id != hid
    finished = client.post(f"/api/handovers/{retry_id}/complete")
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "已完成"
    after_retry = client.get("/api/batches", params={"owner_shift_id": 3}).json()
    assert sorted(b["id"] for b in after_retry if b["status"] == "在途") == before_inflight


def test_consistency_failure_rolls_back_transfer(client, frozen_clock):
    _set_all_day(client, 2, 3)
    _seed_batches(2, 2, "ROLL")
    hid = client.post(
        "/api/handovers/start", json={"from_shift_id": 2, "to_shift_id": 3}
    ).json()["id"]
    before = sorted(
        b["id"]
        for b in client.get("/api/batches", params={"owner_shift_id": 2}).json()
        if b["status"] == "在途"
    )
    assert before
    to_before = sorted(
        b["id"]
        for b in client.get("/api/batches", params={"owner_shift_id": 3}).json()
        if b["status"] == "在途"
    )
    # 发起后交班方又新到在途批次：清单外的批次导致交班后交班方在途不为 0。
    _seed_batches(2, 1, "RACE")
    done = client.post(f"/api/handovers/{hid}/complete").json()
    assert done["status"] == "失败"
    assert "已回滚" in done["reason"]
    # 零半截过户：清单批次归属未变，仍在交班方名下。
    after = sorted(
        b["id"]
        for b in client.get("/api/batches", params={"owner_shift_id": 2}).json()
        if b["status"] == "在途"
    )
    assert set(before) <= set(after)
    moved = sorted(
        b["id"]
        for b in client.get("/api/batches", params={"owner_shift_id": 3}).json()
        if b["status"] == "在途"
    )
    assert moved == to_before
    report = client.get("/api/reconcile").json()[0]
    assert report["handover_id"] == hid
    assert report["一致"] is False
    assert report["detail"]["过户"] == "已回滚（零半截过户）"
    # 失败单不占用进行中名额。
    assert all(
        h["status"] != "进行中" for h in client.get("/api/handovers").json()
    )
    _set_all_day(client, 2, 3)


def test_history_snapshot_immune_to_config_change(client, frozen_clock):
    _set_all_day(client, 2, 3)
    _seed_batches(2, 2, "HIST")
    hid = client.post(
        "/api/handovers/start", json={"from_shift_id": 2, "to_shift_id": 3}
    ).json()["id"]
    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.json()["status"] == "已完成"
    original_finish = done.json()["finish_snapshot"]
    assert original_finish["in_window"] is True

    # 事后把双方窗清空：已完成单的裁决快照与状态不得改判。
    _set_windows(client, 2, [])
    _set_windows(client, 3, [])
    again = client.get(f"/api/handovers/{hid}").json()
    assert again["status"] == "已完成"
    assert again["finish_snapshot"] == original_finish
    assert again["finish_snapshot"]["from_windows"] == ALL_DAY
    assert again["finish_snapshot"]["to_windows"] == ALL_DAY

    # 直接改库也必须被快照表触发器拒绝。
    with sqlite3.connect(db_path) as raw:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "UPDATE handover_window_snapshots SET in_window = 0 WHERE handover_id = ?",
                (hid,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "DELETE FROM handover_window_snapshots WHERE handover_id = ?", (hid,)
            )

    _set_all_day(client, 2, 3)
