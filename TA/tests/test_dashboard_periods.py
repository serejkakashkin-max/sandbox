from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from TA.dashboard_view import build_periods, friday_period_start, parse_created


MSK = ZoneInfo("Europe/Moscow")


class CreatedDateTests(TestCase):
    def test_parses_supported_day_first_and_iso_values(self):
        self.assertEqual(
            parse_created("28.08.2026 08:17"),
            datetime(2026, 8, 28, 8, 17, tzinfo=MSK),
        )
        self.assertEqual(
            parse_created("2026-08-28 08:17:00"),
            datetime(2026, 8, 28, 8, 17, tzinfo=MSK),
        )

    def test_unparseable_value_returns_none(self):
        self.assertIsNone(parse_created("нет даты"))

    def test_friday_boundary_is_inclusive(self):
        moment = datetime(2026, 8, 28, 0, 0, tzinfo=MSK)
        self.assertEqual(friday_period_start(moment), moment)


class PeriodBuildTests(TestCase):
    def test_friday_upload_builds_current_from_previous_friday(self):
        incidents = [
            {"ID инцидента": "A", "Создан": "21.08.2026 00:00"},
            {"ID инцидента": "B", "Создан": "28.08.2026 08:17"},
            {"ID инцидента": "C", "Создан": "20.08.2026 23:59"},
        ]
        result = build_periods(
            incidents,
            datetime(2026, 8, 28, 9, 0, tzinfo=MSK),
        )

        self.assertEqual(
            [row["ID инцидента"] for row in result.current.incidents],
            ["A", "B"],
        )
        self.assertEqual(
            result.current.start,
            datetime(2026, 8, 21, 0, 0, tzinfo=MSK),
        )
        self.assertEqual(
            result.current.end,
            datetime(2026, 8, 28, 8, 17, tzinfo=MSK),
        )

    def test_non_friday_upload_has_no_current_period(self):
        result = build_periods(
            [{"ID инцидента": "A", "Создан": "10.06.2026 12:00"}],
            datetime(2026, 8, 26, 9, 0, tzinfo=MSK),
        )

        self.assertIsNone(result.current)
        self.assertEqual(len(result.weeks), 1)

    def test_invalid_date_stays_in_unknown_period(self):
        result = build_periods(
            [{"ID инцидента": "A", "Создан": "нет даты"}],
            datetime(2026, 8, 26, 9, 0, tzinfo=MSK),
        )

        self.assertEqual(result.unknown.label, "Дата не определена")
        self.assertEqual(result.unknown.incidents[0]["ID инцидента"], "A")
