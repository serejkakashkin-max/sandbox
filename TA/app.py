from flask import Flask, render_template, request, redirect, url_for, send_file
import json
import os
import re
import threading
from collections import Counter
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from werkzeug.utils import secure_filename


TA_DIR = Path(__file__).resolve().parent
BASE_DIR = TA_DIR.parent
RUNTIME_DIR = BASE_DIR / "cache" / "ta_incident_auditor"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
MEMORY_FILE = RUNTIME_DIR / "memory.json"
PROTOCOLS_DIR = RUNTIME_DIR / "protocols"

for runtime_path in (UPLOAD_DIR, PROTOCOLS_DIR):
    runtime_path.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(TA_DIR / "templates"),
    static_folder=str(TA_DIR / "static"),
)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)

incidents = []
_state_lock = threading.RLock()
_ALLOWED_TABS = {"correct", "remarks", "inwork", "test"}
_ALLOWED_TAGS = {"", "WARNING1", "WARNING2", "WARNING3"}
_PROTOCOL_FILENAME_RE = re.compile(r"^protocol_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.txt$")


# ====================== РАБОТА С ПАМЯТЬЮ ======================
def _atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def load_memory():
    if not MEMORY_FILE.exists():
        return {}

    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    normalized = {}
    for inc_id, raw_data in data.items():
        if not isinstance(raw_data, dict):
            continue
        tag = str(raw_data.get("tag", "")).strip()
        if tag not in _ALLOWED_TAGS:
            tag = ""
        normalized[str(inc_id)] = {
            "comment": str(raw_data.get("comment", "")).strip(),
            "tag": tag,
            "executor": str(raw_data.get("executor", "")).strip(),
        }
    return normalized


def save_memory():
    with _state_lock:
        payload = json.dumps(remembered, ensure_ascii=False, indent=2)
        _atomic_write_text(MEMORY_FILE, payload)


remembered = load_memory()


# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================
def _validated_tab(value):
    return value if value in _ALLOWED_TABS else "correct"


def _clean_cell(value):
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


def _find_incident(inc_id):
    with _state_lock:
        return next(
            (item for item in incidents if str(item.get("ID инцидента", "")) == inc_id),
            None,
        )


def _redirect_to_index(tab="correct", executor=""):
    params = {"tab": _validated_tab(tab)}
    if executor:
        params["executor"] = executor
    return redirect(url_for("index", **params))


def _protocol_path(filename):
    if not _PROTOCOL_FILENAME_RE.fullmatch(filename or ""):
        return None
    return PROTOCOLS_DIR / filename


def is_test_incident(inc):
    """Определяет тестовые / тестовые стендовые инциденты."""
    ts = str(inc.get("Тип стенда", "")).upper()
    if any(value in ts for value in ("MAJOR-GO", "MAJOR-CHECK")):
        return True

    desc = str(inc.get("Описание", ""))
    return bool(re.search(r"\b(ts|tv|tsl|tst)[a-z0-9-]{4,}\b", desc, re.I))


def extract_jira_links(text):
    patterns = [
        (r"(OPLOT-\d+)", "https://jira.delta.sbrf.ru/browse/{}"),
        (r"(SMECLM-\d+)", "https://jira.sberbank.ru/browse/{}"),
        (r"(SMECSC-\d+)", "https://jira.delta.sbrf.ru/browse/{}"),
        (r"(EMRM-\d+)", "https://jira.sberbank.ru/browse/{}"),
        (r"(DRMMMB-\d+)", "https://jira.sberbank.ru/browse/{}"),
    ]
    links = {}
    for pattern, base_url in patterns:
        for match in re.findall(pattern, text, re.I):
            key = match.upper()
            links[key] = base_url.format(key)
    return links


def analyze_chronology(sol):
    """Проверка разрывов отключена в обновлении TA по решению автора."""
    return []


def extract_affected_systems(text):
    systems = {
        "Oracle", "PostgreSQL", "Kafka", "RabbitMQ", "Redis", "Nginx", "Apache", "Tomcat",
        "Kubernetes", "Docker", "Linux", "Windows", "Zabbix", "Prometheus", "Grafana",
        "Jenkins", "GitLab", "OpenShift", "VMware", "MQ", "WebSphere",
    }
    found = set()
    for system in systems:
        if re.search(r"\b" + re.escape(system) + r"\b", text, re.I):
            found.add(system)
    return sorted(found)


def extract_problem_types(text):
    problem_types = {
        "Диск", "Файловая система", "CPU", "Высокая нагрузка", "Память", "OutOfMemory", "OOM",
        "GC", "Сеть", "DNS", "Балансировщик", "SSL", "Сертификат", "База данных", "SQL",
        "Deadlock", "Replication", "Очередь", "Блокировка", "Timeout", "Авторизация",
        "LDAP", "Kerberos", "SSO", "Интеграция", "REST", "SOAP", "API",
    }
    found = set()
    for problem_type in problem_types:
        if re.search(r"\b" + re.escape(problem_type) + r"\b", text, re.I):
            found.add(problem_type)
    return sorted(found)


