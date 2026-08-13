from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..parsers import LogRecord


SVA_CLASS = "ru.sber.mmb.emrm.utilities.da.client.smart.sva.SvaKafkaProducer"
VOICE360_MARKER = "VOICE360_CHECK_MEETINGS_TRANSCRIPTION"
SERVICE = "ПКАП СС360.ИТ.Сервисы Цифрового Аватара (CI02736546)"
WORK_GROUP = "Сопровождение Сервисы Цифрового Аватара КИБ (Жилко Д. С.)"

UUID_RE = re.compile(r'Meta:\s*\{\s*"uuid"\s*:\s*"([0-9a-f-]{36})"', re.I)
KEY_RE = re.compile(r'topic:\s*record_focus\s*,\s*key:\s*([0-9a-f-]{36})', re.I)
PART_RE = re.compile(r'"part"\s*:\s*(\d+)', re.I)
LAST_RE = re.compile(r'"isLastRecord"\s*:\s*(true|false)', re.I)
VOICE_RESULT_RE = re.compile(
    r'Ответ:\s*\{\s*"(?P<meeting>[0-9A-F]{32})"\s*:\s*(?P<value>true|false)\s*\}',
    re.I,
)
MEETING_ID_RE = re.compile(r"^[0-9A-F]{32}$")


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _moscow_text(value: Any, *, with_seconds: bool = True) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    local = parsed.astimezone(ZoneInfo("Europe/Moscow"))
    return local.strftime("%Y-%m-%dT%H:%M:%S" if with_seconds else "%Y-%m-%dT%H:%M")


def _timestamp_key(value: Any) -> float:
    parsed = _parse_datetime(value)
    if parsed is None:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.timestamp()


def normalize_meeting_id(value: str) -> str:
    meeting_id = re.sub(r"[\s-]", "", value or "").upper()
    if not MEETING_ID_RE.fullmatch(meeting_id):
        raise ValueError("ID встречи должен содержать 32 шестнадцатеричных символа.")
    return meeting_id


@dataclass(frozen=True)
class ChunkEvidence:
    uuid: str
    part: int | None
    is_last_record: bool | None
    server_time: str | None
    local_time: str | None
    source: str
    message_excerpt: str


@dataclass
class ChunkGroup:
    uuid: str
    rows: list[ChunkEvidence] = field(default_factory=list)

    @property
    def parts(self) -> list[int]:
        return sorted({row.part for row in self.rows if row.part is not None})

    @property
    def first_local_time(self) -> str | None:
        values = [row.local_time for row in self.rows if row.local_time]
        return min(values) if values else None

    @property
    def last_local_time(self) -> str | None:
        values = [row.local_time for row in self.rows if row.local_time]
        return max(values) if values else None

    @property
    def has_final_marker(self) -> bool:
        return any(row.is_last_record is True for row in self.rows)

    @property
    def gaps(self) -> list[int]:
        if not self.parts:
            return []
        return sorted(set(range(self.parts[0], self.parts[-1] + 1)) - set(self.parts))

    @property
    def representative(self) -> ChunkEvidence:
        # В экспертном примере дата взята из part=2. Сохраняем это правило
        # явно и используем первый чанк как безопасный fallback.
        return next((row for row in self.rows if row.part == 2), self.rows[0])


@dataclass(frozen=True)
class VoiceEvidence:
    value: bool
    server_time: str | None
    local_time: str | None
    source: str
    message: str


@dataclass
class TranscriptionAnalysis:
    meeting_id: str
    total_records: int
    matched_records: int
    groups: list[ChunkGroup]
    selected_group: ChunkGroup | None
    voice_responses: list[VoiceEvidence]
    selected_response: VoiceEvidence | None
    warnings: list[str]
    ticket_text: str | None
    selection_explanation: str | None

    @property
    def success(self) -> bool:
        return self.selected_group is not None and self.selected_response is not None

    @property
    def outcome(self) -> str:
        if self.selected_group is None:
            return "chunks_missing"
        if self.selected_response is None:
            return "voice_missing"
        if self.selected_response.value:
            return "transcription_found"
        return "transcription_missing"

    @property
    def conclusion_title(self) -> str:
        return {
            "chunks_missing": "Чанки записи не найдены",
            "voice_missing": "Ответ VOICE360 не найден",
            "transcription_found": "Транскрибация сформирована",
            "transcription_missing": "Транскрибация не найдена",
        }[self.outcome]

    @property
    def conclusion_text(self) -> str:
        return {
            "chunks_missing": (
                "Чанки записи не найдены, возможно, запись отсутствует "
                "или произошла ошибка с микрофоном."
            ),
            "voice_missing": (
                "Чанки записи найдены, но в загруженных логах отсутствует ответ "
                "VOICE360. Для окончательного вывода нужны дополнительные логи."
            ),
            "transcription_found": (
                "Транскрибация сформирована, возможно, стоит отправить пользователя "
                "перепроверить сформированное резюме."
            ),
            "transcription_missing": (
                "VOICE360 не обнаружил транскрибацию. Данных достаточно для "
                "формирования ЗПИ на смежную систему."
            ),
        }[self.outcome]

def _extract_uuid(message: str) -> str | None:
    match = UUID_RE.search(message) or KEY_RE.search(message)
    return match.group(1).lower() if match else None


