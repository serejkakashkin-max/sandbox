from flask import Flask, abort, jsonify, render_template, request, redirect, url_for, send_file, session
import pandas as pd
import re
import os
import json
import secrets
import time
import uuid
from collections import Counter
from functools import wraps
from io import BytesIO
from datetime import datetime
from pathlib import Path
import tempfile
import threading
from werkzeug.utils import secure_filename
from TA.audit_engine import (
    audit_incident as run_incident_audit,
    classify_incident,
    is_test_incident as engine_is_test_incident,
    normalize_text,
)
from TA.db import (
    init_db,
    save_history_entries,
    get_history_for_incident,
    get_all_history_ids,
    get_history_grouped_by_date,
    claim_ai_analysis,
    complete_ai_analysis,
    fail_ai_analysis,
    get_ai_analysis,
    get_ai_states,
)
from TA.ai_analysis import (
    AIReportFormatError,
    PROMPT_VERSION,
    build_incident_payload,
    build_prompt,
    incident_content_hash,
    parse_ai_report,
)
from TA.gigachat_helper import (
    GigaChatConfig,
    GigaChatConfigurationError,
    GigaChatHelper,
    GigaChatRequestError,
)
from TA.security_utils import (
    atomic_write_json,
    atomic_write_text,
    inspect_xlsx_archive,
    load_or_create_secret_key,
    neutralize_spreadsheet_value,
)

TA_DIR = Path(__file__).resolve().parent
BASE_DIR = TA_DIR.parent
RUNTIME_DIR = BASE_DIR / "cache" / "ta_incident_auditor"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
PROTOCOLS_PATH = RUNTIME_DIR / "protocols"
MEMORY_PATH = RUNTIME_DIR / "memory.json"
INSTANCE_DIR = RUNTIME_DIR / "instance"

for runtime_path in (UPLOAD_DIR, PROTOCOLS_PATH, INSTANCE_DIR):
    runtime_path.mkdir(parents=True, exist_ok=True)

PROJECT_ROOT = TA_DIR

def _positive_env_int(name, default):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _trusted_hosts_from_env():
    raw = os.environ.get("INCIDENT_MANAGER_TRUSTED_HOSTS", "").strip()
    if not raw:
        return None
    hosts = [host.strip() for host in raw.split(",") if host.strip()]
    return hosts or None


app = Flask(
    __name__,
    template_folder=str(TA_DIR / "templates"),
    static_folder=str(TA_DIR / "static"),
    instance_path=str(INSTANCE_DIR),
    instance_relative_config=True,
)
app.secret_key = load_or_create_secret_key(
    Path(app.instance_path),
    os.environ.get("INCIDENT_MANAGER_SECRET_KEY"),
)
app.config.update(
    UPLOAD_FOLDER=str(UPLOAD_DIR),
    MAX_CONTENT_LENGTH=_positive_env_int(
        "INCIDENT_MANAGER_MAX_UPLOAD_BYTES", 20 * 1024 * 1024
    ),
    MAX_XLSX_ARCHIVE_MEMBERS=_positive_env_int(
        "INCIDENT_MANAGER_MAX_XLSX_MEMBERS", 2_000
    ),
    MAX_XLSX_UNCOMPRESSED_BYTES=_positive_env_int(
        "INCIDENT_MANAGER_MAX_XLSX_UNCOMPRESSED_BYTES", 100 * 1024 * 1024
    ),
    MAX_XLSX_ROWS=_positive_env_int("INCIDENT_MANAGER_MAX_XLSX_ROWS", 10_000),
    MAX_XLSX_COLUMNS=_positive_env_int("INCIDENT_MANAGER_MAX_XLSX_COLUMNS", 200),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("INCIDENT_MANAGER_HTTPS", "").casefold()
    in {"1", "true", "yes"},
)
_trusted_hosts = _trusted_hosts_from_env()
if _trusted_hosts:
    app.config["TRUSTED_HOSTS"] = _trusted_hosts
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
init_db()

incidents = []
MEMORY_FILE = str(MEMORY_PATH)
PROTOCOLS_DIR = str(PROTOCOLS_PATH)
_state_lock = threading.RLock()
_ALLOWED_TAGS = {"", "WARNING1", "WARNING2", "WARNING3"}
REQUIRED_INCIDENT_COLUMNS = {
    'ID инцидента',
    'Статус',
    'Код закрытия',
    'Тип стенда',
    'Фактическое время возникновения',
    'Создан',
    'Фактическое время окончания',
    'Влияние на клиентский сервисе',
    'Причина',
    'Тема инцидента',
    'Описание',
    'Решение',
}


