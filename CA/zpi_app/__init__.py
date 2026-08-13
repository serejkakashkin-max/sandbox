from __future__ import annotations

import os
import secrets

from flask import Flask


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("ZPI_SECRET_KEY") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=_positive_int("ZPI_MAX_UPLOAD_BYTES", 60 * 1024 * 1024),
        MAX_FILE_BYTES=_positive_int("ZPI_MAX_FILE_BYTES", 30 * 1024 * 1024),
        MAX_FILES=_positive_int("ZPI_MAX_FILES", 10),
        MAX_XLSX_MEMBERS=_positive_int("ZPI_MAX_XLSX_MEMBERS", 2_000),
        MAX_XLSX_UNCOMPRESSED_BYTES=_positive_int(
            "ZPI_MAX_XLSX_UNCOMPRESSED_BYTES", 150 * 1024 * 1024
        ),
        MAX_XLSX_ROWS=_positive_int("ZPI_MAX_XLSX_ROWS", 200_000),
        MAX_XLSX_COLUMNS=_positive_int("ZPI_MAX_XLSX_COLUMNS", 250),
        SESSION_COOKIE_NAME="zpi_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("ZPI_HTTPS", "").casefold()
        in {"1", "true", "yes", "on"},
    )
    if test_config:
        app.config.update(test_config)

    from .web import bp

    app.register_blueprint(bp)

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        return response

    return app
