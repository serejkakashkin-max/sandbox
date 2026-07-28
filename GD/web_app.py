import os
import urllib3
from datetime import datetime
from flask import Flask, jsonify, render_template, request, send_file
from apscheduler.schedulers.background import BackgroundScheduler

# Отключаем предупреждения SSL, чтобы внутренние запросы в Сбере не падали
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .export_service import (
    build_full_raw_snapshot_excel,
    build_monthly_summary_excel,
    build_releases_excel,
)
from .release_cache_fetcher import refresh_snapshot_from_source
from .snapshot_service import (
    build_dashboard_pivots_2026,
    build_monthly_release_series,
    build_summary_metrics,
    filter_records,
    get_cache_info,
    get_release_records,
    load_snapshot,
)

app = Flask(__name__)
scheduler = BackgroundScheduler()


def _auto_refresh_enabled():
    return os.getenv("GD_AUTO_REFRESH_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def start_scheduler():
    if not _auto_refresh_enabled():
        return False
    if scheduler.running:
        return True
    scheduler.add_job(
        refresh_snapshot_from_source,
        "interval",
        minutes=5,
        max_instances=1,
        replace_existing=True,
        id="refresh_snapshot_every_5_minutes",
    )
    scheduler.start()
    return True


@app.route("/")
def index():
    snapshot_data = load_snapshot()
    all_records = get_release_records(snapshot_data=snapshot_data)
    
    selected_query = request.args.get("q", "").strip()
    selected_project = request.args.get("project", "").strip()
    selected_responsible = request.args.get("responsible", "").strip()
    selected_year_raw = request.args.get("year", "").strip()
    selected_year = int(selected_year_raw) if selected_year_raw.isdigit() else None
    
    # НОВОЕ ПОЛЕ: Читаем месяц из URL (например, "?month=2026-08")
    selected_month = request.args.get("month", "").strip()
    
    # ОБНОВЛЕНИЕ: Передаем месяц в функцию фильтрации
    records = filter_records(
        all_records,
        query=selected_query,
        year=selected_year,
        project=selected_project or None,
        responsible=selected_responsible or None,
        month=selected_month or None,
    )
    
    monthly_series = build_monthly_release_series(all_records, start="2025-01-01")
    summary = build_summary_metrics(all_records)
    cache_info = get_cache_info(snapshot_data=snapshot_data)
    pivot_data_2026 = build_dashboard_pivots_2026(all_records)
    
    years = sorted({r["year"] for r in all_records if r.get("year")}, reverse=True)
    projects = sorted({r["project"] for r in all_records if r.get("project")})
    responsibles = sorted({r["owner"] for r in all_records if r.get("owner")})
    
    return render_template(
        "main_app.html",
        records=records,
        summary=summary,
        cache_info=cache_info,
        generated_at=datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        selected_query=selected_query,
        selected_year=selected_year,
        selected_project=selected_project,
        selected_responsible=selected_responsible,
        selected_month=selected_month,  # ДОБАВЛЕНО
        years=years,
        projects=projects,
        responsibles=responsibles,
        monthly_labels=monthly_series.get("labels", []),
        monthly_values=monthly_series.get("values", []),
        months_2026=pivot_data_2026.get("months_2026", []),
        owners_pivot_2026=pivot_data_2026.get("owners_pivot_2026", []),
        projects_pivot_2026=pivot_data_2026.get("projects_pivot_2026", []),
    )


@app.route("/force-refresh")
def force_refresh():
    """Принудительное обновление кэша прямо из браузера"""
    result = refresh_snapshot_from_source()
    return jsonify(result)


@app.route("/download/releases.xlsx")
def download_releases_excel():
    snapshot_data = load_snapshot()
    records = get_release_records(snapshot_data=snapshot_data)
    output = build_releases_excel(records)
    return send_file(
        output,
        as_attachment=True,
        download_name="release_monitor_releases.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/download/monthly-summary.xlsx")
def download_release_summary_excel():
    snapshot_data = load_snapshot()
    records = get_release_records(snapshot_data=snapshot_data)
    monthly_series = build_monthly_release_series(records, start="2025-01-01")
    output = build_monthly_summary_excel(monthly_series)
    return send_file(
        output,
        as_attachment=True,
        download_name="release_monitor_monthly_summary.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/download/raw-snapshot.xlsx")
def download_raw_snapshot_excel():
    snapshot_data = load_snapshot()
    records = get_release_records(snapshot_data=snapshot_data)
    output = build_full_raw_snapshot_excel(records)
    return send_file(
        output,
        as_attachment=True,
        download_name="release_monitor_raw_snapshot.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/debug")
def debug_info():
    snapshot_data = load_snapshot()
    records = get_release_records(snapshot_data=snapshot_data)
    monthly_series = build_monthly_release_series(records, start="2025-01-01")
    
    sample_raw = records[0]["raw"] if records else {}
    sample_normalized = records[0] if records else {}
    if "raw" in sample_normalized:
        sample_normalized = {
            k: v for k, v in sample_normalized.items() if k != "raw"
        }

    return jsonify(
        {
            "success": True,
            "cache_info": get_cache_info(snapshot_data=snapshot_data),
            "records_count": len(records),
            "monthly_series": monthly_series,
            "sample_normalized": sample_normalized,
            "sample_raw": sample_raw,
        }
    )


@app.route("/health")
def health():
    snapshot_data = load_snapshot()
    cache_info = get_cache_info(snapshot_data=snapshot_data)
    return jsonify(
        {
            "status": "ok",
            "source_name": cache_info.get("source_name"),
            "records_count": cache_info.get("records_count"),
            "updated_at": cache_info.get("updated_at"),
        }
    )


@app.route("/releases-only")
def releases_only():
    snapshot_data = load_snapshot()
    all_records = get_release_records(snapshot_data=snapshot_data)
    
    selected_month = request.args.get("month", "").strip()
    selected_project = request.args.get("project", "").strip()
    selected_responsible = request.args.get("responsible", "").strip()

    records = filter_records(
        all_records,
        month=selected_month or None,
        project=selected_project or None,
        responsible=selected_responsible or None,
    )
    
    return render_template(
        "releases_only.html",
        records=records,
        selected_month=selected_month,
        generated_at=datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
    )


if __name__ == "__main__":
    start_scheduler()
    app.run(debug=True, use_reloader=False)
    