def validate_incident_columns(columns):
    present = {str(column).strip() for column in columns}
    return sorted(REQUIRED_INCIDENT_COLUMNS - present, key=str.casefold)


def safe_protocol_path(filename):
    base = Path(PROTOCOLS_DIR).resolve()
    supplied = Path(str(filename))
    if supplied.name != str(filename) or supplied.suffix.casefold() != ".txt":
        return None
    candidate = (base / supplied.name).resolve()
    if candidate.parent != base:
        return None
    return candidate


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def csrf_protected(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.method == "POST":
            expected_token = session.get("csrf_token", "")
            supplied_token = request.form.get("csrf_token", "")
            if (
                not expected_token
                or not supplied_token
                or not secrets.compare_digest(expected_token, supplied_token)
            ):
                abort(400)
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token()}


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            app.logger.exception("Не удалось прочитать локальную память %s", MEMORY_FILE)
            return {}
    return {}

def save_memory():
    with _state_lock:
        atomic_write_json(Path(MEMORY_FILE), remembered)

remembered = load_memory()
if not isinstance(remembered, dict):
    remembered = {}
else:
    remembered = {
        str(incident_id): {
            "comment": str(data.get("comment", "")).strip(),
            "tag": (str(data.get("tag", "")).strip() if str(data.get("tag", "")).strip() in _ALLOWED_TAGS else ""),
            "executor": str(data.get("executor", "")).strip(),
        }
        for incident_id, data in remembered.items()
        if isinstance(data, dict)
    }


def find_incident(incident_id):
    expected = str(incident_id).upper()
    return next(
        (
            incident
            for incident in incidents
            if str(incident.get("ID инцидента", "")).upper() == expected
        ),
        None,
    )


def create_gigachat_helper(config):
    return GigaChatHelper(config=config)


def build_ai_context(incident, config=None):
    config = config or GigaChatConfig.from_env(PROJECT_ROOT)
    payload = build_incident_payload(incident)
    return {
        "payload": payload,
        "incident_id": str(payload.get("ID инцидента", "")).upper(),
        "content_hash": incident_content_hash(payload),
        "prompt_version": PROMPT_VERSION,
        "model": config.model,
        "config": config,
    }


def public_ai_state(row):
    if not row:
        return {
            "status": "not_started",
            "analysis": None,
            "completed_at": None,
            "last_error_code": "",
        }
    return {
        "status": row.get("request_status", "not_started"),
        "analysis": row.get("analysis"),
        "completed_at": row.get("completed_at"),
        "last_error_code": row.get("last_error_code", ""),
    }


AI_ERROR_HTTP_STATUS = {
    "certificates_missing": 503,
    "timeout": 504,
    "authentication": 502,
    "access_denied": 502,
    "rate_limited": 503,
    "service_unavailable": 503,
    "service_error": 502,
    "invalid_response": 502,
    "connection": 503,
    "unknown": 502,
}

# ====================== ДАТЫ ======================
def parse_incident_date(value):
    if value is None:
        return None
    if hasattr(value, 'to_pydatetime'):
        try:
            return value.to_pydatetime().replace(tzinfo=None)
        except Exception:
            pass
    value = str(value).strip()
    if not value or value.lower() in ('nan', 'none', 'nat', ''):
        return None
    formats = [
        '%d.%m.%Y %H:%M:%S',
        '%d.%m.%Y %H:%M',
        '%d.%m.%Y',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%d/%m/%Y %H:%M:%S',
        '%d/%m/%Y %H:%M',
        '%d/%m/%Y',
        '%Y.%m.%d %H:%M:%S',
        '%Y.%m.%d %H:%M',
        '%Y.%m.%d',
    ]
    clean = value.replace('T', ' ')[:19]
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt)
        except Exception:
            continue
    return None

