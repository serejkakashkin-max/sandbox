from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from werkzeug.middleware.dispatcher import DispatcherMiddleware

from GD.web_app import app as gd_application
from GD.web_app import start_scheduler as start_gd_scheduler
from sandbox_app import PublicPrefixMiddleware, create_app


BASE_DIR = Path(__file__).resolve().parent
TA_APP_PATH = BASE_DIR / "TA" / "app.py"
CA_PACKAGE_PATH = BASE_DIR / "CA - Частухин Александр" / "zpi_app"


def _load_ta_app_module():
    spec = importlib.util.spec_from_file_location("ta_incident_auditor_app", TA_APP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load TA Flask application from {TA_APP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_ca_app_package():
    package_name = "ca_zpi_app"
    package_init = CA_PACKAGE_PATH / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        package_name,
        package_init,
        submodule_search_locations=[str(CA_PACKAGE_PATH)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CA Flask application from {package_init}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module


ta_app_module = _load_ta_app_module()
ta_application = ta_app_module.app
ca_app_package = _load_ca_app_package()
ca_application = ca_app_package.create_app()
root_application = create_app()
start_gd_scheduler()

application = PublicPrefixMiddleware(
    DispatcherMiddleware(
        root_application,
        {
            "/ta/incident-auditor": ta_application,
            "/gd/release-monitor": gd_application,
            "/ca/zpi-assistant": ca_application,
        },
    )
)
