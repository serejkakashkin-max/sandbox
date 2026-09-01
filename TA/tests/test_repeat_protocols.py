import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from TA.protocol_history import (
    ProtocolRef,
    build_repeat_groups,
    parse_manual_protocol_entries,
    select_recent_protocols,
)


MSK = ZoneInfo("Europe/Moscow")


class ProtocolSelectionTests(TestCase):
    def test_newest_file_wins_inside_same_friday_week(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "28.08.2026.txt"
            newer = root / "29.08.2026.txt"
            previous = root / "21.08.2026.txt"
            for path in (older, newer, previous):
                path.write_text("Протокол", encoding="utf-8")
            os.utime(previous, (1_000, 1_000))
            os.utime(older, (2_000, 2_000))
            os.utime(newer, (3_000, 3_000))

            refs = select_recent_protocols((newer, older, previous), limit=5)

            self.assertEqual(
                [ref.path.name for ref in refs],
                ["29.08.2026.txt", "21.08.2026.txt"],
            )

    def test_text_fallback_stops_before_in_work_section(self):
        text = """1. INC0001 — Иванов И.И. — WARNING1
   Комментарий: уточнить причину

В работе — переносятся на следующую неделю:
2. INC0002 — Петров П.П. — В РАБОТЕ"""

        rows = parse_manual_protocol_entries(text, "28.08.2026.txt")

        self.assertEqual([row["incident_id"] for row in rows], ["INC0001"])
        self.assertEqual(rows[0]["tag"], "WARNING1")
        self.assertEqual(rows[0]["comment"], "уточнить причину")


class RepeatGroupingTests(TestCase):
    @staticmethod
    def protocol_refs(*names):
        return tuple(
            ProtocolRef(
                path=Path(name),
                report_week=datetime.strptime(name[:10], "%d.%m.%Y").replace(
                    tzinfo=MSK
                ),
                sort_time=float(len(names) - index),
            )
            for index, name in enumerate(names)
        )

    def test_incident_appears_under_newest_protocol_only(self):
        current = [{"ID инцидента": "inc0001"}]
        refs = self.protocol_refs("28.08.2026.txt", "21.08.2026.txt")
        rows = {
            "28.08.2026.txt": [{"incident_id": "INC0001", "comment": "новый"}],
            "21.08.2026.txt": [{"incident_id": "INC0001", "comment": "старый"}],
        }

        groups = build_repeat_groups(current, refs, rows)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].protocol.filename, "28.08.2026.txt")
        self.assertEqual(len(groups[0].incidents[0].history), 2)