def filter_by_date(inc_list, date_from_str, date_to_str):
    if not date_from_str and not date_to_str:
        return inc_list
    date_from = None
    date_to = None
    try:
        if date_from_str:
            date_from = datetime.strptime(date_from_str, '%Y-%m-%d')
    except Exception:
        pass
    try:
        if date_to_str:
            date_to = datetime.strptime(date_to_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
    except Exception:
        pass
    result = []
    for inc in inc_list:
        raw = inc.get('Фактическое время возникновения', '') or inc.get('Фактическое время возникновения инцидента', '')
        dt = parse_incident_date(raw)
        if dt is None:
            continue
        if date_from and dt < date_from:
            continue
        if date_to and dt > date_to:
            continue
        result.append(inc)
    return result

# ====================== ВСПОМОГАТЕЛЬНЫЕ ======================
def is_test_incident(inc):
    return engine_is_test_incident(inc)

def extract_jira_links(text):
    patterns = [
        (r'(OPLOT-\d+)',  'https://jira.delta.sbrf.ru/browse/{}'),
        (r'(SMECLM-\d+)', 'https://jira.sberbank.ru/browse/{}'),
        (r'(SMECSC-\d+)', 'https://jira.delta.sbrf.ru/browse/{}'),
        (r'(EMRM-\d+)',   'https://jira.sberbank.ru/browse/{}'),
        (r'(DRMMMB-\d+)', 'https://jira.sberbank.ru/browse/{}'),
    ]
    links = {}
    for pattern, base_url in patterns:
        for match in re.findall(pattern, text, re.I):
            links[match.upper()] = base_url.format(match.upper())
    return links

def analyze_chronology(sol):
    return []

def extract_affected_systems(text):
    systems = {
        'Oracle', 'PostgreSQL', 'Kafka', 'RabbitMQ', 'Redis', 'Nginx', 'Apache', 'Tomcat',
        'Kubernetes', 'Docker', 'Linux', 'Windows', 'Zabbix', 'Prometheus', 'Grafana',
        'Jenkins', 'GitLab', 'OpenShift', 'VMware', 'MQ', 'WebSphere'
    }
    found = set()
    for sys in systems:
        if re.search(r'\b' + re.escape(sys) + r'\b', text, re.I):
            found.add(sys)
    return sorted(list(found))

def extract_problem_types(text):
    types = {
        'Диск', 'Файловая система', 'CPU', 'Высокая нагрузка', 'Память', 'OutOfMemory', 'OOM',
        'GC', 'Сеть', 'DNS', 'Балансировщик', 'SSL', 'Сертификат', 'База данных', 'SQL',
        'Deadlock', 'Replication', 'Очередь', 'Блокировка', 'Timeout', 'Авторизация',
        'LDAP', 'Kerberos', 'SSO', 'Интеграция', 'REST', 'SOAP', 'API'
    }
    found = set()
    for t in types:
        if re.search(r'\b' + re.escape(t) + r'\b', text, re.I):
            found.add(t)
    return sorted(list(found))

def check_reason_quality(reason):
    if not reason or len(reason.strip()) < 15:
        return "Причина описана слишком кратко"
    bad_phrases = ['исправлено', 'устранено', 'сбой', 'ошибка', 'не работало', 'проблема',
                   'инцидент', 'восстановлено', 'работы выполнены']
    if any(phrase in reason.lower() for phrase in bad_phrases) and len(reason.strip()) < 30:
        return "Причина описана слишком общими словами"
    return None

def clean_uploads(keep=None):
    keep_path = Path(keep).resolve() if keep else None
    upload_directory = Path(app.config['UPLOAD_FOLDER'])
    upload_directory.mkdir(parents=True, exist_ok=True)
    for path in upload_directory.iterdir():
        try:
            if path.is_file() and (keep_path is None or path.resolve() != keep_path):
                path.unlink()
        except OSError:
            app.logger.exception("Не удалось очистить загруженный файл %s", path.name)

def analyze_incident(inc):
    return run_incident_audit(inc)

def get_filtered_incidents():
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    return filter_by_date(incidents, date_from, date_to)

def evaluate_fix_status(inc, history_row, analysis=None):
    """
    Сравнивает прошлый комментарий аудитора с текущим состоянием инцидента.
    Возвращает dict: status_label, status_class, details (list)
    """
    comment = str(history_row.get('comment') or '').lower()
    if classify_incident(inc) not in {'manual', 'duplicate'}:
        analysis = {}
    elif analysis is None:
        analysis = analyze_incident(inc)
    gaps = [g.lower() for g in analysis.get('Замечания', [])]
    why = str(analysis.get('Почему произошло', '') or '')
    details = []
    fixed_flags = []
    open_flags = []

    def has_gap(*words):
        return any(any(w in g for w in words) for g in gaps)

    # --- правила по ключевым словам комментария ---
    if any(w in comment for w in ['причин', 'почему']):
        if not why or why == 'Не указано' or len(why.strip()) < 10 or has_gap('причин'):
            open_flags.append('Причина по-прежнему не заполнена или слабая')
        else:
            fixed_flags.append('Причина заполнена')

    if any(w in comment for w in ['время начала', 'дата начала', 'начало']):
        if analysis.get('Дата начала') == 'Нет' or has_gap('время начала'):
            open_flags.append('Время начала по-прежнему не указано')
        else:
            fixed_flags.append('Время начала указано')

    if any(w in comment for w in ['время окончания', 'дата окончания', 'окончан', 'устранен']):
        if analysis.get('Дата окончания') == 'Нет' or has_gap('время окончания'):
            open_flags.append('Время окончания по-прежнему не указано')
        else:
            fixed_flags.append('Время окончания указано')

    if any(w in comment for w in ['хронолог']):
        if has_gap('хронолог'):
            open_flags.append('Хронология по-прежнему отсутствует/слабая')
        else:
            fixed_flags.append('Хронология присутствует')

    # если комментарий пустой или без известных маркеров — смотрим общий статус
    if not comment.strip() and not fixed_flags and not open_flags:
        if analysis.get('Статус') == 'Инцидент закрыт корректно':
            return {
                'status_label': '🟢 Исправлено',
                'status_class': 'success',
                'details': ['Сейчас замечаний нет']
            }
        if analysis.get('Статус') == 'Есть замечания':
            return {
                'status_label': '🔴 Замечание осталось',
                'status_class': 'danger',
                'details': analysis.get('Замечания', []) or ['Есть замечания']
            }
        return {
            'status_label': '⚪ Нет данных для сравнения',
            'status_class': 'secondary',
            'details': []
        }

    if not fixed_flags and not open_flags:
        # комментарий был, но правила не сработали — fallback
        if analysis.get('Статус') == 'Инцидент закрыт корректно':
            return {
                'status_label': '🟢 Исправлено',
                'status_class': 'success',
                'details': ['Сейчас замечаний нет']
            }
        return {
            'status_label': '🔴 Замечание осталось',
            'status_class': 'danger',
            'details': analysis.get('Замечания', []) or ['Есть замечания']
        }

    if fixed_flags and not open_flags:
        return {
            'status_label': '🟢 Исправлено',
            'status_class': 'success',
            'details': fixed_flags
        }
    if open_flags and not fixed_flags:
        return {
            'status_label': '🔴 Замечание осталось',
            'status_class': 'danger',
            'details': open_flags
        }
    return {
        'status_label': '🟡 Частично исправлено',
        'status_class': 'warning',
        'details': fixed_flags + open_flags
    }
# ====================== РОУТЫ ======================
TAB_ALIASES = {"remarks": "warnings", "test": "skipped"}
ALLOWED_TABS = {"all", "errors", "warnings", "correct", "inwork", "skipped"}


def normalize_tab(value, default="all"):
    normalized = TAB_ALIASES.get(str(value or "").strip(), str(value or "").strip())
    return normalized if normalized in ALLOWED_TABS else default


def _first_incident_value(incident, *keys):
    for key in keys:
        value = normalize_text(incident.get(key))
        if value:
            return value
    return ""


def build_detail_fields(incident):
    description = _first_incident_value(incident, "Описание")
    detailed_description = _first_incident_value(incident, "Подробное описание")
    fields = [
        {
            "label": "Описание",
            "value": description or detailed_description or "—",
            "primary": True,
        },
        {
            "label": "Решение",
            "value": _first_incident_value(incident, "Решение") or "—",
            "primary": True,
        },
        {
            "label": "Наименование услуги",
            "value": _first_incident_value(
                incident,
                "Наименование услуги ",
                "Наименование услуги",
                "Услуга",
            ) or "—",
            "primary": False,
        },
    ]
    if (
        description
        and detailed_description
        and description.casefold() != detailed_description.casefold()
    ):
        fields.append(
            {
                "label": "Подробное описание",
                "value": detailed_description,
                "primary": False,
            }
        )
    fields.extend(
        [
            {
                "label": "Причина из файла",
                "value": _first_incident_value(incident, "Причина") or "—",
                "primary": False,
            },
            {
                "label": "Влияние из Excel",
                "value": _first_incident_value(
                    incident,
                    "Влияние на клиентский сервисе",
                ) or "—",
                "primary": False,
            },
            {
                "label": "Код закрытия",
                "value": _first_incident_value(incident, "Код закрытия") or "—",
                "primary": False,
            },
        ]
    )
    return fields


def incident_group_key(profile, outcome):
    if profile == "in_work":
        return "inwork"
    if outcome in {"skipped", "system_error"}:
        return "skipped"
    return {
        "error": "errors",
        "warning": "warnings",
        "passed": "correct",
    }.get(outcome, "skipped")


@app.route('/', methods=['GET', 'POST'])
@csrf_protected
def index():
    global incidents

    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not file.filename:
            return "Выберите XLSX-файл", 400
        if Path(file.filename).suffix.casefold() != ".xlsx":
            return "Поддерживаются только файлы .xlsx", 400

        payload = file.read()
        try:
            inspect_xlsx_archive(
                payload,
                max_members=app.config["MAX_XLSX_ARCHIVE_MEMBERS"],
                max_uncompressed_bytes=app.config["MAX_XLSX_UNCOMPRESSED_BYTES"],
            )
        except ValueError as exc:
            app.logger.warning("Отклонена загрузка XLSX: %s", exc)
            return str(exc), 400
        try:
            df = pd.read_excel(BytesIO(payload))
        except Exception:
            app.logger.exception("Не удалось прочитать загруженный XLSX")
            return "Не удалось прочитать Excel. Проверьте структуру файла.", 400

        if len(df.index) > app.config["MAX_XLSX_ROWS"]:
            return "В Excel слишком много строк", 400
        if len(df.columns) > app.config["MAX_XLSX_COLUMNS"]:
            return "В Excel слишком много столбцов", 400
        missing_columns = validate_incident_columns(df.columns)
        if missing_columns:
            return "Отсутствуют обязательные столбцы: " + ", ".join(missing_columns), 400

        upload_directory = Path(app.config['UPLOAD_FOLDER'])
        upload_directory.mkdir(parents=True, exist_ok=True)
        safe_name = secure_filename(file.filename) or "incidents.xlsx"
        if Path(safe_name).suffix.casefold() != ".xlsx":
            safe_name += ".xlsx"
        destination = upload_directory / safe_name
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=upload_directory,
                prefix=".upload-",
                suffix=".xlsx",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, destination)
        except OSError:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            app.logger.exception("Не удалось сохранить проверенный XLSX")
            return "Не удалось сохранить загруженный файл", 500

        clean_uploads(keep=destination)
        incidents = [dict(row) for _, row in df.iterrows()]
        return redirect(url_for('index'))

    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    executor_filter = request.args.get('executor', '')
    search_query = request.args.get('search', '').strip()
    search = search_query.casefold()

    filtered = get_filtered_incidents()

    if search:
        filtered = [
            inc for inc in filtered
            if search in str(inc.get('ID инцидента', '')).lower()
            or search in str(inc.get('Исполнитель', '')).lower()
        ]

    if executor_filter:
        filtered = [inc for inc in filtered if executor_filter in str(inc.get('Исполнитель', ''))]

    active_tab = normalize_tab(request.args.get('tab', 'all'))

    history_ids = get_all_history_ids()
    repeated_count = sum(
        str(inc.get('ID инцидента', '')).upper() in history_ids
        for inc in filtered
    )
    only_repeated = request.args.get('only_repeated') == '1'
    if only_repeated:
        filtered = [
            inc for inc in filtered
            if str(inc.get('ID инцидента', '')).upper() in history_ids
        ]

    executor_values = {normalize_text(inc.get('Исполнитель')) for inc in incidents}
    executors = sorted(value for value in executor_values if value)

    ai_config = GigaChatConfig.from_env(PROJECT_ROOT)
    ai_keys = []
    for inc in filtered:
        context = build_ai_context(inc, config=ai_config)
        ai_keys.append(
            (
                context["incident_id"],
                context["content_hash"],
                context["prompt_version"],
                context["model"],
            )
        )
    ai_states = get_ai_states(ai_keys)

    prepared_incidents = []
    for inc in filtered:
        inc_copy = inc.copy()
        inc_copy['executor_display'] = normalize_text(inc.get('Исполнитель')) or 'Исполнитель не указан'
        inc_copy['subject_display'] = normalize_text(inc.get('Тема инцидента')) or 'Тема не указана'
        inc_copy['analysis'] = analyze_incident(inc)
        incident_id = str(inc.get('ID инцидента', '')).upper()
        inc_copy['ai_state'] = public_ai_state(ai_states.get(incident_id))
        inc_copy['is_repeated'] = incident_id in history_ids
        inc_copy['history'] = (
            get_history_for_incident(incident_id)
            if inc_copy['is_repeated']
            else []
        )
        prepared_incidents.append((classify_incident(inc), inc_copy))

    problem_counter = Counter()
    for profile, inc_copy in prepared_incidents:
        if profile in {'manual', 'duplicate'}:
            for ptype in inc_copy['analysis'].get('Problem_types', []):
                problem_counter[ptype] += 1
    problem_stats = sorted(problem_counter.items(), key=lambda x: x[1], reverse=True) if problem_counter else []

    incident_groups = {
        "all": [inc_copy for _, inc_copy in prepared_incidents],
        "errors": [],
        "warnings": [],
        "correct": [],
        "inwork": [],
        "skipped": [],
    }
    for profile, inc_copy in prepared_incidents:
        group_key = incident_group_key(profile, inc_copy['analysis'].get('outcome'))
        incident_groups[group_key].append(inc_copy)

    dashboard_stats = {
        "total": len(prepared_incidents),
        "errors": len(incident_groups["errors"]),
        "warnings": len(incident_groups["warnings"]),
        "correct": len(incident_groups["correct"]),
        "inwork": len(incident_groups["inwork"]),
        "skipped": len(incident_groups["skipped"]),
        "repeated": sum(inc_copy['is_repeated'] for _, inc_copy in prepared_incidents),
    }

    return render_template(
        'index.html',
        incident_groups=incident_groups,
        dashboard_stats=dashboard_stats,
        correct=incident_groups['correct'],
        remarks=incident_groups['warnings'],
        in_work=incident_groups['inwork'],
        test=incident_groups['skipped'],
        repeated_count=repeated_count,
        only_repeated=only_repeated,
        active_tab=active_tab,
        problem_stats=problem_stats,
        remembered_count=len(remembered),
        remembered_list=remembered,
        executors=executors,
        current_executor=executor_filter,
        current_search=search_query,
        date_from=date_from,
        date_to=date_to
    )