def _excerpt(message: str, limit: int = 260) -> str:
    compact = re.sub(r"\s+", " ", message).strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _build_ticket(
    meeting_id: str,
    group: ChunkGroup,
    response: VoiceEvidence,
) -> str:
    representative = group.representative
    result = "true" if response.value else "false"
    response_line = (
        f'{VOICE360_MARKER}. Проверка наличия транскрибации по встрече. '
        f'Ответ: {{"{meeting_id}":{result}}}'
    )
    local_time = representative.local_time or representative.server_time or "не определено"
    return "\n".join(
        [
            f"Услуга: {SERVICE}",
            f"РГ: {WORK_GROUP}",
            "Текст:",
            "Добрый день, коллеги.",
            "В АС ФОКУС пользователи не видят Резюме по проведённой встрече.",
            "В логах мы видим успешные чанки отправки сообщений в УГС.",
            "Просьба проверить на своей стороне наличие транскрибации по встрече.",
            "",
            "В логах ЕФС:",
            response_line,
            f"дата/время записи = {local_time}",
            f"id встречи = {meeting_id}",
            f'uuid = Meta: {{"uuid":"{group.uuid}"}}',
            "При отсутствии транскрибации просьба уточнить причины.",
            "Спасибо.",
        ]
    )


def analyze_transcription(
    records: list[LogRecord],
    meeting_id: str,
) -> TranscriptionAnalysis:
    normalized_id = normalize_meeting_id(meeting_id)
    groups_map: dict[str, list[ChunkEvidence]] = defaultdict(list)
    responses: list[VoiceEvidence] = []
    matched_locations: set[tuple[str, int, str | None]] = set()
    seen_sva: set[tuple[str, str, str]] = set()
    seen_voice: set[tuple[str, str]] = set()

    for record in records:
        message = str(record.get("message", ""))
        class_name = str(record.get("className", ""))
        if normalized_id not in message.upper():
            continue

        if class_name == SVA_CLASS and "topic: record_focus" in message:
            uuid = _extract_uuid(message)
            if uuid:
                server_value = str(record.get("serverEventDatetime", ""))
                fingerprint = (server_value, class_name, message)
                if fingerprint in seen_sva:
                    continue
                seen_sva.add(fingerprint)
                part_match = PART_RE.search(message)
                last_match = LAST_RE.search(message)
                server_time = server_value or None
                groups_map[uuid].append(
                    ChunkEvidence(
                        uuid=uuid,
                        part=int(part_match.group(1)) if part_match else None,
                        is_last_record=(last_match.group(1).casefold() == "true") if last_match else None,
                        server_time=server_time,
                        local_time=_moscow_text(server_time),
                        source=record.location,
                        message_excerpt=_excerpt(message),
                    )
                )
                matched_locations.add((record.source_name, record.row_number, record.sheet_name))

        if VOICE360_MARKER in message and "Ответ:" in message:
            match = VOICE_RESULT_RE.search(message)
            if match and match.group("meeting").upper() == normalized_id:
                server_time = str(record.get("serverEventDatetime", "")) or None
                fingerprint = (server_time or "", message)
                if fingerprint in seen_voice:
                    continue
                seen_voice.add(fingerprint)
                responses.append(
                    VoiceEvidence(
                        value=match.group("value").casefold() == "true",
                        server_time=server_time,
                        local_time=_moscow_text(server_time),
                        source=record.location,
                        message=message.split("Start:", 1)[0].strip(),
                    )
                )
                matched_locations.add((record.source_name, record.row_number, record.sheet_name))

    groups = [ChunkGroup(uuid=uuid, rows=rows) for uuid, rows in groups_map.items()]
    groups.sort(key=lambda group: (-len(group.rows), group.first_local_time or "", group.uuid))
    responses.sort(key=lambda item: _timestamp_key(item.server_time))

    selected_group = groups[0] if groups else None
    selected_response = responses[-1] if responses else None
    warnings: list[str] = []
    selection_explanation = None

    if not groups:
        warnings.append(
            "Не найдены отправки record_focus через SvaKafkaProducer для указанной встречи."
        )
    elif len(groups) > 1:
        warnings.append(
            f"Найдено несколько UUID ({len(groups)}). Основным выбран UUID с наибольшим числом чанков; остальные показаны ниже."
        )
    if selected_group:
        selection_explanation = (
            f"Выбран UUID {selected_group.uuid}: {len(selected_group.rows)} записей, "
            "это самая полная группа чанков. Для времени записи используется part=2, "
            "как в экспертной методике; если part=2 отсутствует — первая запись группы."
        )
        if selected_group.gaps:
            warnings.append(
                "В основной группе отсутствуют номера чанков: "
                + ", ".join(map(str, selected_group.gaps))
                + "."
            )
        if not selected_group.has_final_marker:
            warnings.append(
                "В основной группе нет признака isLastRecord=true; завершающая запись может находиться в другом UUID."
            )
    if not responses:
        warnings.append("Не найден ответ VOICE360 о наличии транскрибации.")
    else:
        values = {item.value for item in responses}
        if len(values) > 1:
            warnings.append("В логах есть противоречивые ответы VOICE360; выбран самый поздний.")
        elif len(responses) > 1:
            warnings.append(
                f"Найдено несколько ответов VOICE360 ({len(responses)}); в заявку включён самый поздний."
            )

    ticket_text = (
        _build_ticket(normalized_id, selected_group, selected_response)
        if selected_group and selected_response
        else None
    )
    return TranscriptionAnalysis(
        meeting_id=normalized_id,
        total_records=len(records),
        matched_records=len(matched_locations),
        groups=groups,
        selected_group=selected_group,
        voice_responses=responses,
        selected_response=selected_response,
        warnings=warnings,
        ticket_text=ticket_text,
        selection_explanation=selection_explanation,
    )
