from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from werkzeug.test import Client
from werkzeug.wrappers import Response


ROOT = Path(__file__).resolve().parents[1]


def _load_wsgi(monkeypatch, public_prefix: str = "", parent_url: str | None = None):
    if public_prefix:
        monkeypatch.setenv("SANDBOX_PUBLIC_PREFIX", public_prefix)
    else:
        monkeypatch.delenv("SANDBOX_PUBLIC_PREFIX", raising=False)

    if parent_url is None:
        monkeypatch.delenv("SANDBOX_PARENT_URL", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_PARENT_URL", parent_url)

    import wsgi

    module = importlib.reload(wsgi)
    module.ta_app_module.incidents = []
    return module


def _client(application) -> Client:
    return Client(application, Response)


def _sample_incident():
    return {
        "ID инцидента": "INC-1",
        "Исполнитель": "Иван Иванов",
        "Статус": "Закрыт",
        "Тип стенда": "",
        "Описание": "Проблема: высокая нагрузка CPU",
        "Решение": "Причина: высокая нагрузка CPU. Время начала 10:00. Время устранения 10:20.",
    }


def test_application_imports(monkeypatch):
    module = _load_wsgi(monkeypatch)
    assert module.application is not None


def test_index_returns_200_and_contains_sandbox_card(monkeypatch):
    module = _load_wsgi(monkeypatch)
    response = _client(module.application).get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Экспериментальные инструменты" in body
    assert "Аудитор инцидентов" in body


def test_health_returns_json(monkeypatch):
    module = _load_wsgi(monkeypatch)
    response = _client(module.application).get("/health")

    assert response.status_code == 200
    assert response.content_type.startswith("application/json")
    assert response.json["status"] == "ok"


def test_ta_index_returns_200(monkeypatch):
    module = _load_wsgi(monkeypatch)
    response = _client(module.application).get("/ta/incident-auditor/")

    assert response.status_code == 200


def test_ta_export_and_incident_links_are_under_ta_route(monkeypatch):
    module = _load_wsgi(monkeypatch)
    module.ta_app_module.incidents = [_sample_incident()]
    response = _client(module.application).get("/ta/incident-auditor/")
    body = response.get_data(as_text=True)

    assert 'href="/ta/incident-auditor/export"' in body
    assert 'window.location="/ta/incident-auditor/incident/INC-1?tab=correct"' in body


def test_public_prefix_is_applied_to_generated_links(monkeypatch):
    module = _load_wsgi(monkeypatch, "/releases/sandbox", "/releases/")
    client = _client(module.application)

    root_response = client.get("/")
    root_body = root_response.get_data(as_text=True)
    assert 'href="/releases/sandbox/ta/incident-auditor/"' in root_body
    assert 'href="/releases/"' in root_body

    module.ta_app_module.incidents = [_sample_incident()]
    ta_response = client.get("/ta/incident-auditor/")
    ta_body = ta_response.get_data(as_text=True)
    assert 'href="/releases/sandbox/ta/incident-auditor/export"' in ta_body
    assert 'window.location="/releases/sandbox/ta/incident-auditor/incident/INC-1?tab=correct"' in ta_body


def test_forwarded_prefix_header_is_applied(monkeypatch):
    module = _load_wsgi(monkeypatch)
    response = _client(module.application).get(
        "/",
        headers={"X-Forwarded-Prefix": "/releases/sandbox"},
    )
    body = response.get_data(as_text=True)

    assert 'href="/releases/sandbox/ta/incident-auditor/"' in body


def test_local_card_link_has_no_public_prefix(monkeypatch):
    module = _load_wsgi(monkeypatch)
    response = _client(module.application).get("/")
    body = response.get_data(as_text=True)

    assert 'href="/ta/incident-auditor/"' in body
    assert "/releases/sandbox/ta/incident-auditor/" not in body


def test_wsgi_import_and_routes_work_from_other_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.delenv("SANDBOX_PUBLIC_PREFIX", raising=False)
    monkeypatch.delenv("SANDBOX_PARENT_URL", raising=False)

    sys.modules.pop("wsgi", None)
    module = importlib.import_module("wsgi")
    module.ta_app_module.incidents = []

    client = _client(module.application)
    assert Path.cwd() == tmp_path
    assert client.get("/").status_code == 200
    assert client.get("/ta/incident-auditor/").status_code == 200


def test_no_environment_absolute_paths_in_python_and_html_sources():
    forbidden = [
        "C:" + "\\" + "sandbox",
        "C:" + "\\" + "release_web",
        "/" + "home" + "/efs_dev" + "/sandbox",
    ]
    checked_suffixes = {".py", ".html"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in checked_suffixes:
            continue
        if path.relative_to(ROOT).as_posix() == "deploy/sandbox.service":
            continue
        if any(part in {".venv", ".pytest_cache", "__pycache__"} for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in source


def test_gitignore_excludes_upload_excels():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "TA/uploads/" in gitignore
    assert "cache/*" in gitignore
    assert "!cache/.gitkeep" in gitignore


def test_ta_sources_do_not_keep_prefix_breaking_absolute_links():
    files = [
        ROOT / "TA" / "app.py",
        ROOT / "TA" / "templates" / "index.html",
        ROOT / "TA" / "templates" / "detail.html",
    ]
    forbidden = [
        'href="' + '/export"',
        "window.location='" + "/incident/",
        'href="' + "/?tab=",
        "redirect(" + "'/'" + ")",
    ]
    for path in files:
        source = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in source