@app.route('/incident/<inc_id>')
def incident_detail(inc_id):
    inc = find_incident(inc_id)
    if not inc:
        return "Инцидент не найден", 404

    inc_copy = inc.copy()
    inc_copy['executor_display'] = normalize_text(inc.get('Исполнитель')) or 'Исполнитель не указан'
    inc_copy['subject_display'] = normalize_text(inc.get('Тема инцидента')) or 'Тема не указана'
    inc_copy['analysis'] = analyze_incident(inc)

    current_tab = normalize_tab(request.args.get('tab', 'all'))
    active_detail_tab = "ai" if request.args.get("view") == "ai" else "audit"
    history = get_history_for_incident(inc_id)
    ai_context = build_ai_context(inc)
    ai_row = get_ai_analysis(
        ai_context["incident_id"],
        ai_context["content_hash"],
        ai_context["prompt_version"],
        ai_context["model"],
    )

    # Оценка исправления для каждой прошлой проверки
    history_enriched = []
    for h in history:
        row = dict(h)
        row['fix_eval'] = evaluate_fix_status(inc, h, analysis=inc_copy['analysis'])
        history_enriched.append(row)

    return render_template(
        'detail.html',
        inc=inc_copy,
        current_tab=current_tab,
        is_remembered=inc_id in remembered,
        remembered_data=remembered.get(inc_id, {}),
        history=history_enriched,
        detail_fields=build_detail_fields(inc),
        ai_state=public_ai_state(ai_row),
        active_detail_tab=active_detail_tab,
    )


