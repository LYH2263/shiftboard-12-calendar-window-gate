from datetime import datetime

import pytest

from app.services import windows as win


def _w(weekday, start, end):
    return win.normalize_windows([{"weekday": weekday, "start": start, "end": end}])


def test_normalize_rejects_bad_time():
    with pytest.raises(ValueError):
        win.normalize_windows([{"weekday": 1, "start": "8am", "end": "9:00"}])
    # 宽松接受 "8:00"，落库统一规范成 "08:00"
    assert win.normalize_windows([{"weekday": 1, "start": "8:00", "end": "9:00"}])[0][
        "start"
    ] == "08:00"
    with pytest.raises(ValueError):
        win.normalize_windows([{"weekday": 8, "start": "08:00", "end": "09:00"}])
    with pytest.raises(ValueError):
        win.normalize_windows([{"weekday": 1, "start": "24:00", "end": "24:00"}])
    with pytest.raises(ValueError):
        win.normalize_windows([{"weekday": 1, "start": "08:00", "end": "08:00"}])
    with pytest.raises(ValueError):
        win.normalize_windows(
            [
                {"weekday": 1, "start": "08:00", "end": "09:00"},
                {"weekday": 1, "start": "08:00", "end": "09:00"},
            ]
        )


def test_simple_window_membership():
    w = _w(1, "08:00", "10:00")  # 2026-11-16 is Monday
    assert win.in_window(w, datetime(2026, 11, 16, 9, 0))
    assert win.in_window(w, datetime(2026, 11, 16, 8, 0))  # 起点含
    assert not win.in_window(w, datetime(2026, 11, 16, 10, 0))  # 终点不含
    assert not win.in_window(w, datetime(2026, 11, 16, 7, 59))
    assert not win.in_window(w, datetime(2026, 11, 17, 9, 0))  # 周二不在窗
    assert not win.in_window([], datetime(2026, 11, 16, 9, 0))  # 无窗=禁止


def test_cross_midnight_window():
    w = _w(1, "22:00", "06:00")  # 周一 22:00 跨到周二 06:00
    assert win.in_window(w, datetime(2026, 11, 16, 23, 0))
    assert win.in_window(w, datetime(2026, 11, 17, 1, 0))
    assert win.in_window(w, datetime(2026, 11, 17, 5, 59))
    assert not win.in_window(w, datetime(2026, 11, 17, 6, 0))
    assert not win.in_window(w, datetime(2026, 11, 17, 23, 0))  # 周二 23:00 不在


def test_full_day_window():
    w = _w(1, "00:00", "24:00")
    assert win.in_window(w, datetime(2026, 11, 16, 0, 0))
    assert win.in_window(w, datetime(2026, 11, 16, 23, 59))
    assert not win.in_window(w, datetime(2026, 11, 17, 0, 0))


def test_shared_intervals_requires_both_sides():
    a = _w(1, "08:00", "12:00")
    b = _w(1, "10:00", "14:00")
    at = datetime(2026, 11, 16, 9, 0)
    shared = win.shared_intervals(a, b, at, at.replace(hour=23))
    assert [(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in shared] == [("10:00", "12:00")]
    # 交班方在窗、接班方出窗 → 交集为空
    assert not win.shared_intervals(a, b, datetime(2026, 11, 16, 8, 30),
                                    datetime(2026, 11, 16, 9, 30))
    assert win.in_window(a, datetime(2026, 11, 16, 8, 30))
    assert not win.in_window(b, datetime(2026, 11, 16, 8, 30))
    # 完全不重叠
    c = _w(1, "13:00", "14:00")
    assert not win.shared_intervals(a, c, at, at.replace(hour=23))


def test_shared_intervals_cross_midnight():
    a = _w(1, "22:00", "06:00")  # 周一 22:00 → 周二 06:00
    b = _w(2, "00:00", "23:59")  # 周二白天
    at = datetime(2026, 11, 17, 0, 0)
    shared = win.shared_intervals(a, b, at, datetime(2026, 11, 17, 12, 0))
    # 周二 00:00 前接班方未开窗，交集从周二 00:00 起
    assert [(s.strftime("%m-%d %H:%M"), e.strftime("%m-%d %H:%M")) for s, e in shared] == [
        ("11-17 00:00", "11-17 06:00")
    ]
