from unittest import TestCase

from TA.audit_engine import audit_incident, classify_incident


def check_by_id(result, rule_id):
    return next(
        (check for check in result["checks"] if check["rule_id"] == rule_id),
        None,
    )


class TksTagRuleTests(TestCase):
    def test_missing_tks_tag_is_a_warning_for_every_profile(self):
        incidents = (
            {
                "Статус": "В работе",
                "Код закрытия": "",
                "Решение": "Собрано ТКС для диагностики",
                "Тег": "WARNING1",
            },
            {
                "Статус": "Закрыт",
                "Код закрытия": "Автовыполнение",
                "Решение": "Сбор ТКС выполнен роботом",
                "Тег": "",
            },
            {
                "Статус": "Закрыт",
                "Тип стенда": "MAJOR-GO",
                "Решение": "Выполнен сбор ткс",
                "Тег": "ДРУГОЙ",
            },
        )

        for incident in incidents:
            with self.subTest(profile=incident):
                result = audit_incident(incident)
                check = check_by_id(result, "TKS_TAG_REQUIRED")

                self.assertIsNotNone(check)
                self.assertEqual(check["status"], "remark")
                self.assertEqual(check["severity"], "warning")
                self.assertEqual(result["outcome"], "warning")

    def test_composite_tag_with_tks_satisfies_rule(self):
        result = audit_incident(
            {
                "Статус": "Закрыт",
                "Код закрытия": "Автовыполнение",
                "Решение": "Собрано ТКС для диагностики",
                "Тег": "WARNING1; ТКС; КРИТИЧЕСКИЙ",
            }
        )

        check = check_by_id(result, "TKS_TAG_REQUIRED")
        self.assertIsNotNone(check)
        self.assertEqual(check["status"], "passed")
        self.assertEqual(check["severity"], "none")
        self.assertEqual(result["outcome"], "skipped")


class NewObjectProfileTests(TestCase):
    def test_exact_new_object_redirect_is_not_audited(self):
        solutions = (
            "Создан инцидент INC0000000123 на новом объекте",
            "  создан   инцидент   inc0000000123   на новом объекте.  ",
        )

        for solution in solutions:
            with self.subTest(solution=solution):
                incident = {
                    "Статус": "Закрыт",
                    "Код закрытия": "Инцидент не подтвержден",
                    "Решение": solution,
                }
                result = audit_incident(incident)

                self.assertEqual(classify_incident(incident), "new_object")
                self.assertEqual(result["profile"], "new_object")
                self.assertEqual(result["outcome"], "skipped")
                self.assertEqual(result["Статус"], "Проверка не проводится")

    def test_additional_text_does_not_bypass_manual_audit(self):
        incident = {
            "Статус": "Закрыт",
            "Код закрытия": "Инцидент не подтвержден",
            "Решение": (
                "Создан инцидент INC0000000123 на новом объекте. "
                "Причина выясняется."
            ),
        }

        result = audit_incident(incident)

        self.assertEqual(classify_incident(incident), "manual")
        self.assertEqual(result["profile"], "manual")
        self.assertEqual(result["outcome"], "error")


class WorkaroundTaskRuleTests(TestCase):
    def _incident(self, task_event):
        return {
            "ID инцидента": "INC0000000777",
            "Статус": "Закрыт",
            "Код закрытия": "Решено обходным путём",
            "Фактическое время возникновения": "23.02.2026 16:20",
            "Создан": "23.02.2026 16:20",
            "Фактическое время окончания": "23.02.2026 16:30",
            "Влияние на клиентский сервисе": "Нет",
            "Причина": "Техническая причина",
            "Описание": "Сервис временно недоступен",
            "Решение": (
                "Время начала инцидента: 23.02.2026 16:20\n"
                "Время окончания инцидента: 23.02.2026 16:30\n"
                "Причина: ошибка конфигурации сервиса\n"
                "Влияние на пользователей отсутствует\n"
                "Краткая хронология:\n"
                "16:20 — обнаружена ошибка\n"
                f"16:25 — {task_event}\n"
                "16:30 — доступность восстановлена"
            ),
        }

    def test_task_reference_anywhere_in_solution_satisfies_workaround(self):
        result = audit_incident(self._incident("создана задача TEAMX-771"))

        required = check_by_id(result, "REMEDIATION_REQUIRED")
        workaround = check_by_id(result, "WORKAROUND_DETAIL")
        self.assertIsNotNone(required)
        self.assertIsNotNone(workaround)
        self.assertEqual(required["status"], "passed")
        self.assertEqual(workaround["status"], "passed")
        self.assertEqual(workaround["severity"], "none")

    def test_task_without_number_does_not_satisfy_workaround(self):
        result = audit_incident(self._incident("создана задача"))

        self.assertEqual(
            check_by_id(result, "WORKAROUND_DETAIL")["severity"],
            "warning",
        )


class ChronologySeverityTests(TestCase):
    def test_missing_chronology_heading_is_warning(self):
        incident = WorkaroundTaskRuleTests()._incident(
            "создана задача TEAMX-771"
        )
        incident["Код закрытия"] = "Выполнено"
        incident["Решение"] = incident["Решение"].replace(
            "Краткая хронология:\n",
            "",
        )

        result = audit_incident(incident)
        check = check_by_id(result, "CHRONOLOGY_REQUIRED")

        self.assertIsNotNone(check)
        self.assertEqual(check["status"], "remark")
        self.assertEqual(check["severity"], "warning")