def _ai_db_key(context):
    return {
        "incident_id": context["incident_id"],
        "content_hash": context["content_hash"],
        "prompt_version": context["prompt_version"],
        "model": context["model"],
    }


@app.route('/incident/<inc_id>/ai-analysis/status')
def ai_analysis_status(inc_id):
    inc = find_incident(inc_id)
    if not inc:
        return jsonify({"status": "not_found", "message": "Инцидент не найден"}), 404
    context = build_ai_context(inc)
    row = get_ai_analysis(**_ai_db_key(context))
    return jsonify(public_ai_state(row))


@app.route('/incident/<inc_id>/ai-analysis', methods=['POST'])
@csrf_protected
def run_ai_analysis(inc_id):
    inc = find_incident(inc_id)
    if not inc:
        return jsonify({"status": "not_found", "message": "Инцидент не найден"}), 404

    context = build_ai_context(inc)
    key = _ai_db_key(context)
    force = request.form.get("force") == "1"
    claim = claim_ai_analysis(**key, force=force)
    if claim == "cached":
        state = public_ai_state(get_ai_analysis(**key))
        state["status"] = "cached"
        return jsonify(state)
    if claim == "running":
        return jsonify({"status": "running", "message": "AI-анализ уже выполняется"}), 202

    request_id = uuid.uuid4().hex
    started = time.monotonic()
    try:
        helper = create_gigachat_helper(context["config"])
        raw_response = helper.generate(build_prompt(context["payload"]))
        analysis = parse_ai_report(raw_response)
        complete_ai_analysis(**key, analysis=analysis)
        app.logger.info(
            "AI analysis completed incident=%s request=%s duration_ms=%d",
            context["incident_id"],
            request_id,
            int((time.monotonic() - started) * 1000),
        )
        state = public_ai_state(get_ai_analysis(**key))
        state["status"] = "completed"
        return jsonify(state)
    except GigaChatConfigurationError as error:
        error_code = error.code
        message = str(error)
        error_type = type(error).__name__
    except GigaChatRequestError as error:
        error_code = error.code
        message = str(error)
        error_type = type(error).__name__
    except AIReportFormatError:
        error_code = "invalid_response"
        message = "GigaChat вернул ответ неизвестного формата. Запрос можно повторить вручную."
        error_type = "AIReportFormatError"
    except Exception as error:
        error_code = "unknown"
        message = "Не удалось выполнить AI-анализ."
        error_type = type(error).__name__

    fail_ai_analysis(**key, error_code=error_code)
    app.logger.warning(
        "AI analysis failed incident=%s request=%s code=%s error=%s duration_ms=%d",
        context["incident_id"],
        request_id,
        error_code,
        error_type,
        int((time.monotonic() - started) * 1000),
    )
    state = public_ai_state(get_ai_analysis(**key))
    state.update(
        {
            "status": "failed",
            "error_code": error_code,
            "message": message,
        }
    )
    return jsonify(state), AI_ERROR_HTTP_STATUS.get(error_code, 502)

