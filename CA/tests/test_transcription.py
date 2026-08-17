from __future__ import annotations

import json

from zpi_app.analyzers.transcription import (
    SVA_CLASS,
    analyze_transcription,
)
from zpi_app.parsers import LogRecord


MEETING_ID = "70F78AD12F724E44AF9AF8CC4673D5CC"
MAIN_UUID = "421a38e5-d74d-4626-af7f-c1c2ff073333"
FINAL_UUID = "2597562e-d01c-4136-8af0-ce17b9ed3333"


def chunk(row: int, uuid: str, part: int, time: str, last: bool = False) -> LogRecord:
    meta = {
        "uuid": uuid,
        "part": part,
        "user": {
            "meetingId": MEETING_ID,
            "focus": {"isLastRecord": last},
        },
    }
    message = (
        f'Сообщение нового формата отправлено в Kafka SVA, topic: record_focus,key: {uuid},'
        f'headers: Meta: {json.dumps(meta, ensure_ascii=False, separators=(",", ":"))}'
    )
    return LogRecord(
        "message.txt",
        row,
        {"className": SVA_CLASS, "serverEventDatetime": time, "message": message},
    )


def voice(row: int, value: bool, time: str) -> LogRecord:
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


def activity(row: int, fact_start_date: str, time: str, *, include_meeting: bool = False) -> LogRecord:
    meeting_fragment = f' meetingId={MEETING_ID}' if include_meeting else ""
    return LogRecord(
        "activity.txt",
        row,
        {
            "className": "ru.sber.LoggingAspect",
            "serverEventDatetime": time,
            "message": (
                "SBER_CRM_GET_TASK_DETAIL.Получение детальной карточки задачи(активности)"
                f'в SberCRM.Ответ:{meeting_fragment} {{\"factStartDate\":\"{fact_start_date}T12:00:00\"}}'
            ),
        },
    )


def test_analysis_selects_largest_group_and_expert_part_two_time():
    records = [
        chunk(1, MAIN_UUID, 0, "2026-08-03T09:41:53.602Z"),
        chunk(2, MAIN_UUID, 1, "2026-08-03T09:42:03.258Z"),
        chunk(3, MAIN_UUID, 2, "2026-08-03T09:42:30.565Z"),
        chunk(4, FINAL_UUID, 0, "2026-08-03T10:21:20.470Z", last=True),
        voice(5, False, "2026-08-03T10:21:21.043Z"),
    ]

    result = analyze_transcription(records, MEETING_ID.lower())

    assert result.success is True
    assert result.selected_group.uuid == MAIN_UUID
    assert result.selected_group.representative.part == 2
    assert result.selected_group.representative.local_time == "2026-08-03T12:42:30"
    assert result.selected_response.value is False
    assert f'Ответ: {{"{MEETING_ID}":false}}' in result.ticket_text
    assert "uuid =\n" in result.ticket_text
    assert f'Meta: {{"uuid":"{MAIN_UUID}"}}' in result.ticket_text
    assert f'Meta: {{"uuid":"{FINAL_UUID}"}}' in result.ticket_text
    assert result.ticket_text.index(MAIN_UUID) < result.ticket_text.index(FINAL_UUID)
    assert any("все найденные UUID" in warning for warning in result.warnings)


def test_analysis_deduplicates_overlapping_exports():
    item = voice(5, False, "2026-08-03T10:21:21.043Z")
    duplicate = LogRecord("message.txt", 99, dict(item.data))

    result = analyze_transcription([item, duplicate], MEETING_ID)

    assert len(result.voice_responses) == 1


def test_analysis_reports_missing_data_without_inventing_ticket():
    result = analyze_transcription([], MEETING_ID)

    assert result.success is False
    assert result.outcome == "chunks_missing"
    assert result.conclusion_title == "Чанки записи не найдены"
    assert "Дата начала активности" in result.conclusion_text
    assert "не найдена" in result.conclusion_text
    assert "ошибка с микрофоном" in result.conclusion_text
    assert result.ticket_text is None
    assert len(result.warnings) == 3


def test_missing_chunks_uses_fact_start_date_for_file_check():
    result = analyze_transcription(
        [activity(1, "2026-08-03", "2026-08-03T09:40:00.000Z")],
        MEETING_ID,
    )

    assert result.outcome == "chunks_missing"
    assert result.selected_activity is not None
    assert result.selected_activity.fact_start_date == "2026-08-03"
    assert result.missing_chunks_steps == [
        "Дата начала активности 2026-08-03. Сравните дату и загрузите новый файл.",
        "Возможно, запись отсутствует или произошла ошибка с микрофоном.",
    ]
    assert "1. Дата начала активности 2026-08-03" in result.conclusion_text
    assert result.ticket_text is None


def test_missing_chunks_does_not_guess_between_conflicting_activity_dates():
    result = analyze_transcription(
        [
            activity(1, "2026-08-03", "2026-08-03T09:40:00.000Z"),
            activity(2, "2026-08-04", "2026-08-04T09:40:00.000Z"),
        ],
        MEETING_ID,
    )

    assert result.selected_activity is None
    assert result.activity_dates == ["2026-08-03", "2026-08-04"]
    assert "несколько дат начала активности" in result.missing_chunks_steps[0]
    assert any("автоматически выбрать" in warning for warning in result.warnings)


def test_true_response_recommends_rechecking_but_keeps_optional_ticket():
    result = analyze_transcription(
        [
            chunk(1, MAIN_UUID, 2, "2026-08-03T09:42:30.565Z"),
            voice(2, True, "2026-08-03T10:21:21.043Z"),
        ],
        MEETING_ID,
    )

    assert result.success is True
    assert result.outcome == "transcription_found"
    assert "перепроверить сформированное резюме" in result.conclusion_text
    assert result.ticket_text is not None
    assert f'Ответ: {{"{MEETING_ID}":true}}' in result.ticket_text
