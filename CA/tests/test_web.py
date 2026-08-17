from __future__ import annotations

import html
import json
from io import BytesIO

from openpyxl import Workbook

from zpi_app import create_app
from zpi_app.analyzers.transcription import SVA_CLASS


MEETING_ID = "70F78AD12F724E44AF9AF8CC4673D5CC"
UUID = "421a38e5-d74d-4626-af7f-c1c2ff073333"


def make_app():
    return create_app({"TESTING": True, "SECRET_KEY": "test-secret"})


def test_health_endpoint():
    client = make_app().test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_index_renders_form():
    client = make_app().test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "Не сформировалось резюме встречи" in response.get_data(as_text=True)
    assert "Укажите ID из заявки" in response.get_data(as_text=True)
    assert 'name="meeting_id"' in response.get_data(as_text=True)
    assert "required" in response.get_data(as_text=True)


def test_post_analyzes_txt_and_renders_ticket():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "known-token"

    meta = {
        "uuid": UUID,
        "part": 2,
        "user": {
            "meetingId": MEETING_ID,
            "focus": {"isLastRecord": False},
        },
    }
    chunk_message = (
        f'Сообщение нового формата отправлено в Kafka SVA, topic: record_focus,key: {UUID},'
        f'headers: Meta: {json.dumps(meta, ensure_ascii=False, separators=(",", ":"))}'
    )
    voice_message = (
        "VOICE360_CHECK_MEETINGS_TRANSCRIPTION. Проверка наличия транскрибации по встрече. "
        f'Ответ: {{"{MEETING_ID}":false}}'
    )
    text = "\n".join(
        [
            f'"className":"{SVA_CLASS}","serverEventDatetime":"2026-08-03T09:42:30.565Z","message":{json.dumps(chunk_message, ensure_ascii=False)}',
            f'"className":"LoggingAspect","serverEventDatetime":"2026-08-03T10:21:21.043Z","message":{json.dumps(voice_message, ensure_ascii=False)}',
        ]
    ).encode("utf-8")

    response = client.post(
        "/",
        data={
            "csrf_token": "known-token",
            "meeting_id": MEETING_ID,
            "logs": (BytesIO(text), "message.txt"),
        },
        content_type="multipart/form-data",
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Транскрибация не найдена" in body
    assert UUID in body
    assert "2026-08-03T12:42:30" in body


def test_post_rejects_bad_csrf():
    client = make_app().test_client()
    response = client.post(
        "/",
        data={"csrf_token": "bad", "meeting_id": MEETING_ID},
    )
    assert response.status_code == 400
    assert "Сессия формы устарела" in response.get_data(as_text=True)


def test_post_without_meeting_id_is_rejected():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "required-token"

    text = '"message":"служебная запись"'.encode("utf-8")

    response = client.post(
        "/",
        data={
            "csrf_token": "required-token",
            "meeting_id": "",
            "logs": (BytesIO(text), "logs.txt"),
        },
        content_type="multipart/form-data",
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "ID встречи должен содержать 32" in body


def test_missing_chunks_shows_microphone_diagnosis_and_clickstream_hint():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "missing-chunks-token"

    voice_message = (
        "VOICE360_CHECK_MEETINGS_TRANSCRIPTION. Проверка наличия транскрибации по встрече. "
        f'Ответ: {{"{MEETING_ID}":false}}'
    )
    activity_message = (
        'SBER_CRM_GET_TASK_DETAIL.Получение детальной карточки задачи(активности)'
        'в SberCRM.Ответ: {"factStartDate":"2026-08-03T12:00:00"}'
    )
    text = "\n".join(
        [
            (
                '"className":"LoggingAspect",'
                '"serverEventDatetime":"2026-08-03T09:40:00.000Z",'
                f'"message":{json.dumps(activity_message, ensure_ascii=False)}'
            ),
            (
                '"className":"LoggingAspect",'
                '"serverEventDatetime":"2026-08-03T10:21:21.043Z",'
                f'"message":{json.dumps(voice_message, ensure_ascii=False)}'
            ),
        ]
    ).encode("utf-8")

    response = client.post(
        "/",
        data={
            "csrf_token": "missing-chunks-token",
            "meeting_id": MEETING_ID,
            "logs": (BytesIO(text), "logs.txt"),
        },
        content_type="multipart/form-data",
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Чанки записи не найдены" in body
    assert 'class="source-list diagnostic-steps"' in body
    assert "Дата начала активности 2026-08-03" in body
    assert "Сравните дату и загрузите новый файл" in body
    assert "произошла ошибка с микрофоном" in body
    assert "Дата начала активности из SberCRM" in body
    assert "табельный номер сотрудника" in body
    assert "https://clickstream.sberbank.ru/frontend/fokus/audience/web-profiles" in body
    assert "Текст заявки на смежную систему" not in body


def test_true_voice_response_recommends_recheck_and_offers_optional_ticket():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "true-token"

    meta = {
        "uuid": UUID,
        "part": 2,
        "user": {"meetingId": MEETING_ID, "focus": {"isLastRecord": True}},
    }
    chunk_message = (
        f'Сообщение нового формата отправлено в Kafka SVA, topic: record_focus,key: {UUID},'
        f'headers: Meta: {json.dumps(meta, ensure_ascii=False, separators=(",", ":"))}'
    )
    voice_message = (
        "VOICE360_CHECK_MEETINGS_TRANSCRIPTION. Проверка наличия транскрибации по встрече. "
        f'Ответ: {{"{MEETING_ID}":true}}'
    )
    text = "\n".join(
        [
            f'"className":"{SVA_CLASS}","serverEventDatetime":"2026-08-03T09:42:30.565Z","message":{json.dumps(chunk_message, ensure_ascii=False)}',
            f'"className":"LoggingAspect","serverEventDatetime":"2026-08-03T10:21:21.043Z","message":{json.dumps(voice_message, ensure_ascii=False)}',
        ]
    ).encode("utf-8")

    response = client.post(
        "/",
        data={
            "csrf_token": "true-token",
            "meeting_id": MEETING_ID,
            "logs": (BytesIO(text), "logs.txt"),
        },
        content_type="multipart/form-data",
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Транскрибация сформирована" in body
    assert "перепроверить сформированное резюме" in body
    assert "Сформировать ЗПИ" in body
    assert 'id="optional-ticket"' in body
    assert "is-hidden" in body
    assert f'Ответ: {{"{MEETING_ID}":true}}' in html.unescape(body)


def test_post_accepts_xlsx_log_table():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "xlsx-token"

    meta = {
        "uuid": UUID,
        "part": 2,
        "user": {"meetingId": MEETING_ID, "focus": {"isLastRecord": False}},
    }
    chunk_message = (
        f'Сообщение нового формата отправлено в Kafka SVA, topic: record_focus,key: {UUID},'
        f'headers: Meta: {json.dumps(meta, ensure_ascii=False, separators=(",", ":"))}'
    )
    voice_message = (
        "VOICE360_CHECK_MEETINGS_TRANSCRIPTION. Проверка наличия транскрибации по встрече. "
        f'Ответ: {{"{MEETING_ID}":false}}'
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["className", "serverEventDatetime", "message"])
    sheet.append([SVA_CLASS, "2026-08-03T09:42:30.565Z", chunk_message])
    sheet.append(["LoggingAspect", "2026-08-03T10:21:21.043Z", voice_message])
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    response = client.post(
        "/",
        data={
            "csrf_token": "xlsx-token",
            "meeting_id": MEETING_ID,
            "logs": (buffer, "logs.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert "Транскрибация не найдена" in response.get_data(as_text=True)


def test_mounted_ui_uses_oplot_theme_and_returns_to_sandbox():
    app = make_app()
    client = app.test_client()
    response = client.get(
        "/",
        environ_overrides={"SCRIPT_NAME": "/releases/sandbox/ca/zpi-assistant"},
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-theme="light"' in body
    assert 'data-bs-theme="light"' in body
    assert 'data-oplot-theme-toggle' in body
    assert '/releases/sandbox/ca/zpi-assistant/static/oplot-theme.js' in body
    assert 'href="/releases/sandbox/"' in body
    assert "CA · Частухин Александр" in body


def test_zpi_session_cookie_is_namespaced():
    app = make_app()
    response = app.test_client().get("/")
    cookie = response.headers.get("Set-Cookie", "")
    assert cookie.startswith("zpi_session=")
