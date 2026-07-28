import json
import os
import threading
from datetime import datetime
from pathlib import Path
import requests

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
PRIMARY_SNAPSHOT_PATH = CACHE_DIR / "release_monitor_snapshot.json"
LAST_GOOD_SNAPSHOT_PATH = CACHE_DIR / "release_monitor_snapshot.last_good.json"
DEBUG_FILE_PATH = CACHE_DIR / "release_monitor_debug.txt"
_REFRESH_LOCK = threading.Lock()

# ИСПРАВЛЕНО: убран двойной https://
SOURCE_URL = os.getenv(
    "GD_RELEASE_MONITOR_SOURCE_URL",
    "https://oplot.sberbank.ru/releases/dashboard/release-monitor/status",
)

def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _write_debug(message):
    _ensure_cache_dir()
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    with DEBUG_FILE_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

def _atomic_write_json(path, data):
    _ensure_cache_dir()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    tmp_path.replace(path)

def fetch_snapshot_from_source():
    # Добавлен параметр verify=False, так как это внутренний корпоративный ресурс
    response = requests.get(SOURCE_URL, timeout=60, verify=False)
    response.raise_for_status()
    return response.json()

def _extract_records_count(snapshot_data):
    if isinstance(snapshot_data, list):
        return len(snapshot_data)
    if isinstance(snapshot_data, dict):
        for field in (
            "release_monitor",
            "items",
            "records",
            "data",
            "releases",
            "rows",
            "result",
            "release_items",
        ):
            value = snapshot_data.get(field)
            if isinstance(value, list):
                return len(value)
    return 0


def _validate_source_snapshot(snapshot_data):
    if not isinstance(snapshot_data, dict):
        raise ValueError("Источник вернул JSON неподдерживаемого формата")
    if snapshot_data.get("success") is not True:
        raise ValueError("Источник вернул success=false")
    records = snapshot_data.get("release_monitor")
    if not isinstance(records, list) or not records:
        raise ValueError("Источник вернул пустой release_monitor")
    if not all(isinstance(item, dict) for item in records):
        raise ValueError("release_monitor содержит записи неподдерживаемого формата")


def refresh_snapshot_from_source():
    with _REFRESH_LOCK:
        _ensure_cache_dir()
        _write_debug("refresh started")

        try:
            snapshot_data = fetch_snapshot_from_source()
            _validate_source_snapshot(snapshot_data)
            records_count = _extract_records_count(snapshot_data)

            _atomic_write_json(LAST_GOOD_SNAPSHOT_PATH, snapshot_data)
            _atomic_write_json(PRIMARY_SNAPSHOT_PATH, snapshot_data)
            _write_debug(
                f"snapshot updated successfully, records_count={records_count}"
            )

            return {
                "success": True,
                "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                "source_url": SOURCE_URL,
                "primary_snapshot_path": str(PRIMARY_SNAPSHOT_PATH),
                "last_good_snapshot_path": str(LAST_GOOD_SNAPSHOT_PATH),
                "records_count": records_count,
            }
        except Exception as exc:
            _write_debug(f"refresh failed: {exc}")
            return {
                "success": False,
                "error": str(exc),
                "source_url": SOURCE_URL,
                "primary_snapshot_path": str(PRIMARY_SNAPSHOT_PATH),
                "last_good_snapshot_path": str(LAST_GOOD_SNAPSHOT_PATH),
            }

if __name__ == "__main__":
    import urllib3
    # Отключаем предупреждения о SSL (полезно для внутрикорпоративных сетей)
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    result = refresh_snapshot_from_source()
    print(json.dumps(result, ensure_ascii=False, indent=2))

    
