from __future__ import annotations

from datetime import datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Flask, jsonify, render_template, request, send_file, url_for

from .mpr_service import (
    MPR_PACKAGES,
    MprError,
    build_archive_filename,
    build_mpr_package_preview,
    build_mpr_rows,
    build_output_filename,
    generate_mpr_docx,
    list_mpr_templates,
    normalize_mpr_package_codes,
    resolve_mpr_template,
    select_mpr_package_rows,
)


def _sandbox_root_url() -> str:
    """Return the Sandbox root while preserving reverse-proxy SCRIPT_NAME."""

    script_root = (request.script_root or "").rstrip("/")
    mount_suffix = "/mm/mpr"
    if script_root.endswith(mount_suffix):
        script_root = script_root[: -len(mount_suffix)]
    return f"{script_root}/" if script_root else "/"


def _build_ui_config(templates) -> dict:
    available_templates = list(templates or [])
    initial_template_code = ""
    if available_templates:
        initial_template_code = str(available_templates[0].get("code") or "")
    return {
        "urls": {
            "preview": url_for("mpr_preview"),
            "generate": url_for("mpr_generate"),
        },
        "initial_template_code": initial_template_code,
    }


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.setdefault("MAX_CONTENT_LENGTH", 100 * 1024 * 1024)

    @app.get("/")
    def mpr_page():
        templates = list_mpr_templates()
        return render_template(
            "mpr.html",
            templates=templates,
            mpr_ui_config=_build_ui_config(templates),
            sandbox_root_url=_sandbox_root_url(),
        )

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "sandbox-mm-mpr"})

    @app.post("/preview")
    def mpr_preview():
        template_code = (request.form.get("template_code") or "").strip()
        files = request.files.getlist("files")

        try:
            resolve_mpr_template(template_code)
            rows = build_mpr_rows(files)
            preview = build_mpr_package_preview(rows)
            return jsonify({"success": True, **preview})
        except MprError as exc:
            payload = {"success": False, "error": exc.message}
            if exc.details:
                payload["details"] = exc.details
            return jsonify(payload), 400
        except Exception as exc:
            app.logger.exception("MPR preview failed: %s", exc)
            return jsonify({"success": False, "error": "Не удалось проверить данные МПР"}), 500

    @app.post("/generate")
    def mpr_generate():
        template_code = (request.form.get("template_code") or "").strip()
        files = request.files.getlist("files")
        package_codes = request.form.getlist("packages")

        try:
            template_path, template_info = resolve_mpr_template(template_code)
            rows = build_mpr_rows(files)
            selected_codes = normalize_mpr_package_codes(package_codes)
            selected_rows = select_mpr_package_rows(rows, selected_codes)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            documents = []

            for code in selected_codes:
                package = MPR_PACKAGES[code]
                output = generate_mpr_docx(
                    template_path,
                    selected_rows[code],
                    location_label=package["label"],
                    package_code=code,
                )
                filename = build_output_filename(
                    template_info,
                    package_label=package["label"],
                    timestamp=timestamp,
                )
                documents.append((filename, output))

            if len(documents) == 1:
                filename, output = documents[0]
                return send_file(
                    output,
                    as_attachment=True,
                    download_name=filename,
                    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )

            archive = BytesIO()
            with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
                for filename, output in documents:
                    zip_file.writestr(filename, output.getvalue())
            archive.seek(0)
            return send_file(
                archive,
                as_attachment=True,
                download_name=build_archive_filename(template_info, timestamp=timestamp),
                mimetype="application/zip",
            )
        except MprError as exc:
            payload = {"success": False, "error": exc.message}
            if exc.details:
                payload["details"] = exc.details
            return jsonify(payload), 400
        except Exception as exc:
            app.logger.exception("MPR generation failed: %s", exc)
            return jsonify({"success": False, "error": "Не удалось сформировать DOCX МПР"}), 500

    return app
