from __future__ import annotations

import re
from dataclasses import dataclass

from ..parsers import LogRecord
from .transcription import SERVICE, WORK_GROUP, VoiceEvidence, normalize_meeting_id


TASK_DETAIL_MARKER = "SBER_CRM_GET_TASK_DETAIL"
CALL_VOICE_MARKER = "VOICE360_CHECK_TRANSCRIPTION"
MEETING_VOICE_MARKER = "VOICE360_CHECK_MEETINGS_TRANSCRIPTION"
SVA_CLASS = "ru.sber.mmb.emrm.utilities.da.client.smart.sva.SvaKafkaProducer"

TASK_TYPE_CALLING_RE = re.compile(
    r'["\']?taskTypeCode["\']?\s*:\s*["\']?CALLING["\']?',
    re.I,
)
FACT_START_DATETIME_RE = re.compile(
    r'["\']?factStartDate["\']?\s*:\s*["\']?'
    r'(?P<value>\d{4}-\d{2}-\d{2}(?:T[0-9:.+\-]+)?)',
    re.I,
)
CALL_VOICE_RESULT_RE = re.compile(
    r'VOICE360_CHECK_TRANSCRIPTION.*?Ответ:\s*'
    r'\{\s*["\']?data["\']?\s*:\s*\{\s*'
    r'["\']?(?P<activity>[0-9A-F]{32})["\']?\s*:\s*'
    r'(?P<value>true|false)\s*\}\s*\}',
    re.I | re.S,
)


def _normalized_message(message: str) -> str:
    return message.replace('\\"', '"').replace("\\'", "'")


def _timestamp_key(value: str | None) -> float:
    if not value:
        return float("-inf")
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    return parsed.timestamp()


def _excerpt(message: str, limit: int = 260) -> str:
    compact = re.sub(r"\s+", " ", message).strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


@dataclass(frozen=True)
class CallActivityEvidence:
    fact_start_date: str
    server_time: str | None
    local_time: str | None
    source: str
    message_excerpt: str
    meeting_match: bool
    task_type_code: str = "CALLING"


@dataclass
class CallAnalysis:
    meeting_id: str
    total_records: int
    matched_records: int
    voice_responses: list[VoiceEvidence]
    selected_response: VoiceEvidence | None
    activity_evidence: list[CallActivityEvidence]
    selected_activity: CallActivityEvidence | None
    warnings: list[str]
    ticket_text: str | None

    # Совместимый интерфейс с анализатором встречи для общего шаблона.
    groups: tuple = ()
    selected_group = None
    selection_explanation = None

    @property
    def scenario(self) -> str:
        return "call"

    @property
    def scenario_label(self) -> str:
        return "Звонок"

    @property
    def task_type_code(self) -> str:
        return "CALLING"

    @property
    def success(self) -> bool:
        return self.selected_response is not None and self.selected_activity is not None

    @property
    def outcome(self) -> str:
        if self.selected_response is None:
            return "voice_missing"
        if self.selected_activity is None:
            return "activity_missing"
        if any(item.value for item in self.voice_responses):
            return "transcription_found"
        return "transcription_missing"

    @property
    def conclusion_title(self) -> str:
        return {
            "voice_missing": "Ответ VOICE360 по звонку не найден",
            "activity_missing": "Дата начала звонка не определена",
            "transcription_found": "Транскрибация звонка сформирована",
            "transcription_missing": "Транскрибация звонка не найдена",
        }[self.outcome]

    @property
    def conclusion_text(self) -> str:
        return {
            "voice_missing": (
                "Сценарий CALLING определён, но в загруженных логах не найден ответ "
                "VOICE360_CHECK_TRANSCRIPTION для указанного ID."
            ),
            "activity_missing": (
                "Сценарий CALLING и ответ VOICE360 найдены, но дату начала звонка "
                "factStartDate нельзя определить однозначно. Нужны логи детальной карточки "
                "нужной активности из SBER_CRM_GET_TASK_DETAIL."
            ),
            "transcription_found": (
                "Хотя бы один ответ VOICE360 равен true. Отправьте пользователя "
                "перепроверить транскрибацию по звонку; если проблема сохраняется, "
                "ЗПИ на смежную РГ всё равно можно сформировать."
            ),
            "transcription_missing": (
                "Все найденные ответы VOICE360 по звонку равны false. Данных достаточно "
                "для формирования ЗПИ на смежную систему."
            ),
        }[self.outcome]

    @property
    def missing_chunks_steps(self) -> list[str]:
        return []


def is_call_scenario(records: list[LogRecord], activity_id: str) -> bool:
    """Определяет CALLING, не перехватывая очевидный сценарий встречи."""
    normalized_id = re.sub(r"[\s-]", "", activity_id or "").upper()
    call_voice_for_id = False
    meeting_evidence_for_id = False
    calling_for_id = False
    any_calling_task = False

    for record in records:
        message = str(record.get("message", ""))
        normalized = _normalized_message(message)
        upper = normalized.upper()

        call_match = CALL_VOICE_RESULT_RE.search(normalized)
        if call_match and call_match.group("activity").upper() == normalized_id:
            call_voice_for_id = True

        if normalized_id and normalized_id in upper:
            if MEETING_VOICE_MARKER in upper:
                meeting_evidence_for_id = True
            if (
                str(record.get("className", "")) == SVA_CLASS
                and "TOPIC: RECORD_FOCUS" in upper
            ):
                meeting_evidence_for_id = True

        if TASK_DETAIL_MARKER in upper and TASK_TYPE_CALLING_RE.search(normalized):
            any_calling_task = True
            if normalized_id and normalized_id in upper:
                calling_for_id = True

    if call_voice_for_id:
        return True
    if meeting_evidence_for_id:
        return False
    if calling_for_id:
        return True
    return any_calling_task


