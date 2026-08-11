import sqlite3
import os
import json
from datetime import datetime, timezone
from pathlib import Path

TA_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = TA_DIR.parent / "cache" / "ta_incident_auditor"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RUNTIME_DIR / "history.db"

def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS incident_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            check_date TEXT NOT NULL,
            protocol_file TEXT NOT NULL,
            tag TEXT DEFAULT '',
            comment TEXT DEFAULT '',
            executor TEXT DEFAULT '',
            description TEXT DEFAULT '',
            solution TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            fix_status TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(incident_id, protocol_file)
        )
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_incident_id
        ON incident_history(incident_id)
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS ai_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            model TEXT NOT NULL,
            request_status TEXT NOT NULL
                CHECK(request_status IN ('running', 'completed', 'failed')),
            analysis_json TEXT,
            raw_response TEXT,
            last_error_code TEXT DEFAULT '',
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(incident_id, content_hash, prompt_version, model)
        )
    ''')
    columns = {
        row[1]
        for row in c.execute("PRAGMA table_info(ai_analyses)").fetchall()
    }
    if "raw_response" not in columns:
        c.execute("ALTER TABLE ai_analyses ADD COLUMN raw_response TEXT")
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_ai_analyses_incident
        ON ai_analyses(incident_id)
    ''')
    conn.commit()
    conn.close()


def _ai_key(incident_id, content_hash, prompt_version, model):
    return (
        str(incident_id).upper(),
        str(content_hash),
        str(prompt_version),
        str(model),
    )


def _utc_datetime(value=None):
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _timestamp(value=None):
    return _utc_datetime(value).isoformat(timespec="seconds")


def _read_ai_row(row):
    if row is None:
        return None
    result = dict(row)
    raw_analysis = result.pop("analysis_json", None)
    if raw_analysis:
        try:
            result["analysis"] = json.loads(raw_analysis)
        except json.JSONDecodeError:
            result["analysis"] = None
    else:
        result["analysis"] = None
    return result


def get_ai_analysis(incident_id, content_hash, prompt_version, model):
    conn = get_conn()
    try:
        row = conn.execute('''
            SELECT * FROM ai_analyses
            WHERE incident_id = ? AND content_hash = ?
              AND prompt_version = ? AND model = ?
        ''', _ai_key(incident_id, content_hash, prompt_version, model)).fetchone()
        return _read_ai_row(row)
    finally:
        conn.close()


def claim_ai_analysis(
    incident_id,
    content_hash,
    prompt_version,
    model,
    *,
    force=False,
    stale_after_seconds=900,
    now=None,
):
    key = _ai_key(incident_id, content_hash, prompt_version, model)
    current = _utc_datetime(now)
    current_stamp = _timestamp(current)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute('''
            SELECT request_status, started_at
            FROM ai_analyses
            WHERE incident_id = ? AND content_hash = ?
              AND prompt_version = ? AND model = ?
        ''', key).fetchone()

        if row is not None:
            if row["request_status"] == "completed" and not force:
                conn.commit()
                return "cached"
            if row["request_status"] == "running" and row["started_at"]:
                try:
                    started = datetime.fromisoformat(row["started_at"])
                    started = _utc_datetime(started)
                except (TypeError, ValueError):
                    started = None
                if started is not None and (current - started).total_seconds() < stale_after_seconds:
                    conn.commit()
                    return "running"

            conn.execute('''
                UPDATE ai_analyses
                SET request_status = 'running', last_error_code = '',
                    started_at = ?, updated_at = ?
                WHERE incident_id = ? AND content_hash = ?
                  AND prompt_version = ? AND model = ?
            ''', (current_stamp, current_stamp, *key))
        else:
            conn.execute('''
                INSERT INTO ai_analyses
                (incident_id, content_hash, prompt_version, model,
                 request_status, started_at, updated_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?)
            ''', (*key, current_stamp, current_stamp))
        conn.commit()
        return "claimed"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_ai_analysis(
    incident_id,
    content_hash,
    prompt_version,
    model,
    analysis,
    *,
    raw_response="",
    now=None,
):
    key = _ai_key(incident_id, content_hash, prompt_version, model)
    stamp = _timestamp(now)
    payload = (
        json.dumps(analysis, ensure_ascii=False, separators=(",", ":"))
        if analysis is not None
        else None
    )
    conn = get_conn()
    try:
        with conn:
            cursor = conn.execute('''
                UPDATE ai_analyses
                SET request_status = 'completed', analysis_json = ?, raw_response = ?,
                    last_error_code = '', completed_at = ?, updated_at = ?
                WHERE incident_id = ? AND content_hash = ?
                  AND prompt_version = ? AND model = ?
            ''', (payload, str(raw_response or ""), stamp, stamp, *key))
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError("AI analysis was not claimed")
    finally:
        conn.close()


