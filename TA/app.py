from flask import Flask, render_template, request, redirect, url_for, send_file
import pandas as pd
import re
import os
from collections import Counter
from io import BytesIO
from pathlib import Path
from werkzeug.utils import secure_filename

TA_DIR = Path(__file__).resolve().parent
BASE_DIR = TA_DIR.parent
app = Flask(__name__, template_folder=str(TA_DIR / 'templates'))
app.config['UPLOAD_FOLDER'] = str(BASE_DIR / 'cache' / 'ta_incident_auditor' / 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

incidents = []

# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================
def is_test_incident(inc):
    """Определяет тестовые / тестовые стендовые инциденты"""
    ts = str(inc.get('Тип стенда', '')).upper()
    if any(x in ts for x in ['MAJOR-GO', 'MAJOR-CHECK']):
        return True
   
    desc = str(inc.get('Описание', ''))
    if re.search(r'\b(ts|tv|tsl|tst)[a-z0-9-]{4,}\b', desc, re.I):
        return True
   
    return False

def extract_jira_links(text):
    patterns = [
        (r'(OPLOT-\d+)', 'https://jira.delta.sbrf.ru/browse/{}'),
        (r'(SMECLM-\d+)', 'https://jira.sberbank.ru/browse/{}'),
        (r'(SMECSC-\d+)', 'https://jira.delta.sbrf.ru/browse/{}'),
        (r'(EMRM-\d+)', 'https://jira.sberbank.ru/browse/{}'),
    ]
    links = {}
    for pattern, base_url in patterns:
        for match in re.findall(pattern, text, re.I):
            links[match.upper()] = base_url.format(match.upper())
    return links

def analyze_chronology(sol):
    times = re.findall(r'\b(\d{1,2}:\d{2})\b', sol)
    remarks = []
    if len(times) >= 2:
        for i in range(1, len(times)):
            try:
                prev = int(times[i-1].split(':')[0]) * 60 + int(times[i-1].split(':')[1])
                curr = int(times[i].split(':')[0]) * 60 + int(times[i].split(':')[1])
                diff = curr - prev
                if diff > 60:
                    remarks.append(f"Большой разрыв в хронологии ({diff//60} ч {diff%60} мин) между {times[i-1]} и {times[i]}")
            except:
                pass
    return remarks

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

# ====================== ОСНОВНОЙ АНАЛИЗ ======================
def analyze_incident(inc):
    sol = str(inc.get('Решение', ''))
    desc = str(inc.get('Описание', ''))
   
    what_match = re.search(r'Проблема[:\s]*(.+?)(?:\n|$)', desc, re.I)
    what = what_match.group(1).strip() if what_match else desc.split('\n')[0][:180]
   
    why_match = re.search(r'Причина[:\s]*(.+?)(?:\n|$)', sol, re.I)
    why = why_match.group(1).strip() if why_match else "Не указано"
   
    comp = []
    if re.search(r'Оплот', sol, re.I): comp.append("Оплот")
    if re.search(r'ЗПИ', sol, re.I): comp.append("ЗПИ")
    if re.search(r'администратор', sol, re.I): comp.append("Администраторы")
    competencies = " / ".join(comp) if comp else "Не указано"
   
    ticket = re.search(r'(OPLOT-\d+|JIRA-\d+)', sol, re.I)
    steps = f"Выполнены работы в рамках тикета {ticket.group(1)}" if ticket else "Выполнены работы"
   
    has_start = bool(re.search(r'(Время начала|Фактическое время возникновения|начало инцидента)', sol, re.I))
    has_end = bool(re.search(r'(Время устранения|Фактическое время окончания|время окончания|окончания инцидента)', sol, re.I))
   
    chronology_remarks = analyze_chronology(sol)
    has_chronology = bool(re.search(r'хронология|краткая хронология', sol, re.I) or re.search(r'\d{2}:\d{2}', sol))
   
    gaps = []
    if not why or len(why) < 10: gaps.append("Причина")
    if not has_start: gaps.append("Время начала")
    if not has_end: gaps.append("Время окончания")
    if not has_chronology: gaps.append("Хронология")
    gaps.extend(chronology_remarks)
   
    reason_issue = check_reason_quality(why)
    if reason_issue:
        gaps.append(reason_issue)
   
    if len(gaps) == 0:
        status = "Инцидент закрыт корректно"
    else:
        status = "Есть замечания"
   
    return {
        'Статус': status,
        'Что произошло': what,
        'Почему произошло': why,
        'Привлечённые компетенции': competencies,
        'Ход устранения': steps,
        'Дата начала': 'Да' if has_start else 'Нет',
        'Дата окончания': 'Да' if has_end else 'Нет',
        'Замечания': gaps,
        'Jira_links': extract_jira_links(sol),
        'Affected_systems': extract_affected_systems(sol + desc),
        'Problem_types': extract_problem_types(sol + desc)
    }

# ====================== РОУТЫ ======================
@app.route('/', methods=['GET', 'POST'])
def index():
    global incidents
    problem_stats = []

    if request.method == 'POST':
        file = request.files.get('file')
        if file and file.filename:
            filename = secure_filename(file.filename) or 'uploaded.xlsx'
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            df = pd.read_excel(filepath)
            incidents = [dict(row) for _, row in df.iterrows()]
            return redirect(url_for('index'))
   
    # Подсчёт типов проблем
    problem_counter = Counter()
    for inc in incidents:
        if not is_test_incident(inc) and str(inc.get('Статус', '')) != "В работе":
            analysis = analyze_incident(inc)
            for ptype in analysis.get('Problem_types', []):
                problem_counter[ptype] += 1
   
    if problem_counter:
        problem_stats = sorted(problem_counter.items(), key=lambda x: x[1], reverse=True)

    filtered = incidents
    search = request.args.get('search', '').lower()
    if search:
        filtered = [inc for inc in filtered if search in str(inc.get('ID инцидента', '')).lower() or
                    search in str(inc.get('Исполнитель', '')).lower()]
   
    correct = []
    remarks = []
    in_work = []
    test = []
   
    for inc in filtered:
        inc_copy = inc.copy()
        status_field = str(inc.get('Статус', ''))
       
        if status_field == "В работе":
            in_work.append(inc_copy)
        elif is_test_incident(inc):
            test.append(inc_copy)
        else:
            inc_copy['analysis'] = analyze_incident(inc)
            status_text = inc_copy['analysis']['Статус'].lower()
            if "корректно" in status_text:
                correct.append(inc_copy)
            else:
                remarks.append(inc_copy)
   
    return render_template('index.html',
                           correct=correct,
                           remarks=remarks,
                           in_work=in_work,
                           test=test,
                           problem_stats=problem_stats)

@app.route('/incident/<inc_id>')
def incident_detail(inc_id):
    inc = next((i for i in incidents if str(i.get('ID инцидента', '')) == inc_id), None)
    if not inc:
        return "Инцидент не найден", 404
   
    inc_copy = inc.copy()
    if str(inc.get('Статус', '')) == "В работе" or is_test_incident(inc):
        inc_copy['analysis'] = {"Статус": "Анализ не проводится для данного типа инцидента"}
    else:
        inc_copy['analysis'] = analyze_incident(inc)
   
    # Сохраняем текущую вкладку для возврата
    current_tab = request.args.get('tab', 'correct')
    return render_template('detail.html', inc=inc_copy, current_tab=current_tab)

@app.route('/export')
def export():
    if not incidents:
        return "Нет данных"
    data = []
    for inc in incidents:
        if str(inc.get('Статус', '')) == "В работе":
            status = "В работе"
        elif is_test_incident(inc):
            status = "Тестовый"
        else:
            analysis = analyze_incident(inc)
            status = analysis['Статус']
        data.append({
            'ID инцидента': inc.get('ID инцидента', ''),
            'Исполнитель': inc.get('Исполнитель', ''),
            'Статус аудита': status,
        })
    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name="audit_result.xlsx", as_attachment=True)

if __name__ == '__main__':
    host = os.getenv('SANDBOX_HOST', '127.0.0.1')
    port = int(os.getenv('SANDBOX_PORT', '3535'))
    app.run(host=host, port=port, debug=False)
