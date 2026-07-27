from __future__ import annotations

import importlib.util
from pathlib import Path

from werkzeug.middleware.dispatcher import DispatcherMiddleware

from sandbox_app import PublicPrefixMiddleware, create_app


BASE_DIR = Path(__file__).resolve().parent
TA_APP_PATH = BASE_DIR / "TA" / "app.py"


def _load_ta_app_module():
    spec = importlib.util.spec_from_file_location("ta_incident_auditor_app", TA_APP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load TA Flask application from {TA_APP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ta_app_module = _load_ta_app_module()
ta_application = ta_app_module.app
root_application = create_app()

application = PublicPrefixMiddleware(
    DispatcherMiddleware(
        root_application,
        {"/ta/incident-auditor": ta_application},
    )
)