def check_reason_quality(reason):
    if not reason or len(reason.strip()) < 15:
        return "Причина описана слишком кратко"

    bad_phrases = [
        "исправлено", "устранено", "сбой", "ошибка", "не работало", "проблема",
        "инцидент", "восстановлено", "работы выполнены",
    ]
    if any(phrase in reason.lower() for phrase in bad_phrases) and len(reason.strip()) < 30:
        return "Причина описана слишком общими словами"
    return None


def clean_uploads():
    """Удаляет старые загруженные файлы TA перед сохранением нового Excel."""
    for path in UPLOAD_DIR.iterdir():
        if not path.is_file():
            continue
        try:
            path.unlink()
        except OSError:
            pass


def _list_protocol_files():
    return sorted(
        (
            path.name
            for path in PROTOCOLS_DIR.iterdir()
            if path.is_file() and _PROTOCOL_FILENAME_RE.fullmatch(path.name)
        ),
        reverse=True,
    )


# ====================== ОСНОВНОЙ АНАЛИЗ ======================
def analyze_incident(inc):
    sol = str(inc.get("Решение", ""))
    desc = str(inc.get("Описание", ""))

    what_match = re.search(r"Проблема[:\s]*(.+?)(?:\n|$)", desc, re.I)
    what = what_match.group(1).strip() if what_match else desc.split("\n")[0][:180]

    why_match = re.search(r"Причина[:\s]*(.+?)(?:\n|$)", sol, re.I)
    why = why_match.group(1).strip() if why_match else "Не указано"

    cause_from_file = _clean_cell(inc.get("Причина", ""))
    if cause_from_file:
        why = cause_from_file

    closure_code = _clean_cell(inc.get("Код закрытия", ""))

    competencies_parts = []
    if re.search(r"Оплот", sol, re.I):
        competencies_parts.append("Оплот")
    if re.search(r"ЗПИ", sol, re.I):
        competencies_parts.append("ЗПИ")
    if re.search(r"администратор", sol, re.I):
        competencies_parts.append("Администраторы")
    competencies = " / ".join(competencies_parts) if competencies_parts else "Не указано"

    ticket = re.search(r"(OPLOT-\d+|JIRA-\d+|DRMMMB-\d+)", sol, re.I)
    steps = f"Выполнены работы в рамках тикета {ticket.group(1)}" if ticket else "Выполнены работы"

    has_start = bool(
        re.search(r"(Время начала|Фактическое время возникновения|начало инцидента)", sol, re.I)
    )
    has_end = bool(
        re.search(
            r"(Время устранения|Фактическое время окончания|время окончания|окончания инцидента)",
            sol,
            re.I,
        )
    )
    has_chronology = bool(
        re.search(r"хронология|краткая хронология", sol, re.I)
        or re.search(r"\d{2}:\d{2}", sol)
    )

    gaps = []
    if not why or len(why) < 10:
        gaps.append("Причина")
    if not has_start:
        gaps.append("Время начала")
    if not has_end:
        gaps.append("Время окончания")
    if not has_chronology:
        gaps.append("Хронология")

    # По решению автора TA анализ разрывов в хронологии не выполняется.
    gaps.extend(analyze_chronology(sol))

    reason_issue = check_reason_quality(why)
    if reason_issue:
        gaps.append(reason_issue)

    status = "Инцидент закрыт корректно" if not gaps else "Есть замечания"

    return {
        "Статус": status,
        "Что произошло": what,
        "Почему произошло": why,
        "Привлечённые компетенции": competencies,
        "Ход устранения": steps,
        "Дата начала": "Да" if has_start else "Нет",
        "Дата окончания": "Да" if has_end else "Нет",
        "Замечания": gaps,
        "Jira_links": extract_jira_links(sol),
        "Affected_systems": extract_affected_systems(sol + desc),
        "Problem_types": extract_problem_types(sol + desc),
        "Код закрытия": closure_code,
        "Причина из файла": cause_from_file,
    }


