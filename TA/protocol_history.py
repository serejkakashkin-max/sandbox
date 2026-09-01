"""Selection and presentation helpers for repeat protocol checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .dashboard_view import friday_period_start


MSK = ZoneInfo("Europe/Moscow")
PROTOCOL_DATE_RE = re.compile(r"^(\d{2}\.\d{2}\.\d{4})(?: \(\d+\))?\.txt$", re.I)
ENTRY_RE = re.compile(
    r"^\s*\d+\.\s+(?P<incident>[^\s—-]+)\s+[—-]\s+(?P<rest>.+?)\s*$"
)
COMMENT_RE = re.compile(r"^\s*Комментарий\s*:\s*(?P<comment>.*)$", re.I)


@dataclass(frozen=True)
class ProtocolRef:
    path: Path
    report_week: datetime
    sort_time: float

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def label(self) -> str:
        return self.report_week.strftime("%d.%m.%Y")


@dataclass(frozen=True)
class RepeatIncident:
    incident: Mapping[str, Any]
    history: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class RepeatProtocolGroup:
    protocol: ProtocolRef
    incidents: tuple[RepeatIncident, ...]


def _protocol_moment(path: Path) -> tuple[datetime, float]:
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    match = PROTOCOL_DATE_RE.match(path.name)
    if match:
        moment = datetime.strptime(match.group(1), "%d.%m.%Y").replace(tzinfo=MSK)
    else:
        moment = datetime.fromtimestamp(modified, tz=MSK)
    return moment, modified


def select_recent_protocols(
    files: Sequence[Path],
    limit: int = 5,
) -> tuple[ProtocolRef, ...]:
    """Select the newest protocol from each of the newest unique report weeks."""
    if limit <= 0:
        return ()
    newest_by_week: dict[datetime, ProtocolRef] = {}
    for raw_path in files:
        path = Path(raw_path)
        moment, modified = _protocol_moment(path)
        week = friday_period_start(moment)
        candidate = ProtocolRef(path=path, report_week=week, sort_time=modified)
        existing = newest_by_week.get(week)
        if existing is None or (candidate.sort_time, candidate.filename) > (
            existing.sort_time,
            existing.filename,
        ):
            newest_by_week[week] = candidate
    return tuple(
        newest_by_week[week]
        for week in sorted(newest_by_week, reverse=True)[:limit]
    )


def parse_manual_protocol_entries(
    text: str,
    protocol_file: str,
) -> tuple[dict[str, str], ...]:
    """Parse only manually selected entries from an existing text protocol."""
    rows: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        if line.strip().casefold().startswith("в работе"):
            break
        comment_match = COMMENT_RE.match(line)
        if comment_match and rows:
            rows[-1]["comment"] = comment_match.group("comment").strip()
            continue
        match = ENTRY_RE.match(line)
        if not match:
            continue
        incident_id = match.group("incident").strip().upper()
        parts = re.split(r"\s+[—-]\s+", match.group("rest"), maxsplit=1)
        executor = parts[0].strip()
        tag = parts[1].strip() if len(parts) > 1 else ""
        if tag.casefold() == "в работе":
            continue
        rows.append(
            {
                "incident_id": incident_id,
                "executor": executor,
                "tag": tag,
                "comment": "",
                "protocol_file": protocol_file,
            }
        )
    return tuple(rows)


def _incident_id(row: Mapping[str, Any]) -> str:
    return str(row.get("incident_id") or row.get("ID инцидента") or "").strip().upper()


def build_repeat_groups(
    current_incidents: Sequence[Mapping[str, Any]],
    protocol_refs: Sequence[ProtocolRef],
    rows_by_protocol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[RepeatProtocolGroup, ...]:
    """Put each repeated incident under its newest matching protocol only."""
    indexed: dict[str, dict[str, Mapping[str, Any]]] = {}
    for ref in protocol_refs:
        indexed[ref.filename] = {
            _incident_id(row): row
            for row in rows_by_protocol.get(ref.filename, ())
            if _incident_id(row)
        }

    grouped: dict[str, list[RepeatIncident]] = {ref.filename: [] for ref in protocol_refs}
    for incident in current_incidents:
        incident_id = _incident_id(incident)
        if not incident_id:
            continue
        history: list[Mapping[str, Any]] = []
        newest_filename = ""
        for ref in protocol_refs:
            hit = indexed.get(ref.filename, {}).get(incident_id)
            if hit is None:
                continue
            if not newest_filename:
                newest_filename = ref.filename
            enriched = dict(hit)
            enriched.setdefault("protocol_file", ref.filename)
            enriched.setdefault("protocol_label", ref.path.stem)
            history.append(enriched)
        if newest_filename:
            grouped[newest_filename].append(
                RepeatIncident(incident=incident, history=tuple(history))
            )

    return tuple(
        RepeatProtocolGroup(protocol=ref, incidents=tuple(grouped[ref.filename]))
        for ref in protocol_refs
        if grouped[ref.filename]
    )
