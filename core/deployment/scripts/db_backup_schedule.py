"""Разбор POSTGRES_BACKUP_SCHEDULE: день, интервал, неделя."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCHEDULE_OFF = frozenset({'', 'off', 'none', 'false', '0', 'disabled'})
_SCHEDULE_DAILY_DEFAULT = frozenset({'true', 'on', 'daily', 'yes'})
_WEEKDAYS = {
    'sun': 0,
    'sunday': 0,
    'mon': 1,
    'monday': 1,
    'tue': 2,
    'tuesday': 2,
    'wed': 3,
    'wednesday': 3,
    'thu': 4,
    'thursday': 4,
    'fri': 5,
    'friday': 5,
    'sat': 6,
    'saturday': 6,
}
_WEEKDAY_SCHTASKS = ('SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT')


@dataclass(frozen=True)
class BackupSchedule:
    kind: str
    hour: int = 3
    minute: int = 0
    every_hours: int = 0
    weekday: int = 0

    def label(self) -> str:
        if self.kind == 'interval':
            return f'every {self.every_hours}h'
        if self.kind == 'weekly':
            return f'weekly {_WEEKDAY_SCHTASKS[self.weekday].lower()} {self.hour:02d}:{self.minute:02d}'
        return f'daily {self.hour:02d}:{self.minute:02d}'

    def cron_expr(self) -> str:
        if self.kind == 'interval':
            return f'{self.minute} */{self.every_hours} * * *'
        if self.kind == 'weekly':
            return f'{self.minute} {self.hour} * * {self.weekday}'
        return f'{self.minute} {self.hour} * * *'

    def schtasks_args(self) -> list[str]:
        if self.kind == 'interval':
            return ['/SC', 'HOURLY', '/MO', str(self.every_hours)]
        time_str = f'{self.hour:02d}:{self.minute:02d}'
        if self.kind == 'weekly':
            return ['/SC', 'WEEKLY', '/D', _WEEKDAY_SCHTASKS[self.weekday], '/ST', time_str]
        return ['/SC', 'DAILY', '/ST', time_str]


def _parse_clock(text: str) -> tuple[int, int] | None:
    if re.fullmatch(r'\d{1,2}', text):
        hour = int(text)
        if 0 <= hour <= 23:
            return hour, 0
        return None
    match = re.fullmatch(r'(\d{1,2}):(\d{2})', text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def parse_backup_schedule(raw: str) -> BackupSchedule | None:
    """
    off | HH:MM | daily HH:MM | every 6h | weekly sun 03:00
    true/on/daily без времени — каждый день в 03:00.
    """
    text = (raw or '').strip().lower()
    if text in _SCHEDULE_OFF:
        return None
    if text in _SCHEDULE_DAILY_DEFAULT:
        return BackupSchedule(kind='daily', hour=3, minute=0)
    clock = _parse_clock(text)
    if clock:
        return BackupSchedule(kind='daily', hour=clock[0], minute=clock[1])
    daily = re.fullmatch(r'daily(?:\s+|:)(.+)', text)
    if daily:
        clock = _parse_clock(daily.group(1).strip())
        if clock:
            return BackupSchedule(kind='daily', hour=clock[0], minute=clock[1])
        return None
    interval = re.fullmatch(r'(?:every\s+)?(\d+)\s*h(?:ours?)?', text)
    if interval:
        hours = int(interval.group(1))
        if 1 <= hours <= 24:
            return BackupSchedule(kind='interval', every_hours=hours)
        return None
    weekly = re.fullmatch(r'weekly\s+(\w+)(?:\s+|:)(.+)', text)
    if weekly:
        day_raw = weekly.group(1)
        clock = _parse_clock(weekly.group(2).strip())
        if clock is None:
            return None
        if day_raw in _WEEKDAYS:
            weekday = _WEEKDAYS[day_raw]
        elif day_raw.isdigit() and 0 <= int(day_raw) <= 6:
            weekday = int(day_raw)
        else:
            return None
        return BackupSchedule(kind='weekly', hour=clock[0], minute=clock[1], weekday=weekday)
    return None


def backup_schedule_time() -> BackupSchedule | None:
    from deployment_env import read_env  # noqa: WPS433

    return parse_backup_schedule(read_env('POSTGRES_BACKUP_SCHEDULE', 'off'))
