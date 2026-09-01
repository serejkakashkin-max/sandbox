"""Pure presentation helpers for the incident dashboard.

The functions in this module deliberately know nothing about Flask or the
database.  This keeps period grouping, search and counters deterministic and
easy to verify independently from the web interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import math
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


MSK = ZoneInfo("Europe/Moscow")
CREATED_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%d.%m.%y %H:%M:%S",
    "%d.%m.%y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


@dataclass(frozen=True)
class PeriodView:
    key: str
    label: str
    start: datetime | None
    end: datetime | None
    incidents: tuple[Mapping[str, Any], ...]
    is_current: bool = False
    is_unknown: bool = False


@dataclass(frozen=True)
class PeriodCollection:
    current: PeriodView | None
    weeks: tuple[PeriodView, ...]
    unknown: PeriodView | None


def _aware_msk(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=MSK)
    return value.astimezone(MSK)


def parse_created(value: Any) -> datetime | None:
    """Parse an Excel ``Создан`` value into a Moscow-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware_msk(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=MSK)
    if hasattr(value, "to_pydatetime"):
        try:
            converted = value.to_pydatetime()
        except (TypeError, ValueError, OverflowError):
            converted = None
        if isinstance(converted, datetime):
            return _aware_msk(converted)
    if isinstance(value, float) and math.isnan(value):
        return None

    text = str(value).strip()
    if not text or text.casefold() in {"nan", "nat", "none"}:
        return None
    for date_format in CREATED_FORMATS:
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=MSK)
        except ValueError:
            continue
    try:
        return _aware_msk(datetime.fromisoformat(text))
    except ValueError:
        return None


def friday_period_start(value: datetime) -> datetime:
    """Return the inclusive Friday 00:00 boundary containing ``value``."""
    moment = _aware_msk(value)
    days_since_friday = (moment.weekday() - 4) % 7
    start_date = (moment - timedelta(days=days_since_friday)).date()
    return datetime.combine(start_date, time.min, tzinfo=MSK)


def _period_label(start: datetime, end: datetime) -> str:
    if start.year == end.year:
        return f"{start:%d.%m}–{end:%d.%m}"
    return f"{start:%d.%m.%Y}–{end:%d.%m.%Y}"


def build_periods(
    incidents: Sequence[Mapping[str, Any]],
    loaded_at: datetime,
) -> PeriodCollection:
    """Group incidents into Friday reporting weeks and an optional current cut."""
    loaded = _aware_msk(loaded_at)
    parsed_rows = tuple((row, parse_created(row.get("Создан"))) for row in incidents)
    known_dates = tuple(moment for _, moment in parsed_rows if moment is not None)

    current: PeriodView | None = None
    current_ids: set[int] = set()
    if loaded.weekday() == 4 and known_dates:
        start = friday_period_start(loaded) - timedelta(days=7)
        latest = max(known_dates)
        current_rows = tuple(
            row
            for row, moment in parsed_rows
            if moment is not None and start <= moment <= latest
        )
        if current_rows:
            current_ids = {id(row) for row in current_rows}
            current = PeriodView(
                key="current",
                label="Текущий",
                start=start,
                end=latest,
                incidents=current_rows,
                is_current=True,
            )

    buckets: dict[datetime, list[Mapping[str, Any]]] = {}
    unknown_rows: list[Mapping[str, Any]] = []
    for row, moment in parsed_rows:
        if moment is None:
            unknown_rows.append(row)
            continue
        if id(row) in current_ids:
            continue
        start = friday_period_start(moment)
        buckets.setdefault(start, []).append(row)

    weeks = tuple(
        PeriodView(
            key=start.date().isoformat(),
            label=_period_label(start, start + timedelta(days=7)),
            start=start,
            end=start + timedelta(days=7),
            incidents=tuple(buckets[start]),
        )
        for start in sorted(buckets, reverse=True)
    )
    unknown = (
        PeriodView(
            key="unknown",
            label="Дата не определена",
            start=None,
            end=None,
            incidents=tuple(unknown_rows),
            is_unknown=True,
        )
        if unknown_rows
        else None
    )
    return PeriodCollection(current=current, weeks=weeks, unknown=unknown)


STATUS_KEYS = ("errors", "warnings", "all", "correct", "skipped")


def status_group_key(profile: str, outcome: str) -> str:
    """Map an audit result to the five compact dashboard statuses."""
    if str(profile or "").casefold() == "in_work":
        return "skipped"
    if outcome == "error":
        return "errors"
    if outcome == "warning":
        return "warnings"
    if outcome == "passed":
        return "correct"
    return "skipped"


def build_status_groups(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = {key: [] for key in STATUS_KEYS}
    groups["all"] = list(rows)
    for row in rows:
        analysis = row.get("analysis") or {}
        key = status_group_key(
            str(row.get("profile") or ""),
            str(analysis.get("outcome") or ""),
        )
        groups[key].append(row)
    return groups


def choose_default_status(groups: Mapping[str, Sequence[Any]]) -> str:
    for key in ("errors", "warnings", "all"):
        if groups.get(key):
            return key
    return "all"


def choose_default_section(
    repeat_groups: Sequence[Any],
    periods: PeriodCollection | Any,
) -> str:
    if repeat_groups:
        return "repeat"
    if getattr(periods, "current", None) is not None:
        return "current"
    if getattr(periods, "weeks", ()): 
        return "period"
    return "all"


def global_search(
    rows: Sequence[Mapping[str, Any]],
    query: str,
) -> tuple[Mapping[str, Any], ...]:
    needle = str(query or "").strip().casefold()
    if not needle:
        return tuple(rows)
    return tuple(
        row
        for row in rows
        if needle
        in str(row.get("incident_id") or row.get("ID инцидента") or "").casefold()
        or needle
        in str(row.get("Исполнитель") or row.get("executor_display") or "").casefold()
    )


def build_metrics(
    rows: Sequence[Mapping[str, Any]],
    repeated_ids: set[str] | Sequence[str],
) -> dict[str, int]:
    repeated = {str(value).strip().upper() for value in repeated_ids}
    metrics = {
        "total": len(rows),
        "errors": 0,
        "warnings": 0,
        "correct": 0,
        "skipped": 0,
        "repeated": 0,
    }
    for row in rows:
        analysis = row.get("analysis") or {}
        key = status_group_key(
            str(row.get("profile") or ""),
            str(analysis.get("outcome") or ""),
        )
        metrics[key] += 1
        incident_id = str(
            row.get("incident_id") or row.get("ID инцидента") or ""
        ).strip().upper()
        if incident_id in repeated:
            metrics["repeated"] += 1
    return metrics
