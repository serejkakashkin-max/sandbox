from __future__ import annotations

import os

from werkzeug.serving import run_simple

from wsgi import application


def main() -> None:
    host = os.getenv("SANDBOX_HOST", "127.0.0.1")
    port = int(os.getenv("SANDBOX_PORT", "3535"))
    run_simple(host, port, application, use_debugger=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