@app.route('/remember/<inc_id>', methods=['POST'])
@csrf_protected
def remember_incident(inc_id):
    comment = request.form.get('comment', '').strip()
    tag = request.form.get('tag', '').strip()
    if tag not in _ALLOWED_TAGS:
        return "Недопустимый тег", 400
    inc = next((i for i in incidents if str(i.get('ID инцидента', '')) == inc_id), None)
    if not inc:
        return "Инцидент не найден", 404
    with _state_lock:
        previous = remembered.get(inc_id)
        remembered[inc_id] = {
            'comment': comment,
            'tag': tag,
            'executor': str(inc.get('Исполнитель', ''))
        }
        try:
            save_memory()
        except OSError:
            if previous is None:
                remembered.pop(inc_id, None)
            else:
                remembered[inc_id] = previous
            app.logger.exception("Не удалось сохранить инцидент %s в текущий протокол", inc_id)
            return "Не удалось сохранить текущий протокол", 500
    return redirect(url_for('incident_detail', inc_id=inc_id, tab=request.args.get('tab', 'correct')))

@app.route('/unremember/<inc_id>', methods=['POST'])
@csrf_protected
def unremember_incident(inc_id):
    with _state_lock:
        previous = remembered.pop(inc_id, None)
        try:
            save_memory()
        except OSError:
            if previous is not None:
                remembered[inc_id] = previous
            app.logger.exception("Не удалось удалить инцидент %s из текущего протокола", inc_id)
            return "Не удалось сохранить текущий протокол", 500
    if request.form.get("return_to") == "detail" and any(
        str(item.get("ID инцидента", "")) == inc_id for item in incidents
    ):
        return redirect(
            url_for(
                "incident_detail",
                inc_id=inc_id,
                tab=normalize_tab(request.form.get("tab", "all")),
            )
        )
    return redirect(url_for('index', tab=normalize_tab(request.form.get('tab', 'all'))))

