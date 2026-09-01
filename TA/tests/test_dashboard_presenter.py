from unittest import TestCase

from TA.dashboard_view import (
    build_metrics,
    choose_default_section,
    choose_default_status,
    global_search,
    status_group_key,
)


class StatusGroupingTests(TestCase):
    def test_in_work_is_grouped_with_skipped(self):
        self.assertEqual(status_group_key("in_work", "skipped"), "skipped")

    def test_default_status_prefers_errors_then_warnings_then_all(self):
        self.assertEqual(
            choose_default_status({"errors": [1], "warnings": [2], "all": [1, 2]}),
            "errors",
        )
        self.assertEqual(
            choose_default_status({"errors": [], "warnings": [2], "all": [2]}),
            "warnings",
        )
        self.assertEqual(
            choose_default_status({"errors": [], "warnings": [], "all": [3]}),
            "all",
        )


class SectionDefaultTests(TestCase):
    def test_repeat_has_priority_over_current_period(self):
        periods = type("Periods", (), {"current": object(), "weeks": ()})()
        self.assertEqual(choose_default_section([object()], periods), "repeat")


class GlobalSearchTests(TestCase):
    def test_search_ignores_description_and_matches_id_or_executor_only(self):
        rows = (
            {
                "ID инцидента": "INC001",
                "Исполнитель": "Иванов И.И.",
                "Описание": "другая строка",
            },
            {
                "ID инцидента": "INC002",
                "Исполнитель": "Петров П.П.",
                "Описание": "Иванов",
            },
        )

        self.assertEqual(
            [row["ID инцидента"] for row in global_search(rows, "иванов")],
            ["INC001"],
        )


class MetricsTests(TestCase):
    @staticmethod
    def rows_for_all_outcomes():
        return (
            {
                "incident_id": "INC001",
                "profile": "manual",
                "analysis": {"outcome": "error"},
            },
            {
                "incident_id": "INC002",
                "profile": "manual",
                "analysis": {"outcome": "warning"},
            },
            {
                "incident_id": "INC003",
                "profile": "manual",
                "analysis": {"outcome": "passed"},
            },
            {
                "incident_id": "INC004",
                "profile": "in_work",
                "analysis": {"outcome": "skipped"},
            },
        )

    def test_primary_categories_sum_to_total(self):
        rows = self.rows_for_all_outcomes()
        metrics = build_metrics(rows, {"INC001"})

        self.assertEqual(
            metrics["total"],
            metrics["errors"]
            + metrics["warnings"]
            + metrics["correct"]
            + metrics["skipped"],
        )
        self.assertEqual(metrics["repeated"], 1)