def fail_ai_analysis(
    incident_id,
    content_hash,
    prompt_version,
    model,
    error_code,
    *,
    now=None,
):
    key = _ai_key(incident_id, content_hash, prompt_version, model)
    stamp = _timestamp(now)
    conn = get_conn()
    try:
        with conn:
            cursor = conn.execute('''
                UPDATE ai_analyses
                SET request_status = 'failed', last_error_code = ?, updated_at = ?
                WHERE incident_id = ? AND content_hash = ?
                  AND prompt_version = ? AND model = ?
            ''', (str(error_code), stamp, *key))
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError("AI analysis was not claimed")
    finally:
        conn.close()


def get_ai_states(keys):
    normalized = [_ai_key(*key) for key in keys]
    if not normalized:
        return {}
    values_sql = ", ".join(["(?, ?, ?, ?)"] * len(normalized))
    params = [value for key in normalized for value in key]
    conn = get_conn()
    try:
        rows = conn.execute(f'''
            WITH wanted(incident_id, content_hash, prompt_version, model) AS (
                VALUES {values_sql}
            )
            SELECT a.*
            FROM ai_analyses AS a
            INNER JOIN wanted AS w
              ON a.incident_id = w.incident_id
             AND a.content_hash = w.content_hash
             AND a.prompt_version = w.prompt_version
             AND a.model = w.model
        ''', params).fetchall()
        return {
            row["incident_id"]: _read_ai_row(row)
            for row in rows
        }
    finally:
        conn.close()

def save_history_entries(protocol_file, entries):
    """
    entries: list of dicts:
      incident_id, tag, comment, executor, description, solution, reason
    """
    if not entries:
        return

    check_date = datetime.now().strftime('%d.%m.%Y')
    conn = get_conn()
    try:
        with conn:
            for e in entries:
                conn.execute('''
                    INSERT OR REPLACE INTO incident_history
                    (incident_id, check_date, protocol_file, tag, comment, executor,
                     description, solution, reason, fix_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    str(e.get('incident_id', '')).upper(),
                    check_date,
                    protocol_file,
                    e.get('tag', '') or '',
                    e.get('comment', '') or '',
                    e.get('executor', '') or '',
                    e.get('description', '') or '',
                    e.get('solution', '') or '',
                    e.get('reason', '') or '',
                    e.get('fix_status', '') or '',
                ))
    finally:
        conn.close()

def get_history_for_incident(incident_id):
    """Все проверки по ID, от новых к старым"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM incident_history
        WHERE incident_id = ?
        ORDER BY created_at DESC, id DESC
    ''', (str(incident_id).upper(),))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_all_repeated_grouped():
    """
    Инциденты, которые встречались больше одного раза,
    сгруппированные по protocol_file / check_date.
    Пока используется на следующих этапах.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        SELECT incident_id, COUNT(*) as cnt
        FROM incident_history
        GROUP BY incident_id
        HAVING cnt > 1
    ''')
    repeated_ids = {r['incident_id'] for r in c.fetchall()}

    if not repeated_ids:
        conn.close()
        return {}

    placeholders = ','.join('?' * len(repeated_ids))
    c.execute(f'''
        SELECT * FROM incident_history
        WHERE incident_id IN ({placeholders})
        ORDER BY check_date DESC, created_at DESC
    ''', tuple(repeated_ids))

    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    grouped = {}
    for r in rows:
        key = r.get('check_date') or r.get('protocol_file')
        grouped.setdefault(key, []).append(r)
    return grouped

def search_by_comment(query):
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM incident_history
        WHERE comment LIKE ?
        ORDER BY created_at DESC
    ''', (f'%{query}%',))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_all_history_ids():
    """Множество всех incident_id, которые когда-либо были в протоколах"""
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT DISTINCT incident_id FROM incident_history')
    ids = {str(r[0]).upper() for r in c.fetchall()}
    conn.close()
    return ids

def get_history_grouped_by_date():
    """
    Все записи истории, сгруппированные по check_date.
    { '07.08.2026': [row, row, ...], ... }
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM incident_history
        ORDER BY created_at DESC, id DESC
    ''')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    grouped = {}
    for r in rows:
        key = r.get('check_date') or r.get('protocol_file') or 'Без даты'
        grouped.setdefault(key, []).append(r)
    return grouped