@app.route('/bulk_remember', methods=['POST'])
@csrf_protected
def bulk_remember():
    ids = request.form.getlist('ids')
    tag = request.form.get('bulk_tag', '').strip()
    if tag not in _ALLOWED_TAGS:
        return "Недопустимый тег", 400
    with _state_lock:
        previous = dict(remembered)
        for inc_id in ids:
            inc = next((i for i in incidents if str(i.get('ID инцидента', '')) == inc_id), None)
            if inc and inc_id not in remembered:
                remembered[inc_id] = {
                    'comment': '',
                    'tag': tag,
                    'executor': str(inc.get('Исполнитель', ''))
                }
        try:
            save_memory()
        except OSError:
            remembered.clear()
            remembered.update(previous)
            app.logger.exception("Не удалось сохранить массовое добавление в протокол")
            return "Не удалось сохранить текущий протокол", 500
    return redirect(url_for('index', tab=normalize_tab(request.form.get('tab', 'all'))))


def _list_protocol_files():
    paths = []
    for path in Path(PROTOCOLS_DIR).glob("*.txt"):
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        paths.append((modified, path.name))
    return [name for _, name in sorted(paths, reverse=True)]

@app.route('/protocol', methods=['POST'])
@csrf_protected
def protocol():
    filtered = get_filtered_incidents()
    with _state_lock:
        remembered_snapshot = {key: dict(value) for key, value in remembered.items()}
    lines = ["Коллеги, добрый день!", "Направляю результат анализа инцидентов за текущую неделю:", ""]
    counter = 1

    for inc_id, data in remembered_snapshot.items():
        line = f"{counter}. {inc_id} — {data['executor']}"
        if data.get('tag'):
            line += f" — {data['tag']}"
        lines.append(line)
        if data.get('comment'):
            lines.append(f"   Комментарий: {data['comment']}")
        counter += 1

    in_work_lines = []
    for inc in filtered:
        if str(inc.get('Статус', '')) == "В работе":
            inc_id = str(inc.get('ID инцидента', ''))
            if inc_id not in remembered_snapshot:
                executor = str(inc.get('Исполнитель', ''))
                in_work_lines.append(f"{counter}. {inc_id} — {executor} — В РАБОТЕ")
                counter += 1

    if in_work_lines:
        lines.append("")
        lines.append("В работе — переносятся на следующую неделю:")
        lines.extend(in_work_lines)

    if counter == 1:
        lines.append("Нет данных для протокола.")

    protocol_text = "\n".join(lines)

    should_save = True
    files = _list_protocol_files()
    if files:
        try:
            with open(os.path.join(PROTOCOLS_DIR, files[0]), 'r', encoding='utf-8') as f:
                if f.read().strip() == protocol_text.strip():
                    should_save = False
        except OSError:
            app.logger.exception("Не удалось прочитать последний протокол %s", files[0])

    if should_save:
        base = datetime.now().strftime("%d.%m.%Y")
        filename = f"{base}.txt"
        n = 2
        while os.path.exists(os.path.join(PROTOCOLS_DIR, filename)):
            filename = f"{base} ({n}).txt"
            n += 1
        protocol_path = Path(PROTOCOLS_DIR) / filename
        try:
            atomic_write_text(protocol_path, protocol_text)
        except OSError:
            app.logger.exception("Не удалось сохранить протокол %s", filename)
            return "Не удалось сохранить протокол", 500

        history_entries = []
        for inc_id, data in remembered_snapshot.items():
            inc = next(
                (i for i in incidents if str(i.get('ID инцидента', '')).upper() == str(inc_id).upper()),
                None
            )
            history_entries.append({
                'incident_id': inc_id,
                'tag': data.get('tag', ''),
                'comment': data.get('comment', ''),
                'executor': data.get('executor', ''),
                'description': str(inc.get('Описание', '')) if inc else '',
                'solution': str(inc.get('Решение', '')) if inc else '',
                'reason': str(inc.get('Причина', '')) if inc else '',
            })
        try:
            save_history_entries(filename, history_entries)
        except Exception:
            app.logger.exception("Не удалось сохранить историю протокола %s", filename)
            try:
                protocol_path.unlink(missing_ok=True)
            except OSError:
                app.logger.exception("Не удалось откатить файл протокола %s", filename)
            return "Не удалось сохранить историю протокола", 500

    return render_template('protocol.html', protocol_text=protocol_text)

