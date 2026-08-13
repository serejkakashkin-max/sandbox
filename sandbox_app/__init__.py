from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify, render_template, request


def _normalize_path(value: str, *, default: str = "") -> str:
    path = (value or default).strip()
    if not path:
        return ""
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def _join_public_path(*parts: str) -> str:
    joined = "/".join(part.strip("/") for part in parts if part)
    return f"/{joined}/" if joined else "/"


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def sandbox_index() -> str:
        ta_module_url = _join_public_path(request.script_root, "/ta/incident-auditor")
        gd_module_url = _join_public_path(request.script_root, "/gd/release-monitor")
        ca_module_url = _join_public_path(request.script_root, "/ca/zpi-assistant")
        modules: list[dict[str, Any]] = [
            {
                "owner_code": "TA",
                "owner_name": "Тутов Артём",
                "title": "Аудитор инцидентов",
                "description": "Экспериментальный инструмент анализа инцидентов",
                "status": "EXPERIMENTAL",
                "url": ta_module_url,
            },
            {
                "owner_code": "GD",
                "owner_name": "Гапоненко Дмитрий",
                "title": "Монитор релизов",
                "description": "Аналитический мониторинг и статистика релизов",
                "status": "EXPERIMENTAL",
                "url": gd_module_url,
            },
            {
                "owner_code": "CA",
                "owner_name": "Частухин Александр",
                "title": "Помощник ЗПИ",
                "description": "Сценарный разбор пользовательских логов и подготовка заявки на смежную систему",
                "status": "EXPERIMENTAL",
                "url": ca_module_url,
            },
        ]
        parent_url = os.getenv("SANDBOX_PARENT_URL", "/").strip() or "/"
        return render_template(
            "sandbox.html",
            modules=modules,
            parent_url=parent_url,
        )

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "service": "sandbox"})

    return app


class PublicPrefixMiddleware:
    """Adds SCRIPT_NAME for deployments where the proxy strips a public prefix."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX") or os.getenv("SANDBOX_PUBLIC_PREFIX", "")
        prefix = _normalize_path(prefix)
        if prefix:
            script_name = _normalize_path(environ.get("SCRIPT_NAME", ""))
            if script_name == prefix or script_name.startswith(f"{prefix}/"):
                environ["SCRIPT_NAME"] = script_name
            else:
                environ["SCRIPT_NAME"] = f"{prefix}{script_name}"
        return self.app(environ, start_response)
