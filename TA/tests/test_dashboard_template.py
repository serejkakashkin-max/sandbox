from datetime import datetime
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from TA import app as app_module


class DashboardTemplateTests(TestCase):
    @staticmethod
    def incident():
        return {
            "ID инцидента": "INC001",
            "Статус": "Закрыт",
            "Код закрытия": "Выполнено",
            "Тип стенда": "ПРОМ",
            "Фактическое время возникновения": "28.08.2026 08:00",
            "Создан": "28.08.2026 08:00",
            "Фактическое время окончания": "28.08.2026 08:00",
            "Влияние на клиентский сервисе": "Нет",
            "Причина": "Техническая причина",
            "Тема инцидента": "Проверка",
            "Описание": "Кратковременная недоступность сервиса",
            "Исполнитель": "Иванов И.И.",
            "Решение": (
                "Время начала инцидента: 10:00 28.08.2026\n"
                "Время окончания инцидента: 10:10 28.08.2026\n"
                "Причина: ошибка конфигурации\n"
                "Влияние отсутствует\n"
                "Краткая хронология:\n"
                "10:00 — обнаружено\n10:10 — восстановлено"
            ),
        }

    def setUp(self):
        self.previous = list(app_module.incidents)
        self.previous_upload = dict(app_module.upload_state)
        app_module.app.config.update(TESTING=True)
        app_module.incidents[:] = [self.incident()]
        app_module.upload_state.update(
            {
                "filename": "test.xlsx",
                "loaded_at": datetime(
                    2026, 8, 28, 9, 0, tzinfo=ZoneInfo("Europe/Moscow")
                ),
            }
        )

    def tearDown(self):
        app_module.incidents[:] = self.previous
        app_module.upload_state.clear()
        app_module.upload_state.update(self.previous_upload)

    def get_dashboard_html(self):
        with patch.object(app_module, "_list_protocol_files", return_value=[]), patch.object(
            app_module, "get_history_for_protocols", return_value={}
        ):
            response = app_module.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_dashboard_has_short_status_labels_and_no_legacy_filter(self):
        html = self.get_dashboard_html()

        self.assertIn("На доработку", html)
        self.assertIn("Замечания", html)
        self.assertIn("Не проверяются", html)
        self.assertNotIn("Только проверявшиеся ранее", html)
        self.assertNotIn("tab=inwork", html)
        self.assertNotIn("Дата с", html)

    def test_ai_control_remains_explicit(self):
        html = self.get_dashboard_html()

        self.assertIn("ai-action", html)
        self.assertIn("data-ai-url", html)
        self.assertNotIn("data-ai-autostart", html)

    def test_dashboard_loads_progressive_enhancement_script(self):
        html = self.get_dashboard_html()

        self.assertIn("js/dashboard.js", html)
        self.assertIn("data-period-navigation", html)
        self.assertIn("data-incident-row", html)