# ====================== ПРОТОКОЛ ======================
def _build_protocol_text():
    lines = [
        "Коллеги, добрый день!",
        "Направляю результат анализа инцидентов за текущую неделю:",
        "",
    ]
    counter = 1

    with _state_lock:
        remembered_snapshot = dict(remembered)
        incidents_snapshot = list(incidents)

    for inc_id, data in remembered_snapshot.items():
        line = f"{counter}. {inc_id} — {data.get('executor', '')}"
        if data.get("tag"):
            line += f" — {data['tag']}"
        lines.append(line)
        if data.get("comment"):
            lines.append(f"   Комментарий: {data['comment']}")
        counter += 1

    in_work_lines = []
    for inc in incidents_snapshot:
        if str(inc.get("Статус", "")) != "В работе":
            continue
        inc_id = str(inc.get("ID инцидента", ""))
        if inc_id in remembered_snapshot:
            continue
        executor = str(inc.get("Исполнитель", ""))
        in_work_lines.append(f"{counter}. {inc_id} — {executor} — В РАБОТЕ")
        counter += 1

    if in_work_lines:
        lines.extend(["", "В работе — переносятся на следующую неделю:"])
        lines.extend(in_work_lines)

    if counter == 1:
        lines.append("Нет данных для протокола.")

    return "\n".join(lines)


def _save_protocol_if_changed(protocol_text):
    files = _list_protocol_files()
    if files:
        latest_path = PROTOCOLS_DIR / files[0]
        try:
            if latest_path.read_text(encoding="utf-8").strip() == protocol_text.strip():
                return False
        except OSError:
            pass

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target_path = PROTOCOLS_DIR / f"protocol_{timestamp}.txt"
    _atomic_write_text(target_path, protocol_text)
    return True


# ====================== РОУТЫ ======================
@app.route("/", methods=["GET", "POST"])
def index():
    global incidents

    current_tab = _validated_tab(request.values.get("tab", "correct"))
    executor_filter = request.values.get("executor", "").strip()

    if request.method == "POST":
        file = request.files.get("file")
        if file and file.filename:
            filename = secure_filename(file.filename) or "uploaded.xlsx"
            if not filename.lower().endswith(".xlsx"):
                return "Поддерживаются только файлы .xlsx", 400

            clean_uploads()
            filepath = UPLOAD_DIR / filename
            file.save(filepath)
            try:
                dataframe = pd.read_excel(filepath)
            except Exception as exc:
                try:
                    filepath.unlink()
                except OSError:
                    pass
                return f"Не удалось прочитать Excel-файл: {exc}", 400

            with _state_lock:
                incidents = [dict(row) for _, row in dataframe.iterrows()]
            return _redirect_to_index(current_tab, executor_filter)

    with _state_lock:
        incidents_snapshot = list(incidents)
        remembered_snapshot = dict(remembered)

    problem_counter = Counter()
    for inc in incidents_snapshot:
        if not is_test_incident(inc) and str(inc.get("Статус", "")) != "В работе":
            analysis = analyze_incident(inc)
            for problem_type in analysis.get("Problem_types", []):
                problem_counter[problem_type] += 1
    problem_stats = sorted(problem_counter.items(), key=lambda item: item[1], reverse=True)

    filtered = incidents_snapshot
    search = request.args.get("search", "").lower().strip()
    if search:
        filtered = [
            inc
            for inc in filtered
            if search in str(inc.get("ID инцидента", "")).lower()
            or search in str(inc.get("Исполнитель", "")).lower()
        ]
    if executor_filter:
        filtered = [
            inc for inc in filtered if str(inc.get("Исполнитель", "")) == executor_filter
        ]

    executors = sorted(
        {
            executor
            for inc in incidents_snapshot
            if (executor := _clean_cell(inc.get("Исполнитель", "")))
        }
    )

    correct, remarks, in_work, test = [], [], [], []
    for inc in filtered:
        inc_copy = inc.copy()
        status_field = str(inc.get("Статус", ""))

        if status_field == "В работе":
            in_work.append(inc_copy)
        elif is_test_incident(inc):
            test.append(inc_copy)
        else:
            inc_copy["analysis"] = analyze_incident(inc)
            if "корректно" in inc_copy["analysis"]["Статус"].lower():
                correct.append(inc_copy)
            else:
                remarks.append(inc_copy)

    incident_ids = {
        str(inc.get("ID инцидента", "")) for inc in incidents_snapshot
    }
    return render_template(
        "index.html",
        correct=correct,
        remarks=remarks,
        in_work=in_work,
        test=test,
        problem_stats=problem_stats,
        remembered_count=len(remembered_snapshot),
        remembered_list=remembered_snapshot,
        incident_ids=incident_ids,
        executors=executors,
        current_executor=executor_filter,
        current_tab=current_tab,
    )


@app.route("/incident/<inc_id>")
def incident_detail(inc_id):
    inc = _find_incident(inc_id)
    if not inc:
        return "Инцидент не найден", 404

    inc_copy = inc.copy()
    if str(inc.get("Статус", "")) == "В работе" or is_test_incident(inc):
        inc_copy["analysis"] = {"Статус": "Анализ не проводится для данного типа инцидента"}
    else:
        inc_copy["analysis"] = analyze_incident(inc)

    current_tab = _validated_tab(request.args.get("tab", "correct"))
    current_executor = request.args.get("executor", "").strip()
    with _state_lock:
        remembered_data = remembered.get(inc_id, {}).copy()

    return render_template(
        "detail.html",
        inc=inc_copy,
        current_tab=current_tab,
        current_executor=current_executor,
        is_remembered=bool(remembered_data),
        remembered_data=remembered_data,
    )


