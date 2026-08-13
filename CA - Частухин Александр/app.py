from __future__ import annotations

import os

from waitress import serve

from zpi_app import create_app


application = create_app()


def main() -> None:
    host = os.getenv("ZPI_HOST", "127.0.0.1")
    port = int(os.getenv("ZPI_PORT", "5055"))
    serve(application, host=host, port=port, threads=4)


if __name__ == "__main__":
    main()

