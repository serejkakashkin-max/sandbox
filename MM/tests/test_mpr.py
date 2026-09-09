from __future__ import annotations

from io import BytesIO
import unittest

from werkzeug.datastructures import FileStorage

from MM.app import create_app
from MM.mpr_service import build_mpr_package_preview, build_mpr_rows, list_mpr_templates


CSV = """Имя;Наименование услуги;Имя дата-центра ВМ;Имя AC;ID КЭ сервера;Платформа;Статус стенда\npslsb-app001;service_one;МегаЦОД;AC-1;1;linux;Работает\npslsb-app002;service_two;Сколково;AC-2;2;linux;Работает\npslsb-off001;service_off;МегаЦОД;AC-3;3;linux;Не работает\n"""


class MprSandboxTests(unittest.TestCase):
    def test_template_is_available(self):
        templates = list_mpr_templates()
        self.assertTrue(templates)
        self.assertEqual("os_update", templates[0]["code"])

    def test_csv_filtering_and_package_preview(self):
        upload = FileStorage(stream=BytesIO(CSV.encode("utf-8")), filename="limits.csv")
        rows = build_mpr_rows([upload])
        self.assertEqual(2, len(rows))
        preview = build_mpr_package_preview(rows)
        packages = {item["code"]: item for item in preview["packages"]}
        self.assertEqual(1, packages["mcod"]["rows_count"])
        self.assertEqual(1, packages["scod_vavilova"]["rows_count"])
        self.assertEqual([], preview["unmapped"])

    def test_page_is_prefix_safe_and_has_owner(self):
        app = create_app()
        app.config.update(TESTING=True)
        response = app.test_client().get(
            "/",
            environ_overrides={"SCRIPT_NAME": "/releases/sandbox/mm/mpr"},
        )
        self.assertEqual(200, response.status_code)
        text = response.get_data(as_text=True)
        self.assertIn("MM - Мухиддинов Ману", text)
        self.assertIn('href="/releases/sandbox/"', text)
        self.assertIn('"preview": "/releases/sandbox/mm/mpr/preview"', text)
        self.assertIn('"generate": "/releases/sandbox/mm/mpr/generate"', text)


if __name__ == "__main__":
    unittest.main()
