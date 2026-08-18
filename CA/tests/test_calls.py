from __future__ import annotations

import html
import json
from io import BytesIO

from zpi_app import create_app
from zpi_app.analyzers import analyze_case
from zpi_app.parsers import LogRecord


CALL_ID = "6F419727672E4D78AD121ED36F794A6A"
MEETING_ID = "70F78AD12F724E44AF9AF8CC4673D5CC"


def call_activity(row: int, fact_start: str, time: str, *, include_id: bool = False) -> LogRecord:
    payload = {"taskTypeCode": "CALLING", "factStartDate": fact_start}
    if include_id:
        payload["activityId"] = CALL_ID
    return LogRecord(
        "activity.txt",
        row,
        {
            "className": "ru.sber.LoggingAspect",
            "serverEventDatetime": time,
            "message": (
                "SBER_CRM_GET_TASK_DETAIL.Получение детальной карточки задачи(активности)"
                "в SberCRM.Ответ: "
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            ),
        },
    )


def call_voice(row: int, value: bool, time: str) -> LogRecord:
    return LogRecord(
        "serviceName.txt",
        row,
        {
            "className": "ru.sber.LoggingAspect",
            "serverEventDatetime": time,
            "message": (
                "VOICE360_CHECK_TRANSCRIPTION. Проверка наличия транскрибации по звонку. "
                f'Ответ: {{"data":{{"{CALL_ID}":{str(value).lower()}}}}}'
            ),
        },
    )


def meeting_voice(row: int, value: bool, time: str) -> LogRecord:
    return LogRecord(
        "serviceName.txt",
        row,
        {
            "className": "ru.sber.LoggingAspect",
            "serverEventDatetime": time,
            "message": (
                "VOICE360_CHECK_MEETINGS_TRANSCRIPTION. Проверка наличия "
                f'транскрибации по встрече. Ответ: {{"{MEETING_ID}":{str(value).lower()}}}'
            ),
        },
    )


def test_call_false_builds_ticket_and_preserves_plus_three_offset():
    fact_start = "2026-08-03T17:29:34.926+03:00"
    result = analyze_case(
        [
            call_activity(1, fact_start, "2026-08-03T14:29:35Z", include_id=True),
            call_voice(2, False, "2026-08-03T14:30:00Z"),
        ],
        CALL_ID,
    )

    assert result.scenario == "call"
    assert result.task_type_code == "CALLING"
    assert result.outcome == "transcription_missing"
    assert result.selected_activity.fact_start_date == fact_start
    assert f'Ответ: {{"data":{{"{CALL_ID}":false}}}}' in result.ticket_text
    assert f"дата/время записи = {fact_start}" in result.ticket_text
    assert "20:29" not in result.ticket_text


def test_call_any_true_has_priority_over_later_false():
    result = analyze_case(
        [
            call_activity(1, "2026-08-03T17:29:34.926+03:00", "2026-08-03T14:29:35Z"),
            call_voice(2, True, "2026-08-03T14:30:00Z"),
            call_voice(3, False, "2026-08-03T14:31:00Z"),
        ],
        CALL_ID,
    )

    assert result.outcome == "transcription_found"
    assert result.selected_response.value is True
    assert "перепроверить транскрибацию по звонку" in result.conclusion_text
    assert f'Ответ: {{"data":{{"{CALL_ID}":true}}}}' in result.ticket_text


def test_call_does_not_guess_conflicting_fact_start_dates():
    result = analyze_case(
        [
            call_activity(1, "2026-08-03T17:29:34.926+03:00", "2026-08-03T14:29:35Z"),
            call_activity(2, "2026-08-04T10:00:00+03:00", "2026-08-04T07:00:01Z"),
            call_voice(3, False, "2026-08-04T07:01:00Z"),
        ],
        CALL_ID,
    )

    assert result.scenario == "call"
    assert result.selected_activity is None
    assert result.outcome == "activity_missing"
    assert result.ticket_text is None


def test_meeting_evidence_for_target_id_overrides_unrelated_calling_task():
    result = analyze_case(
        [
            call_activity(1, "2026-08-03T17:29:34.926+03:00", "2026-08-03T14:29:35Z"),
            meeting_voice(2, False, "2026-08-03T10:21:21Z"),
        ],
        MEETING_ID,
    )
    assert result.outcome == "chunks_missing"
    assert not hasattr(result, "task_type_code") or result.task_type_code is None


def _call_log_text(values: list[bool]) -> bytes:
    fact_start = "2026-08-03T17:29:34.926+03:00"
    activity = {
        "taskTypeCode": "CALLING",
        "factStartDate": fact_start,
        "activityId": CALL_ID,
    }
    rows = [
        json.dumps({
            "className": "LoggingAspect",
            "serverEventDatetime": "2026-08-03T14:29:35Z",
            "message": "SBER_CRM_GET_TASK_DETAIL.Получение детальной карточки задачи(активности)в SberCRM.Ответ: " + json.dumps(activity, ensure_ascii=False, separators=(",", ":")),
        }, ensure_ascii=False)
    ]
    for i, value in enumerate(values, 1):
        rows.append(json.dumps({
            "className": "LoggingAspect",
            "serverEventDatetime": f"2026-08-03T14:3{i}:00Z",
            "message": "VOICE360_CHECK_TRANSCRIPTION. Проверка наличия транскрибации по звонку. " + f'Ответ: {{"data":{{"{CALL_ID}":{str(value).lower()}}}}}',
        }, ensure_ascii=False))
    return "\n".join(rows).encode("utf-8")


def test_call_web_flow_renders_calling_and_optional_true_ticket():
    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "call-token"

    response = client.post(
        "/",
        data={
            "csrf_token": "call-token",
            "meeting_id": CALL_ID,
            "logs": (BytesIO(_call_log_text([False, True])), "call.txt"),
        },
        content_type="multipart/form-data",
    )
    body = html.unescape(response.get_data(as_text=True))

    assert response.status_code == 200
    assert "Результат анализа · Звонок" in body
    assert "CALLING" in body
    assert "2026-08-03T17:29:34.926+03:00" in body
    assert "Транскрибация звонка сформирована" in body
    assert 'id="optional-ticket"' in body
    assert f'Ответ: {{"data":{{"{CALL_ID}":true}}}}' in body
