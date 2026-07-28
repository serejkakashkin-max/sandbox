import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
PRIMARY_SNAPSHOT_PATH = CACHE_DIR / "release_monitor_snapshot.json"
LAST_GOOD_SNAPSHOT_PATH = CACHE_DIR / "release_monitor_snapshot.last_good.json"
DEBUG_FILE_PATH = CACHE_DIR / "release_monitor_debug.txt"


def _load_json_file(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _file_mtime_str(path):
    if not path or not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M:%S")


def _parse_date(value):
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        if "T" in text:
            text_for_iso = text.replace("Z", "+00:00")
            return datetime.fromisoformat(text_for_iso)
    except Exception:
        pass

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y",
        "%d.%m.%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def _pick_first(record, fields):
    for field in fields:
        value = record.get(field)

        if isinstance(value, list):
            for item in value:
                if item not in (None, ""):
                    item_text = str(item).strip()
                    if item_text:
                        return item_text

        elif value not in (None, ""):
            value_text = str(value).strip()
            if value_text:
                return value_text

    return ""


def _extract_raw_records(snapshot_data):
    if not snapshot_data:
        return []

    if isinstance(snapshot_data, list):
        return [item for item in snapshot_data if isinstance(item, dict)]

    if not isinstance(snapshot_data, dict):
        return []

    possible_list_fields = [
        "release_monitor",
        "items",
        "records",
        "data",
        "releases",
        "rows",
        "result",
        "release_items",
    ]

    for field in possible_list_fields:
        value = snapshot_data.get(field)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    for value in snapshot_data.values():
        if isinstance(value, list) and all(isinstance(x, dict) for x in value):
            return value

    return []


def _pick_release_date(record):
    candidate_fields = [
        "source_deployment_start_iso",
        "deployment_start_iso",
        "source_deployment_end_iso",
        "deployment_end_iso",
        "sort_date",
        "created_sort_date",
        "created",
        "updated_at",
        "release_date",
        "date_start",
        "start_date",
        "planned_date",
        "production_date",
        "actual_date",
        "release_start_date",
        "release_end_date",
        "planned_release_date",
        "manual_release_date",
        "manual_release_start_date",
        "manual_release_end_date",
        "planned_release_start_date",
        "planned_release_end_date",
        "source_deployment_start",
        "source_deployment_end",
        "deployment_start",
        "deployment_end",
        "date",
        "month",
    ]

    for field in candidate_fields:
        value = record.get(field)
        if value in (None, ""):
            continue

        parsed_dt = _parse_date(value)
        if parsed_dt:
            return str(value), parsed_dt

    for key, value in record.items():
        if value in (None, ""):
            continue

        key_lower = str(key).lower()
        if (
            "date" in key_lower
            or "dt" in key_lower
            or "start" in key_lower
            or "end" in key_lower
            or "deployment" in key_lower
            or "month" in key_lower
            or "created" in key_lower
        ):
            parsed_dt = _parse_date(value)
            if parsed_dt:
                return str(value), parsed_dt

    return "", None


def load_snapshot():
    errors = []

    if PRIMARY_SNAPSHOT_PATH.exists():
        try:
            data = _load_json_file(PRIMARY_SNAPSHOT_PATH)
            if not _extract_raw_records(data):
                raise ValueError("primary snapshot has no release records")
            if isinstance(data, dict):
                data["_loaded_from"] = str(PRIMARY_SNAPSHOT_PATH)
                data["_loaded_source_name"] = "primary"
            return data
        except Exception as exc:
            errors.append(f"primary_error: {exc}")

    if LAST_GOOD_SNAPSHOT_PATH.exists():
        try:
            data = _load_json_file(LAST_GOOD_SNAPSHOT_PATH)
            if not _extract_raw_records(data):
                raise ValueError("last_good snapshot has no release records")
            if isinstance(data, dict):
                data["_loaded_from"] = str(LAST_GOOD_SNAPSHOT_PATH)
                data["_loaded_source_name"] = "last_good"
                data["_load_errors"] = errors
            return data
        except Exception as exc:
            errors.append(f"last_good_error: {exc}")

    return {
        "_loaded_from": "",
        "_loaded_source_name": "none",
        "_load_errors": errors,
    }


def generate_jira_link(key):
    """Генерирует ссылку на Jira на основе префикса ключа."""
    if not key or key == "-":
        return ""
        
    key_upper = str(key).upper()
    
    # 1. Смежники на delta.sbrf.ru
    if key_upper.startswith(("SMECSC", "AIGAS", "HELPERAI")):
        domain = "https://jira.delta.sbrf.ru"
        return f"{domain}/browse/{key}"
        
    # 2. Смежники на jira.sberbank.ru
    elif key_upper.startswith(("EMRM", "SMECLM")):
        domain = "https://jira.sberbank.ru"
        return f"{domain}/browse/{key}"
        
    # Базовый фоллбэк
    return f"https://jira.sberbank.ru/browse/{key}"


def normalize_record(record):
    release_date_raw, release_dt = _pick_release_date(record)

    release_name = _pick_first(
        record,
        (
            "release_name",
            "release_name_line",
            "release_summary",
            "release_key",
            "base_release_key",
            "name",
            "title",
            "release_title",
        ),
    )

    project = _pick_first(
        record,
        (
            "project",
            "project_name",
            "system_name",
            "base_system_name",
            "manual_system_name",
            "system",
            "product",
        ),
    )

    source = _pick_first(
        record,
        (
            "source",
            "source_name",
            "source_pretty",
            "source_prefix",
            "origin",
            "channel",
        ),
    )

    release_id = _pick_first(
        record,
        (
            "release_id",
            "id_release",
            "release_key",
            "base_release_key",
            "key_id",
            "base_key",
        ),
    )

    version = _pick_first(
        record,
        (
            "release_version",
            "version",
            "manual_release_version",
        ),
    )

    rov_key = _pick_first(
        record,
        (
            "base_rov_key",
            "manual_rov_key",
            "rov_id",
            "id_rov",
            "rov_key",
        ),
    )

    release_status = _pick_first(
        record,
        (
            "release_status_normalized",
            "base_release_status",
            "release_status",
            "manual_release_status",
            "row_state",
            "status_release",
            "status",
        ),
    )

    rov_status = _pick_first(
        record,
        (
            "base_rov_status",
            "rov_status",
            "manual_rov_status",
            "status_rov",
        ),
    )

    owner = _pick_first(
        record,
        (
            "psi_responsible",
            "psi_responsibles",
            "responsible",
            "responsible_fio",
            "manual_responsible",
            "responsible_name",
            "owner",
            "owner_fio",
            "assignee",
            "release_owner",
            "business_owner",
            "manual_owner",
            "psi_owner",
        ),
    )

    if not owner:
        for key, value in record.items():
            if value in (None, ""):
                continue

            key_lower = str(key).lower()
            if "responsible" in key_lower or "owner" in key_lower:
                owner_candidate = value
                if isinstance(owner_candidate, list):
                    owner_candidate = ", ".join(str(v) for v in owner_candidate if v)
                owner_candidate = str(owner_candidate).strip()
                if owner_candidate:
                    owner = owner_candidate
                    break

    start_date = _pick_first(
        record,
        (
            "source_deployment_start",
            "source_deployment_start_iso",
            "deployment_start",
            "deployment_start_iso",
            "date_start",
            "start_date",
            "release_start_date",
            "manual_release_start_date",
            "planned_release_start_date",
            "release_date",
        ),
    )

    end_date = _pick_first(
        record,
        (
            "source_deployment_end",
            "source_deployment_end_iso",
            "deployment_end",
            "deployment_end_iso",
            "date_end",
            "end_date",
            "finish_date",
            "release_end_date",
            "manual_release_end_date",
            "planned_release_end_date",
        ),
    )

    release_date_display = start_date or release_date_raw or end_date

    return {
        "release_name": release_name,
        "project": project,
        "source": source,
        "release_id": release_id,
        "release_id_link": generate_jira_link(release_id),  # ДОБАВЛЕНО: Ссылка на Jira
        "version": version,
        "base_rov_key": rov_key,
        "rov_link": generate_jira_link(rov_key),            # ДОБАВЛЕНО: Ссылка на Jira
        "release_status": release_status,
        "status": release_status,
        "rov_status": rov_status,
        "owner": owner,
        "start_date": start_date,
        "end_date": end_date,
        "release_date": release_date_raw,
        "release_date_display": release_date_display,
        "release_dt": release_dt,
        "year": release_dt.year if release_dt else None,
        "month_key": release_dt.strftime("%Y-%m") if release_dt else "",
        "raw": record,
    }


def get_release_records(snapshot_data=None):
    if snapshot_data is None:
        snapshot_data = load_snapshot()

    raw_records = _extract_raw_records(snapshot_data)
    normalized = [normalize_record(item) for item in raw_records]

    normalized.sort(
        key=lambda r: (
            r["release_dt"] is None,
            r["release_dt"] or datetime.min,
            r["release_name"],
        ),
        reverse=True,
    )

    return normalized


# ОБНОВЛЕНИЕ: добавлен параметр month=None
def filter_records(records, query=None, year=None, project=None, responsible=None, month=None):
    result = records or []

    if query:
        q = query.strip().lower()
        result = [
            r
            for r in result
            if (
                q in str(r.get("release_name", "")).lower()
                or q in str(r.get("project", "")).lower()
                or q in str(r.get("source", "")).lower()
                or q in str(r.get("release_id", "")).lower()
                or q in str(r.get("base_rov_key", "")).lower()
                or q in str(r.get("owner", "")).lower()
            )
        ]

    if year:
        result = [r for r in result if r.get("year") == year]

    if project:
        project_value = str(project).strip()
        result = [
            r for r in result
            if str(r.get("project", "")).strip() == project_value
        ]

    if responsible:
        responsible_value = str(responsible).strip()
        result = [
            r for r in result
            if str(r.get("owner", "")).strip() == responsible_value
        ]

    # НОВЫЙ ФИЛЬТР: по месяцу (формат YYYY-MM)
    if month:
        month_value = str(month).strip()
        result = [
            r for r in result
            if str(r.get("month_key", "")).strip() == month_value
        ]

    result = sorted(
        result,
        key=lambda r: (
            r.get("release_dt") is None,
            r.get("release_dt") or datetime.min,
            r.get("release_name", ""),
        ),
        reverse=True,
    )

    return result


def build_monthly_release_series(records, start="2025-01-01"):
    start_dt = _parse_date(start)
    month_counter = Counter()

    for record in records or []:
        dt = record.get("release_dt")
        if not dt:
            continue

        if start_dt and dt < start_dt:
            continue

        month_key = dt.strftime("%Y-%m")
        month_counter[month_key] += 1

    if not month_counter:
        return {"labels": [], "values": []}

    labels = sorted(month_counter.keys())
    values = [month_counter.get(label, 0) for label in labels]

    return {
        "labels": labels,
        "values": values,
    }


def get_cache_info(snapshot_data=None):
    if snapshot_data is None:
        snapshot_data = load_snapshot()

    loaded_from = snapshot_data.get("_loaded_from", "")
    source_name = snapshot_data.get("_loaded_source_name", "none")
    file_path = Path(loaded_from) if loaded_from else None
    records_count = len(_extract_raw_records(snapshot_data))

    return {
        "source_name": source_name,
        "loaded_from": loaded_from,
        "updated_at": _file_mtime_str(file_path),
        "primary_exists": PRIMARY_SNAPSHOT_PATH.exists(),
        "last_good_exists": LAST_GOOD_SNAPSHOT_PATH.exists(),
        "debug_exists": DEBUG_FILE_PATH.exists(),
        "records_count": records_count,
        "load_errors": snapshot_data.get("_load_errors", []),
    }


def build_summary_metrics(records):
    total_releases = len(records or [])

    total_unique_rov = len(
        {r.get("base_rov_key") for r in (records or []) if r.get("base_rov_key")}
    )

    overdue_count = sum(
        1
        for r in (records or [])
        if "просроч" in str(r.get("release_status", "")).lower()
    )

    non_final_count = sum(
        1
        for r in (records or [])
        if str(r.get("release_status", "")).strip()
        and str(r.get("release_status", "")).strip().lower() not in {
            "final",
            "finalized",
            "completed",
            "завершен",
            "завершено",
            "финальный",
        }
    )

    prod_count = sum(
        1
        for r in (records or [])
        if "пром" in str(r.get("release_status", "")).lower()
        or "prod" in str(r.get("release_status", "")).lower()
    )

    total_records_2026 = sum(1 for r in (records or []) if r.get("year") == 2026)
    total_projects = len({r.get("project") for r in (records or []) if r.get("project")})
    total_owners = len({r.get("owner") for r in (records or []) if r.get("owner")})

    return {
        "total_releases": total_releases,
        "total_records": total_releases,
        "records_2026": total_records_2026,
        "total_projects": total_projects,
        "total_owners": total_owners,
        "total_unique_rov": total_unique_rov,
        "overdue_count": overdue_count,
        "non_final_count": non_final_count,
        "prod_count": prod_count,
    }


MONTHS_2026 = [f"2026-{month:02d}" for month in range(1, 13)]


def build_pivot_table_2026(records, row_field):
    pivot = defaultdict(lambda: {month: 0 for month in MONTHS_2026})
    last_dt_by_row = {}

    for record in records or []:
        if record.get("year") != 2026:
            continue

        month_key = record.get("month_key", "")
        if month_key not in MONTHS_2026:
            continue

        row_name = str(record.get(row_field) or "").strip()
        if not row_name:
            row_name = "Не указан"

        pivot[row_name][month_key] += 1

        current_dt = record.get("release_dt")
        stored_dt = last_dt_by_row.get(row_name)
        if current_dt and (stored_dt is None or current_dt > stored_dt):
            last_dt_by_row[row_name] = current_dt

    sorted_rows = sorted(
        pivot.keys(),
        key=lambda name: (
            last_dt_by_row.get(name) is None,
            last_dt_by_row.get(name) or datetime.min,
            name.lower(),
        ),
        reverse=True,
    )

    result = []
    for row_name in sorted_rows:
        months = [pivot[row_name][month] for month in MONTHS_2026]
        result.append(
            {
                "name": row_name,
                "months": months,
                "total": sum(months),
            }
        )

    return result


def build_dashboard_pivots_2026(records):
    return {
        "months_2026": MONTHS_2026,
        "owners_pivot_2026": build_pivot_table_2026(records, "owner"),
        "projects_pivot_2026": build_pivot_table_2026(records, "project"),
    }