@app.route('/protocols_history')
def protocols_history():
    files = _list_protocol_files()
    return render_template('protocols_history.html', files=files)

@app.route('/protocol_file/<filename>')
def protocol_file(filename):
    path = safe_protocol_path(filename)
    if path and path.is_file():
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        return render_template('protocol.html', protocol_text=text)
    return "Файл не найден", 404

@app.route('/delete_protocol/<filename>', methods=['POST'])
@csrf_protected
def delete_protocol(filename):
    path = safe_protocol_path(filename)
    if path and path.is_file():
        try:
            os.remove(path)
        except OSError:
            app.logger.exception("Не удалось удалить протокол %s", path.name)
            return "Не удалось удалить протокол", 500
    return redirect(url_for('protocols_history'))

@app.route('/clear_memory', methods=['POST'])
@csrf_protected
def clear_memory():
    with _state_lock:
        previous = dict(remembered)
        remembered.clear()
        try:
            save_memory()
        except OSError:
            remembered.update(previous)
            app.logger.exception("Не удалось очистить текущий протокол")
            return "Не удалось сохранить текущий протокол", 500
    return redirect(url_for('index'))

@app.route('/clear_incidents', methods=['POST'])
@csrf_protected
def clear_incidents():
    global incidents
    incidents = []
    return redirect(url_for('index'))

@app.route('/export')
def export():
    filtered = get_filtered_incidents()
    if not filtered:
        return "Нет данных"
    data = []
    for inc in filtered:
        profile = classify_incident(inc)
        if profile == 'in_work':
            status = "В работе"
        elif profile == 'test':
            status = "Тестовый"
        elif profile == 'automatic':
            status = "Автоматическое закрытие"
        else:
            status = analyze_incident(inc)['Статус']
        data.append({
            'ID инцидента': neutralize_spreadsheet_value(inc.get('ID инцидента', '')),
            'Исполнитель': neutralize_spreadsheet_value(inc.get('Исполнитель', '')),
            'Статус аудита': neutralize_spreadsheet_value(status),
        })
    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name="audit_result.xlsx", as_attachment=True)

if __name__ == '__main__':
    app.run(
        host="127.0.0.1",
        port=_positive_env_int("INCIDENT_MANAGER_PORT", 5080),
        debug=False,
        use_reloader=False,
    )
