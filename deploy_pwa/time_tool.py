"""Small, read-only adapter for the open-source MCP time tool contract.

No personal data, external service, or model call is involved. The server
controls the supported timezones and returns an explicit source timestamp.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ZONES = {
    "Asia/Shanghai": "中国大陆",
    "Asia/Taipei": "台湾",
    "Asia/Tokyo": "日本",
    "Asia/Singapore": "新加坡",
    "Europe/London": "英国",
    "America/New_York": "美国东部",
    "America/Los_Angeles": "美国西部",
    "UTC": "UTC",
}


class TimeToolError(ValueError):
    pass


def _zone(name):
    if not isinstance(name, str) or name not in ZONES:
        raise TimeToolError("暂不支持该时区")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise TimeToolError("服务器时区资料尚未配置") from exc


def convert_time(value, source, target):
    """Convert a local minute, rejecting DST gaps and ambiguous hours."""
    if not isinstance(value, str) or len(value) != 16:
        raise TimeToolError("时间格式应为 YYYY-MM-DDTHH:MM")
    try:
        naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError as exc:
        raise TimeToolError("时间格式应为 YYYY-MM-DDTHH:MM") from exc
    source_zone = _zone(source)
    target_zone = _zone(target)
    first = naive.replace(tzinfo=source_zone, fold=0)
    second = naive.replace(tzinfo=source_zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise TimeToolError("该时间处于夏令时间切换，请换一个时间")
    utc = first.astimezone(timezone.utc)
    if utc.astimezone(source_zone).replace(tzinfo=None) != naive:
        raise TimeToolError("该本地时间不存在，请换一个时间")
    result = utc.astimezone(target_zone)
    return {
        "source": first.isoformat(timespec="minutes"),
        "target": result.isoformat(timespec="minutes"),
        "source_timezone": source,
        "target_timezone": target,
        "utc": utc.isoformat(timespec="minutes"),
        "tool": "time_convert_local",
        "source_kind": "server_calculated",
    }
