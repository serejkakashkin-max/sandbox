from datetime import datetime
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from TA import app as app_module


class DashboardRouteTests(TestCase):
    def setUp(self):
        self.previous = list(app_module.incidents)
        self.previous_upload = dict(app_module.upload_state)
        app_module.app.config.update(TESTING=True)

    def tearDown(self):
        app_module.incidents[:] = self.previous
        app_module.upload_state.clear()
        app_module.upload_state.update(self.previous_upload)

    @staticmethod
    def incident(incident_id, executor, created):
        return {
            "ID инцидента": incident_id,
            "Статус": "Закрыт",
            "Код закрытия": "Выполнено",
            "Тип стенда": "ПРОМ",
            "Фактическое время возникновения": created,
            "Создан": created,
            "Фактическое время окончания": created,
            "Влияние на клиентский сервисе": "Нет",
            "Причина": "Техническая причина",
            "Тема инцидента": "Проверка",
            "Описание": "Кратковременная недоступность сервиса",
            "Исполнитель": executor,
            "Решение": (
                "Время начала инцидента: 10:00 28.08.2026\n"
                "Время окончания инцидента: 10:10 28.08.2026\n"
                "Причина: ошибка конфигурации\n"
                "Влияние отсутствует\n"
                "Краткая хронология:\n"
                "10:00 — обнаружено\n10:10 — восстановлено"
            ),
        }

    def test_global_search_returns_match_from_old_period(self):
        app_module.incidents[:] = [
            self.incident("INC001", "Иванов И.И.", "28.08.2026 08:00"),
            self.incident("INC002", "Петров П.П.", "10.07.2026 08:00"),
        ]
        app_module.upload_state.update(
            {
                "filename": "test.xlsx",
                "loaded_at": datetime(
                    2026, 8, 28, 9, 0, tzinfo=ZoneInfo("Europe/Moscow")
                ),
            }
        )
        with patch.object(app_module, "_list_protocol_files", return_value=[]), patch.object(
            app_module, "get_history_for_protocols", return_value={}
        ):
            response = app_module.app.test_client().get("/?search=Петров")

        self.assertEqual(response.status_code, 200)
        self.assertIn("INC002", response.get_data(as_text=True))
