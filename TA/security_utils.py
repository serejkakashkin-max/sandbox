"""Small security primitives used by the local Flask application."""

from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
import secrets
import tempfile
from typing import Any
from zipfile import BadZipFile, ZipFile, is_zipfile


def load_or_create_secret_key(
    instance_directory: Path,
    configured_secret: str | None = None,
) -> str:
    """Return the configured secret or a stable secret stored in the instance folder."""
    if configured_secret:
        return configured_secret

    instance_directory = Path(instance_directory)
    instance_directory.mkdir(parents=True, exist_ok=True)
    secret_path = instance_directory / "secret_key"
    try:
        saved = secret_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        saved = ""
    if saved:
        return saved

    generated = secrets.token_hex(32)
    try:
        with secret_path.open("x", encoding="utf-8") as secret_file:
            secret_file.write(generated)
    except FileExistsError:
        saved = secret_path.read_text(encoding="utf-8").strip()
        if saved:
            return saved
        raise RuntimeError("Файл локального secret key пуст")
    return generated


def inspect_xlsx_archive(
    payload: bytes,
    *,
    max_members: int = 2_000,
    max_uncompressed_bytes: int = 100 * 1024 * 1024,
) -> dict[str, int]:
    """Reject malformed or unexpectedly large XLSX ZIP containers before parsing."""
    stream = BytesIO(payload)
    if not payload or not is_zipfile(stream):
        raise ValueError("Файл не является корректным XLSX-архивом")
    stream.seek(0)
    try:
        with ZipFile(stream) as archive:
            members = archive.infolist()
            if len(members) > max_members:
                raise ValueError(
                    f"XLSX содержит слишком много файлов: {len(members)}"
                )
            if any(member.flag_bits & 0x1 for member in members):
                raise ValueError("Зашифрованные XLSX-файлы не поддерживаются")
            uncompressed_bytes = sum(member.file_size for member in members)
            if uncompressed_bytes > max_uncompressed_bytes:
                raise ValueError(
                    "Распакованный размер XLSX превышает допустимый лимит"
                )
    except BadZipFile as exc:
        raise ValueError("Файл не является корректным XLSX-архивом") from exc
    return {
        "members": len(members),
        "uncompressed_bytes": uncompressed_bytes,
    }


def atomic_write_json(path: Path, value: Any) -> None:
    """Serialize JSON beside its destination and atomically replace the old file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise



def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text atomically beside its destination."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

def neutralize_spreadsheet_value(value: Any) -> Any:
    """Force formula-looking strings to remain text when exported to a workbook."""
    if not isinstance(value, str) or not value:
        return value
    stripped = value.lstrip()
    if value[0] in "\t\r\n" or (stripped and stripped[0] in "=+-@"):
        return "'" + value
    return value
