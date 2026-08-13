from __future__ import annotations

import importlib
from pathlib import Path

from werkzeug.test import Client
from werkzeug.wrappers import Response


REQUIRED_IMPORTS = (
    "apscheduler",
    "flask",
    "gigachat",
    "httpcore",
    "httpx",
    "openpyxl",
    "pandas",
    "pymorphy3",
    "requests",
    "spellchecker",
    "urllib3",
    "waitress",
)

REQUIRED_FILES = (
    "TA/templates/base.html",
    "TA/static/js/ai-analysis.js",
    "GD/templates/main_app.html",
    "CA - Частухин Александр/zpi_app/templates/base.html",
)

SMOKE_ROUTES = (
    "/",
    "/health",
    "/ta/incident-auditor/",
    "/ta/incident-auditor/static/js/ai-analysis.js",
    "/gd/release-monitor/health",
    "/ca/zpi-assistant/",
    "/ca/zpi-assistant/health",
)


def main() -> None:
    for module_name in REQUIRED_IMPORTS:
        importlib.import_module(module_name)

    missing_files = [path for path in REQUIRED_FILES if not Path(path).is_file()]
    if missing_files:
        raise SystemExit(f"Missing required Sandbox files: {missing_files}")

    from wsgi import application

    client = Client(application, Response)
    failures: list[str] = []
    for path in SMOKE_ROUTES:
        response = client.get(path)
        if response.status_code != 200:
            failures.append(f"{path}: HTTP {response.status_code}")

    if failures:
        raise SystemExit("Sandbox smoke check failed: " + "; ".join(failures))

    print("Sandbox smoke check passed for root, TA, GD and CA.")


if __name__ == "__main__":
    main()