def _select_activity(
    evidence: list[CallActivityEvidence],
) -> CallActivityEvidence | None:
    if not evidence:
        return None
    id_specific = [item for item in evidence if item.meeting_match]
    candidates = id_specific or evidence
    values = {item.fact_start_date for item in candidates}
    if len(values) != 1:
        return None
    return max(candidates, key=lambda item: _timestamp_key(item.server_time))


def _build_ticket(
    activity_id: str,
    activity: CallActivityEvidence,
    response: VoiceEvidence,
) -> str:
    result = "true" if response.value else "false"
    response_line = (
        f'{CALL_VOICE_MARKER}. Проверка наличия транскрибации по звонку. '
        f'Ответ: {{"data":{{"{activity_id}":{result}}}}}'
    )
    return "\n".join(
        [
            f"Услуга: {SERVICE}",
            f"РГ: {WORK_GROUP}",
            "Текст:",
            "Добрый день, коллеги.",
            "В АС ФОКУС пользователь не видит транскрибацию по звонку.",
            "Просьба проверить на своей стороне наличие транскрибации или причину возникновения ошибки.",
            "В логах ЕФС:",
            response_line,
            f"дата/время записи = {activity.fact_start_date}",
            f"id встречи = {activity_id}",
        ]
    )


def analyze_call(
    records: list[LogRecord],
    activity_id: str,
) -> CallAnalysis:
    normalized_id = normalize_meeting_id(activity_id)
    responses: list[VoiceEvidence] = []
    activity_evidence: list[CallActivityEvidence] = []
    matched_locations: set[tuple[str, int, str | None]] = set()
    seen_voice: set[tuple[str, str]] = set()
    seen_activity: set[tuple[str, str, str]] = set()

    for record in records:
        message = str(record.get("message", ""))
        normalized = _normalized_message(message)
        upper = normalized.upper()

        if TASK_DETAIL_MARKER in upper and TASK_TYPE_CALLING_RE.search(normalized):
            fact_match = FACT_START_DATETIME_RE.search(normalized)
            if fact_match:
                fact_start_date = fact_match.group("value")
                server_time = str(record.get("serverEventDatetime", "")) or None
                fingerprint = (fact_start_date, server_time or "", normalized)
                if fingerprint not in seen_activity:
                    seen_activity.add(fingerprint)
                    activity_evidence.append(
                        CallActivityEvidence(
                            fact_start_date=fact_start_date,
                            server_time=server_time,
                            local_time=None,
                            source=record.location,
                            message_excerpt=_excerpt(message),
                            meeting_match=normalized_id in upper,
                        )
                    )
                    matched_locations.add(
                        (record.source_name, record.row_number, record.sheet_name)
                    )

        call_match = CALL_VOICE_RESULT_RE.search(normalized)
        if call_match and call_match.group("activity").upper() == normalized_id:
            server_time = str(record.get("serverEventDatetime", "")) or None
            fingerprint = (server_time or "", normalized)
            if fingerprint in seen_voice:
                continue
            seen_voice.add(fingerprint)
            responses.append(
                VoiceEvidence(
                    value=call_match.group("value").casefold() == "true",
                    server_time=server_time,
                    local_time=None,
                    source=record.location,
                    message=normalized.split("Start:", 1)[0].strip(),
                )
            )
            matched_locations.add((record.source_name, record.row_number, record.sheet_name))

    responses.sort(key=lambda item: _timestamp_key(item.server_time))
    activity_evidence.sort(key=lambda item: _timestamp_key(item.server_time))
    selected_activity = _select_activity(activity_evidence)

    true_responses = [item for item in responses if item.value]
    selected_response = true_responses[-1] if true_responses else (responses[-1] if responses else None)

    warnings: list[str] = []
    if not responses:
        warnings.append(
            "Не найден ответ VOICE360_CHECK_TRANSCRIPTION по указанному ID звонка."
        )
    elif len({item.value for item in responses}) > 1:
        warnings.append(
            "В логах есть ответы VOICE360 и true, и false. По экспертному правилу "
            "наличие хотя бы одного true имеет приоритет: рекомендована перепроверка."
        )
    elif len(responses) > 1:
        warnings.append(
            f"Найдено несколько ответов VOICE360 ({len(responses)}); все они учтены в выводе."
        )

    if not activity_evidence:
        warnings.append(
            "В записи SBER_CRM_GET_TASK_DETAIL с taskTypeCode=CALLING не найден factStartDate."
        )
    elif selected_activity is None:
        warnings.append(
            "Для CALLING найдено несколько разных factStartDate; автоматически выбрать "
            "дату звонка без риска ошибки нельзя."
        )

    ticket_text = (
        _build_ticket(normalized_id, selected_activity, selected_response)
        if selected_activity is not None and selected_response is not None
        else None
    )

    return CallAnalysis(
        meeting_id=normalized_id,
        total_records=len(records),
        matched_records=len(matched_locations),
        voice_responses=responses,
        selected_response=selected_response,
        activity_evidence=activity_evidence,
        selected_activity=selected_activity,
        warnings=warnings,
        ticket_text=ticket_text,
    )
