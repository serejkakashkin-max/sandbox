from __future__ import annotations

import secrets

from flask import Blueprint, current_app, render_template, request, session
from werkzeug.exceptions import RequestEntityTooLarge

from .analyzers import analyze_case
from .parsers import LogParseError, parse_uploads


bp = Blueprint("main", __name__)


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _valid_csrf() -> bool:
    expected = session.get("csrf_token", "")
    supplied = request.form.get("csrf_token", "")
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def _sandbox_home_url() -> str:
    script_root = (request.script_root or "").rstrip("/")
    mount_path = "/ca/zpi-assistant"
    if script_root.endswith(mount_path):
        parent = script_root[: -len(mount_path)]
        return f"{parent}/" if parent else "/"
    return "/"


@bp.app_context_processor
def inject_globals():
    return {
        "csrf_token": _csrf_token(),
        "sandbox_home_url": _sandbox_home_url(),
    }


@bp.get("/health")
def health():
    return {"status": "ok", "service": "zpi-log-assistant"}


@bp.route("/", methods=["GET", "POST"])
def index():
    context = {
        "analysis": None,
        "parse_result": None,
        "error": None,
        "form": {
            "meeting_id": "",
            "incident_number": "",
            "user_login": "",
            "reported_at": "",
        },
    }
    if request.method == "GET":
        return render_template("index.html", **context)

    context["form"] = {
        "meeting_id": request.form.get("meeting_id", "").strip(),
        "incident_number": request.form.get("incident_number", "").strip(),
        "user_login": request.form.get("user_login", "").strip(),
        "reported_at": request.form.get("reported_at", "").strip(),
    }
    if not _valid_csrf():
        context["error"] = "Сессия формы устарела. Обновите страницу и повторите загрузку."
        return render_template("index.html", **context), 400

    try:
        parse_result = parse_uploads(request.files.getlist("logs"), current_app.config)
        meeting_id = context["form"]["meeting_id"]
        analysis = analyze_case(
            parse_result.records,
            meeting_id,
        )
    except (LogParseError, ValueError) as exc:
        context["error"] = str(exc)
        return render_template("index.html", **context), 400

    context["parse_result"] = parse_result
    context["analysis"] = analysis
    return render_template("index.html", **context)


@bp.app_errorhandler(RequestEntityTooLarge)
def too_large(_error):
    context = {
        "analysis": None,
        "parse_result": None,
        "error": "Общий размер загрузки превышает допустимый лимит.",
        "form": {
            "meeting_id": "",
            "incident_number": "",
            "user_login": "",
            "reported_at": "",
        },
    }
    return render_template("index.html", **context), 413