@app.route("/remember/<inc_id>", methods=["POST"])
def remember_incident(inc_id):
    inc = _find_incident(inc_id)
    if not inc:
        return "Инцидент не найден", 404

    tag = request.form.get("tag", "").strip()
    if tag not in _ALLOWED_TAGS:
        tag = ""

    with _state_lock:
        remembered[inc_id] = {
            "comment": request.form.get("comment", "").strip(),
            "tag": tag,
            "executor": str(inc.get("Исполнитель", "")).strip(),
        }
        save_memory()

    return redirect(
        url_for(
            "incident_detail",
            inc_id=inc_id,
            tab=_validated_tab(request.form.get("tab", "correct")),
            executor=request.form.get("executor", "").strip() or None,
        )
    )


@app.route("/unremember/<inc_id>", methods=["POST"])
def unremember_incident(inc_id):
    with _state_lock:
        remembered.pop(inc_id, None)
        save_memory()

    tab = _validated_tab(request.form.get("tab", "correct"))
    executor = request.form.get("executor", "").strip()
    if request.form.get("return_to") == "detail" and _find_incident(inc_id):
        return redirect(
            url_for(
                "incident_detail",
                inc_id=inc_id,
                tab=tab,
                executor=executor or None,
            )
        )
    return _redirect_to_index(tab, executor)


@app.route("/bulk_remember", methods=["POST"])
def bulk_remember():
    ids = request.form.getlist("ids")
    tag = request.form.get("bulk_tag", "").strip()
    if tag not in _ALLOWED_TAGS:
        tag = ""

    with _state_lock:
        incidents_by_id = {
            str(inc.get("ID инцидента", "")): inc for inc in incidents
        }
        for inc_id in ids:
            inc = incidents_by_id.get(inc_id)
            if inc and inc_id not in remembered:
                remembered[inc_id] = {
                    "comment": "",
                    "tag": tag,
                    "executor": str(inc.get("Исполнитель", "")).strip(),
                }
        save_memory()

    return _redirect_to_index(
        request.form.get("tab", "correct"),
        request.form.get("executor", "").strip(),
    )


@app.route("/protocol")
def protocol():
    protocol_text = _build_protocol_text()
    saved = _save_protocol_if_changed(protocol_text)
    return render_template("protocol.html", protocol_text=protocol_text, saved=saved)


@app.route("/protocols_history")
def protocols_history():
    return render_template("protocols_history.html", files=_list_protocol_files())


@app.route("/protocol_file/<filename>")
def protocol_file(filename):
    path = _protocol_path(filename)
    if not path or not path.is_file():
        return "Файл не найден", 404
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "Файл не найден", 404
    return render_template("protocol.html", protocol_text=text, saved=False, history_view=True)


@app.route("/clear_memory", methods=["POST"])
def clear_memory():
    with _state_lock:
        remembered.clear()
        save_memory()
    return _redirect_to_index(
        request.form.get("tab", "correct"),
        request.form.get("executor", "").strip(),
    )


@app.route("/clear_incidents", methods=["POST"])
def clear_incidents():
    global incidents
    with _state_lock:
        incidents = []
    return _redirect_to_index(
        request.form.get("tab", "correct"),
        request.form.get("executor", "").strip(),
    )


@app.route("/export")
def export():
    with _state_lock:
        incidents_snapshot = list(incidents)
    if not incidents_snapshot:
        return "Нет данных"

    data = []
    for inc in incidents_snapshot:
        if str(inc.get("Статус", "")) == "В работе":
            status = "В работе"
        elif is_test_incident(inc):
            status = "Тестовый"
        else:
            status = analyze_incident(inc)["Статус"]
        data.append(
            {
                "ID инцидента": inc.get("ID инцидента", ""),
                "Исполнитель": inc.get("Исполнитель", ""),
                "Статус аудита": status,
            }
        )

    dataframe = pd.DataFrame(data)
    output = BytesIO()
    dataframe.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name="audit_result.xlsx", as_attachment=True)


@app.route("/delete_protocol/<filename>", methods=["POST"])
def delete_protocol(filename):
    path = _protocol_path(filename)
    if path and path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
    return redirect(url_for("protocols_history"))


if __name__ == "__main__":
    host = os.getenv("SANDBOX_HOST", "127.0.0.1")
    port = int(os.getenv("SANDBOX_PORT", "3535"))
    app.run(host=host, port=port, debug=False)
