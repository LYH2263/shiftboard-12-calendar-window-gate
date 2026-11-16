"""班次交班日历窗：星期几 + 本地墙钟起止时刻。

约定：
- weekday 使用 ISO 1=周一 … 7=周日；
- 起止时刻为本地墙钟 HH:MM，结束允许 24:00；
- 结束时刻不大于起始时刻视为跨午夜（如 22:00–06:00）；
- 一个班次可配多段窗；未配置窗 = 禁止发起/完成交班（不是“全天可办”）。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def parse_time_minutes(text: str, *, is_end: bool) -> int:
    raw = str(text or "").strip()
    parts = raw.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(f"时刻格式应为 HH:MM：{raw or '空'}")
    hour, minute = int(parts[0]), int(parts[1])
    if minute > 59:
        raise ValueError(f"分钟必须在 00–59：{raw}")
    if hour > 24 or (hour == 24 and minute != 0):
        raise ValueError(f"时刻超出范围：{raw}")
    total = hour * 60 + minute
    if total == 24 * 60 and not is_end:
        raise ValueError("起始时刻不能是 24:00")
    return total


def normalize_windows(raw_windows: list[dict] | None) -> list[dict]:
    """校验并规范化窗口定义；非法直接抛 ValueError（中文原因）。"""
    out: list[dict] = []
    seen: set[tuple] = set()
    for raw in raw_windows or []:
        try:
            weekday = int(raw.get("weekday"))
        except (TypeError, ValueError):
            raise ValueError("星期取值必须是 1–7（1=周一）") from None
        if not 1 <= weekday <= 7:
            raise ValueError("星期取值必须是 1–7（1=周一）")
        start_min = parse_time_minutes(raw.get("start"), is_end=False)
        end_min = parse_time_minutes(raw.get("end"), is_end=True)
        if start_min == end_min:
            raise ValueError("同一天内起止时刻不能相同；全天请用 00:00–24:00")
        start_text = f"{start_min // 60:02d}:{start_min % 60:02d}"
        end_text = f"{end_min // 60:02d}:{end_min % 60:02d}"
        key = (weekday, start_min, end_min)
        if key in seen:
            raise ValueError(f"重复的日历窗：{WEEKDAY_NAMES[weekday - 1]} {start_text}–{end_text}")
        seen.add(key)
        out.append(
            {
                "weekday": weekday,
                "start": start_text,
                "end": end_text,
                "start_min": start_min,
                "end_min": end_min,
            }
        )
    out.sort(key=lambda w: (w["weekday"], w["start_min"], w["end_min"]))
    return out


def public_windows(windows: list[dict]) -> list[dict]:
    return [
        {"weekday": w["weekday"], "start": w["start"], "end": w["end"]}
        for w in windows
    ]


def format_window(w: dict) -> str:
    return f"{WEEKDAY_NAMES[w['weekday'] - 1]} {w['start']}–{w['end']}"


def describe_windows(windows: list[dict]) -> str:
    if not windows:
        return "未配置（禁止交班）"
    return "；".join(format_window(w) for w in windows)


def _window_interval(w: dict, day) -> tuple[datetime, datetime]:
    base = datetime.combine(day, time(0, 0))
    start = base + timedelta(minutes=w["start_min"])
    end = base + timedelta(minutes=w["end_min"])
    if w["end_min"] <= w["start_min"]:
        end += timedelta(days=1)
    return start, end


def expand_intervals(
    windows: list[dict], range_start: datetime, range_end: datetime
) -> list[tuple[datetime, datetime]]:
    """把周历窗展开成 [range_start, range_end) 内的绝对时间段（自动合并）。"""
    if not windows or range_end <= range_start:
        return []
    intervals: list[tuple[datetime, datetime]] = []
    day = range_start.date() - timedelta(days=1)
    last_day = range_end.date() + timedelta(days=1)
    while day <= last_day:
        for w in windows:
            if w["weekday"] != day.isoweekday():
                continue
            s, e = _window_interval(w, day)
            if e <= range_start or s >= range_end:
                continue
            intervals.append((max(s, range_start), min(e, range_end)))
        day += timedelta(days=1)
    intervals.sort()
    merged: list[tuple[datetime, datetime]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def in_window(windows: list[dict], at: datetime) -> bool:
    if not windows:
        return False
    probe = expand_intervals(windows, at, at + timedelta(seconds=1))
    return bool(probe)


def shared_intervals(
    from_windows: list[dict],
    to_windows: list[dict],
    range_start: datetime,
    range_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """交班方 ∩ 接班方日历窗。"""
    left = expand_intervals(from_windows, range_start, range_end)
    right = expand_intervals(to_windows, range_start, range_end)
    out: list[tuple[datetime, datetime]] = []
    i = j = 0
    while i < len(left) and j < len(right):
        s = max(left[i][0], right[j][0])
        e = min(left[i][1], right[j][1])
        if s < e:
            out.append((s, e))
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return out


def format_interval(start: datetime, end: datetime) -> str:
    return f"{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M}"
