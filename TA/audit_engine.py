"""Deterministic, explainable checks for incident closure records."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any


TEST_STANDS = ("MAJOR-GO", "MAJOR-CHECK", "LT")


@dataclass(frozen=True)
class AuditConfig:
    tolerance_minutes: int = 15
    minimum_remediation_chars: int = 10
    minimum_chronology_events: int = 2


@dataclass(frozen=True)
class ParsedSolution:
    raw: str
    problem_present: bool
    problem_text: str
    start_text: str
    end_text: str
    cause: str
    remediation: str
    impact_text: str
    chronology_present: bool
    chronology_text: str
    chronology_events: tuple[str, ...]


def normalize_text(value: Any) -> str:
    """Return a whitespace-normalized string and hide spreadsheet null markers."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if text.lower() in {"", "nan", "none", "nat", "null"}:
        return ""
    return text


def _build_datetime(
    year: int | str,
    month: int | str,
    day: int | str,
    hour: int | str = 0,
    minute: int | str = 0,
    second: int | str = 0,
) -> datetime | None:
    numeric_year = int(year)
    if numeric_year < 100:
        numeric_year += 2000
    try:
        return datetime(
            numeric_year,
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
        )
    except (TypeError, ValueError):
        return None


def parse_incident_datetime(value: Any) -> datetime | None:
    """Parse typed Excel values and supported textual date formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().replace(tzinfo=None)
        except (AttributeError, TypeError, ValueError):
            return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime(1899, 12, 30) + timedelta(days=float(value))
        except (OverflowError, TypeError, ValueError):
            return None
    return extract_datetime(normalize_text(value))


def extract_datetime(
    value: Any,
    reference: datetime | None = None,
    *,
    require_time: bool = False,
) -> datetime | None:
    """Extract a strict date/time from free text; combine time-only values with a reference."""
    source = normalize_text(value)
    if not source:
        return None

    time_first = re.search(
        r"(?<!\d)(\d{1,2})[:.](\d{2})(?::(\d{2}))?"
        r"[T\s,]+(\d{1,2})[.,/\-](\d{1,2})[.,/\-](\d{2,4})(?!\d)",
        source,
    )
    year_first = re.search(
        r"(?<!\d)(\d{4})[.,/\-](\d{1,2})[.,/\-](\d{1,2})"
        r"(?:[T\s,]+(\d{1,2})[:.](\d{2})(?::(\d{2}))?)?",
        source,
    )
    day_first = re.search(
        r"(?<!\d)(\d{1,2})[.,/\-](\d{1,2})[.,/\-](\d{2,4})"
        r"(?:[T\s,]+(\d{1,2})[:.](\d{2})(?::(\d{2}))?)?",
        source,
    )

    dated_matches = [
        (match.start(), kind, match)
        for kind, match in (
            ("time_first", time_first),
            ("year_first", year_first),
            ("day_first", day_first),
        )
        if match is not None
    ]
    if dated_matches:
        _, kind, match = min(dated_matches, key=lambda item: item[0])
        if kind == "time_first":
            hour, minute, second, day, month, year = match.groups(default="0")
            return _build_datetime(year, month, day, hour, minute, second)
        if require_time and match.group(4) is None:
            return None
        if kind == "year_first":
            year, middle, last, hour, minute, second = match.groups(default="0")
            standard = _build_datetime(year, middle, last, hour, minute, second)
            if standard is not None:
                return standard
            return _build_datetime(year, last, middle, hour, minute, second)
        day, month, year, hour, minute, second = match.groups(default="0")
        return _build_datetime(year, month, day, hour, minute, second)

    time_only = re.search(r"(?<!\d)(\d{1,2})[:.](\d{2})(?::(\d{2}))?(?!\d)", source)
    if time_only and reference is not None:
        hour, minute, second = (int(part or 0) for part in time_only.groups(default="0"))
        candidates = []
        for offset in (-1, 0, 1):
            day = reference.date() + timedelta(days=offset)
            try:
                candidates.append(datetime(day.year, day.month, day.day, hour, minute, second))
            except ValueError:
                return None
        return min(candidates, key=lambda item: abs(item - reference))
    return None


_SECTION_PATTERNS = {
    "problem": re.compile(
        r"^[ \t]*(?:что[ \t]+произошло|проблема|описание[ \t]+проблемы)"
        r"[ \t]*[:\-][ \t]*([^\n]*)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "start": re.compile(
        r"^[ \t]*(?:фактическое[ \t]+)?время[ \t]+(?:начала|возникновения)"
        r"(?:[ \t]+(?:инцидента|влияния))?[ \t]*[:\-][ \t]*([^\n]*)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "end": re.compile(
        r"^[ \t]*(?:фактическое[ \t]+)?время[ \t]+(?:окончания|устранения)"
        r"(?:[ \t]+(?:инцидента|влияния))?[ \t]*[:\-][ \t]*([^\n]*)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "cause": re.compile(
        r"^[ \t]*причина(?:[ \t]+инцидента)?[ \t]*[:\-][ \t]*([^\n]*)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "remediation": re.compile(
        r"^[ \t]*(?:способ[ \t]+устранения|решение)[ \t]*[:\-][ \t]*([^\n]*)$",
        re.IGNORECASE | re.MULTILINE,
    ),
}
_CHRONOLOGY_HEADING = re.compile(
    r"^[ \t]*(?:краткая[ \t]+)?хронология[ \t]*[:\-]?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_IMPACT_TIME_LINE = re.compile(
    r"^[ \t]*время[ \t]+(?:начала|окончания)[ \t]+влияния\b",
    re.IGNORECASE,
)
_IMPACT_HEADING_LINE = re.compile(
    r"^[ \t]*(?:[-*][ \t]*)?влияни\w*\b",
    re.IGNORECASE,
)


def _section_value(source: str, section: str) -> str:
    match = _SECTION_PATTERNS[section].search(source)
    return normalize_text(match.group(1)) if match else ""


def _is_meaningful_timed_line(line: str) -> bool:
    if not re.search(r"(?:\d{1,2}[:.]\d{2}|\d{1,2}[.,/\-]\d{1,2}[.,/\-]\d{2,4})", line):
        return False
    return len(re.findall(r"[A-Za-zА-Яа-яЁё]", line)) >= 3


def _extract_impact_text(source: str) -> str:
    candidates = [
        normalize_text(line)
        for line in source.splitlines()
        if re.search(r"\bвлияни\w*", line, re.IGNORECASE)
    ]
    content_lines = [
        line for line in candidates if not _IMPACT_TIME_LINE.search(line)
    ]
    explicit = next(
        (line for line in content_lines if _IMPACT_HEADING_LINE.search(line)),
        "",
    )
    return explicit


def parse_solution(value: Any) -> ParsedSolution:
    """Parse the user-written solution into the workbook's established sections."""
    source = normalize_text(value)
    chronology_match = _CHRONOLOGY_HEADING.search(source)
    chronology_text = normalize_text(source[chronology_match.end() :]) if chronology_match else ""
    chronology_events = tuple(
        line.strip()
        for line in chronology_text.split("\n")
        if _is_meaningful_timed_line(line.strip())
    )
    impact_line = _extract_impact_text(source)
    return ParsedSolution(
        raw=source,
        problem_present=_SECTION_PATTERNS["problem"].search(source) is not None,
        problem_text=_section_value(source, "problem"),
        start_text=_section_value(source, "start"),
        end_text=_section_value(source, "end"),
        cause=_section_value(source, "cause"),
        remediation=_section_value(source, "remediation"),
        impact_text=impact_line,
        chronology_present=chronology_match is not None,
        chronology_text=chronology_text,
        chronology_events=chronology_events,
    )


def classify_incident(incident: Mapping[str, Any]) -> str:
    """Select the audit profile using explicit workbook fields."""
    status = normalize_text(incident.get("Статус")).casefold()
    close_code = normalize_text(incident.get("Код закрытия")).casefold()
    stand_type = normalize_text(incident.get("Тип стенда")).upper()

    if status == "в работе":
        return "in_work"
    if close_code == "автовыполнение":
        return "automatic"
    if any(stand_type == marker or stand_type.startswith(f"{marker} ") for marker in TEST_STANDS):
        return "test"
    if close_code == "дублирование":
        return "duplicate"
    return "manual"


def is_test_incident(incident: Mapping[str, Any]) -> bool:
    return classify_incident(incident) == "test"


def make_check(
    rule_id: str,
    title: str,
    status: str,
    message: str,
    evidence: Mapping[str, Any] | None = None,
    recommendation: str = "",
    severity: str = "none",
) -> dict[str, Any]:
    if status not in {"passed", "remark", "info", "skipped"}:
        raise ValueError(f"Unsupported check status: {status}")
    if severity not in {"none", "error", "warning"}:
        raise ValueError(f"Unsupported check severity: {severity}")
    if status != "remark" and severity != "none":
        raise ValueError("Only remarks may carry a user severity")
    return {
        "rule_id": rule_id,
        "title": title,
        "status": status,
        "severity": severity,
        "message": message,
        "evidence": dict(evidence or {}),
        "recommendation": recommendation,
    }


def _extract_jira_links(source: str) -> dict[str, str]:
    patterns = (
        (r"OPLOT-\d+", "https://jira.delta.sbrf.ru/browse/{}"),
        (r"SMECLM-\d+", "https://jira.sberbank.ru/browse/{}"),
        (r"SMECSC-\d+", "https://jira.delta.sbrf.ru/browse/{}"),
        (r"EMRM-\d+", "https://jira.sberbank.ru/browse/{}"),
        (r"DRMMMB-\d+", "https://jira.sberbank.ru/browse/{}"),
    )
    result = {}
    for pattern, template in patterns:
        for key in re.findall(pattern, source, re.IGNORECASE):
            normalized = key.upper()
            result[normalized] = template.format(normalized)
    return result


def _extract_named_terms(source: str, terms: set[str]) -> list[str]:
    return sorted(term for term in terms if re.search(rf"\b{re.escape(term)}\b", source, re.IGNORECASE))


def _summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        status: sum(check["status"] == status for check in checks)
        for status in ("passed", "remark", "info", "skipped")
    }
    summary.update(
        {
            severity: sum(check.get("severity") == severity for check in checks)
            for severity in ("error", "warning")
        }
    )
    return summary


def _derive_outcome(checks: list[dict[str, Any]], profile: str) -> str:
    if any(
        check["rule_id"] in {"AUDIT_RULE_ERROR", "SPELLCHECK_FAILED"}
        for check in checks
    ):
        return "system_error"
    if profile in {"in_work", "automatic", "test"}:
        return "skipped"
    if any(check.get("severity") == "error" for check in checks):
        return "error"
    if any(check.get("severity") == "warning" for check in checks):
        return "warning"
    if any(check["status"] == "remark" for check in checks):
        return "warning"
    return "passed"


def _required_check(
    present: bool,
    rule_id: str,
    title: str,
    missing_message: str,
    recommendation: str,
    missing_severity: str = "error",
) -> dict[str, Any]:
    if present:
        return make_check(rule_id, title, "passed", f"{title} указано")
    return make_check(
        rule_id,
        title,
        "remark",
        missing_message,
        recommendation=recommendation,
        severity=missing_severity,
    )


def _format_datetime(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y %H:%M:%S") if value else ""


def _time_field_checks(
    *,
    title: str,
    required_rule_id: str,
    solution_text: str,
) -> tuple[list[dict[str, Any]], datetime | None]:
    parsed_solution = extract_datetime(solution_text, require_time=True)
    checks = []
    if not solution_text:
        checks.append(
            make_check(
                required_rule_id,
                title,
                "remark",
                f"{title} не указано",
                recommendation=f"Укажите корректные дату и время для раздела «{title}».",
                severity="error",
            )
        )
    elif parsed_solution is None:
        checks.append(
            make_check(
                required_rule_id,
                title,
                "remark",
                f"{title} содержит некорректную дату или время",
                evidence={"solution": solution_text},
                recommendation="Исправьте формат или невозможное календарное значение.",
                severity="error",
            )
        )
    else:
        checks.append(
            make_check(
                required_rule_id,
                title,
                "passed",
                f"{title} распознано",
                evidence={"solution": _format_datetime(parsed_solution)},
            )
        )

    return checks, parsed_solution


_WEAK_REMEDIATION = re.compile(
    r"^(?:устранено|исправлено|восстановлено|работы\s+выполнены|решено|перезапущено)[.!]?$",
    re.IGNORECASE,
)
_GENERIC_REMEDIATION_OUTCOME = re.compile(
    r"^(?:(?:работоспособност\w*|работа)[ \t]+.{1,60}|(?:сервис|система)[ \t]+.{0,60})"
    r"[ \t]+(?:был\w*[ \t]+)?(?:восстановлен\w*|нормализован\w*|доступен\w*)[.!]?$",
    re.IGNORECASE,
)
_REMEDIATION_SIGNAL = re.compile(
    r"установ|замен|перезапущ|перезагруж|исправ|скоррект|"
    r"переключ|очищ|удален|отключ|включ|обновл|настро|увелич|уменьш|"
    r"добавл|создан(?:а|о|ы)?[ \t]+(?:заявк|задач)|заведен|применен|"
    r"откачен|переразмещ|разблокир|поднят|возвращен|перевыпущ|"
    r"выполнен\w*[ \t]+(?:перезапуск|очистк|замен|настройк|переключ|откат)|"
    r"откат|возврат[ \t]+к|перевыпуск|очистк|остановк|сканирован|"
    r"раскатк|внедрен|самоустран|авторазреш|хотфикс|"
    r"(?:в[ \t]+рамках|по)[ \t]+(?:исполнени\w*|активност\w*)?[ \t]*"
    r"(?:задач|проблем|зпи|зни|корнев\w*[ \t]+инцидент)[^\n]{0,80}"
    r"(?:INC|PM|OPLOT|EMRM|INCT|C)-?\d+|(?:INC|PM|OPLOT|EMRM|INCT|C)-?\d+",
    re.IGNORECASE,
)


def _cause_checks(cause: str, category: str) -> list[dict[str, Any]]:
    checks = [
        _required_check(
            bool(cause),
            "CAUSE_REQUIRED",
            "Причина",
            "Причина не указана",
            "Опишите фактическую техническую причину инцидента.",
        )
    ]
    if category:
        checks.append(
            make_check(
                "CAUSE_CATEGORY",
                "Категория причины",
                "info",
                "Категория из Excel показана отдельно и не заменяет фактическую причину",
                evidence={"category": category, "solution": cause},
            )
        )
    return checks


def _remediation_checks(remediation: str, close_code: str, config: AuditConfig) -> list[dict[str, Any]]:
    checks = [
        _required_check(
            bool(remediation),
            "REMEDIATION_REQUIRED",
            "Способ устранения",
            "Способ устранения не указан",
            "Опишите конкретные выполненные действия.",
        )
    ]
    if remediation:
        weak = (
            len(remediation.strip()) < config.minimum_remediation_chars
            or bool(_WEAK_REMEDIATION.fullmatch(remediation.strip()))
            or bool(_GENERIC_REMEDIATION_OUTCOME.fullmatch(remediation.strip()))
            or not _REMEDIATION_SIGNAL.search(remediation)
        )
        checks.append(
            make_check(
                "REMEDIATION_QUALITY",
                "Качество устранения",
                "remark" if weak else "passed",
                "Способ устранения описан слишком общо" if weak else "Способ устранения содержит конкретное действие",
                evidence={"solution": remediation},
                recommendation=(
                    "Укажите выполненное действие, изменение, восстановление или связанную задачу."
                    if weak
                    else ""
                ),
                severity="warning" if weak else "none",
            )
        )
    if close_code.casefold() == "решено обходным путём":
        describes_workaround = bool(
            re.search(
                r"обходн|временн|переключ|резерв|альтернатив|ручн|маршрут|(?:INC|PM|OPLOT|JIRA)-?\d+",
                remediation,
                re.IGNORECASE,
            )
        )
        checks.append(
            make_check(
                "WORKAROUND_DETAIL",
                "Обходной путь",
                "passed" if describes_workaround else "remark",
                "Обходной путь описан" if describes_workaround else "Код закрытия указывает обходной путь, но он не описан",
                evidence={"close_code": close_code, "solution": remediation},
                recommendation=(
                    "Опишите применённый обходной путь и связанную задачу окончательного устранения."
                    if not describes_workaround
                    else ""
                ),
                severity="none" if describes_workaround else "warning",
            )
        )
    return checks


def _text_quality_checks(parsed: ParsedSolution) -> list[dict[str, Any]]:
    checks = []
    if parsed.problem_present:
        weak_problem = (
            len(parsed.problem_text) < 15
            or bool(
                re.fullmatch(
                    r"(?:проблема|ошибка|сбой|инцидент|описано[ \t]+выше|см\.?[ \t]+выше)[.!]?",
                    parsed.problem_text,
                    re.IGNORECASE,
                )
            )
        )
        checks.append(
            make_check(
                "PROBLEM_DESCRIPTION",
                "Что произошло",
                "remark" if weak_problem else "passed",
                (
                    "Добровольно добавленный раздел «Что произошло» пуст или формален"
                    if weak_problem
                    else "Раздел «Что произошло» содержит описание"
                ),
                evidence={"solution": parsed.problem_text},
                recommendation=(
                    "Опишите наблюдаемый симптом, затронутый объект и проявление проблемы либо удалите пустой заголовок."
                    if weak_problem
                    else ""
                ),
                severity="warning" if weak_problem else "none",
            )
        )

    punctuation = re.findall(r"[!?]{3,}|[,;:]{3,}|\.{4,}", parsed.raw)
    checks.append(
        make_check(
            "PUNCTUATION",
            "Пунктуация",
            "remark" if punctuation else "passed",
            "Найдены грубые повторы знаков препинания" if punctuation else "Грубых пунктуационных дефектов не найдено",
            evidence={"fragments": punctuation[:5]} if punctuation else {},
            recommendation="Уберите повторяющиеся знаки препинания." if punctuation else "",
            severity="warning" if punctuation else "none",
        )
    )
    return checks


def _classify_impact(impact_text: str) -> str:
    if not impact_text:
        return "missing"
    if re.search(
        r"(?:влияни\w*[^\n]{0,80}(?:нет|отсутств\w*|не\s+(?:оказывалось|зафиксировано|наблюдалось)))|"
        r"(?:(?:нет|отсутств\w*)[^\n]{0,40}влияни\w*)",
        impact_text,
        re.IGNORECASE,
    ):
        return "none"
    numeric = re.search(
        r"\b\d+(?:[.,]\d+)?\s*(?:%|пользоват\w*|клиент\w*|запрос\w*|операц\w*|"
        r"сообщени\w*|сотрудник\w*|сервис\w*|минут\w*)",
        impact_text,
        re.IGNORECASE,
    )
    effect = re.search(
        r"недоступ\w*|деградац\w*|невозмож\w*|замедлен\w*|задерж\w*|"
        r"отказ\w*|потер\w*|ошибк\w*",
        impact_text,
        re.IGNORECASE,
    )
    affected = re.search(
        r"авторизац\w*|функц\w*|сервис\w*|операц\w*|запрос\w*|"
        r"пользоват\w*|клиент\w*|сотрудник\w*",
        impact_text,
        re.IGNORECASE,
    )
    return "present_specific" if numeric or (effect and affected) else "present_vague"


def _impact_checks(impact_text: str, factual_impact: str) -> list[dict[str, Any]]:
    classification = _classify_impact(impact_text)
    checks = [
        _required_check(
            classification != "missing",
            "IMPACT_REQUIRED",
            "Влияние",
            "Влияние не указано",
            "Явно укажите отсутствие влияния либо опишите фактическое влияние.",
        )
    ]
    reference = factual_impact.casefold()
    if not reference:
        checks.append(
            make_check(
                "IMPACT_MATCH",
                "Согласованность влияния",
                "info",
                "В Excel отсутствует значение влияния для сравнения",
                evidence={"solution": impact_text, "classification": classification},
            )
        )
        return checks

    if reference == "нет":
        matches = classification == "none"
        message = "Отсутствие влияния указано явно" if matches else "Решение противоречит значению «Нет» в Excel"
        mismatch_severity = "error"
    elif reference == "да":
        matches = classification == "present_specific"
        if matches:
            message = "Влияние описано конкретно"
        elif classification == "none":
            message = "Решение указывает отсутствие влияния, но в Excel указано «Да»"
        elif classification == "missing":
            message = "Влияние не описано, хотя в Excel указано «Да»"
        else:
            message = "Для значения «Да» влияние описано недостаточно конкретно"
        mismatch_severity = "warning" if classification == "present_vague" else "error"
    else:
        checks.append(
            make_check(
                "IMPACT_MATCH",
                "Согласованность влияния",
                "info",
                "Неизвестное значение влияния в Excel",
                evidence={"excel": factual_impact, "solution": impact_text},
            )
        )
        return checks

    checks.append(
        make_check(
            "IMPACT_MATCH",
            "Согласованность влияния",
            "passed" if matches else "remark",
            message,
            evidence={"excel": factual_impact, "solution": impact_text, "classification": classification},
            recommendation=(
                ""
                if matches
                else "Согласуйте описание влияния с Excel и добавьте количество либо конкретный эффект."
            ),
            severity="none" if matches else mismatch_severity,
        )
    )
    return checks


_DATE_CONTEXT_LINE = re.compile(
    r"^[ \t]*(?:[-*][ \t]*)?\d{1,2}[.,/\-]\d{1,2}[.,/\-]\d{2,4}[ \t]*$"
)


def _event_datetimes(
    events: tuple[str, ...],
    reference: datetime | None,
    chronology_text: str = "",
) -> list[datetime | None]:
    parsed_events = []
    current_reference = reference
    pending_date_context = None
    source_events = events
    if chronology_text:
        source_events = tuple(
            line.strip()
            for line in chronology_text.splitlines()
            if line.strip()
        )
    for event in source_events:
        if _DATE_CONTEXT_LINE.fullmatch(event):
            date_context = extract_datetime(event)
            if date_context is not None:
                current_reference = date_context
                pending_date_context = date_context.date()
            continue
        if chronology_text and not _is_meaningful_timed_line(event):
            continue
        if pending_date_context is not None:
            contains_date = re.search(
                r"(?<!\d)(?:\d{4}[.,/\-]\d{1,2}[.,/\-]\d{1,2}|"
                r"\d{1,2}[.,/\-]\d{1,2}[.,/\-]\d{2,4})(?!\d)",
                event,
            )
            time_match = None if contains_date else re.search(
                r"(?<!\d)(\d{1,2})[:.](\d{2})(?::(\d{2}))?(?!\d)",
                event,
            )
            if time_match is not None:
                hour, minute, second = time_match.groups(default="0")
                parsed = _build_datetime(
                    pending_date_context.year,
                    pending_date_context.month,
                    pending_date_context.day,
                    hour,
                    minute,
                    second,
                )
            else:
                parsed = extract_datetime(event, current_reference)
            pending_date_context = None
        else:
            parsed = extract_datetime(event, current_reference)
        parsed_events.append(parsed)
        if parsed is not None:
            current_reference = parsed
    return parsed_events


def _chronology_conflicts(
    events: tuple[str, ...],
    event_times: list[datetime | None],
) -> list[dict[str, Any]]:
    conflicts = []
    for index in range(1, len(events)):
        previous_time = event_times[index - 1]
        next_time = event_times[index]
        if previous_time is None or next_time is None or next_time >= previous_time:
            continue
        conflicts.append(
            {
                "previous_event": events[index - 1],
                "next_event": events[index],
                "previous_time": _format_datetime(previous_time),
                "next_time": _format_datetime(next_time),
                "backwards_minutes": round(
                    (previous_time - next_time).total_seconds() / 60,
                    1,
                ),
            }
        )
    return conflicts


_CREATION_EVENT = re.compile(
    r"(?:создан\w*|автосоздан\w*|зарегистрирован\w*|регистрац\w*)[^\n]{0,40}(?:инцидент|автоцидент)|"
    r"(?:инцидент|автоцидент)[^\n]{0,40}(?:создан\w*|зарегистрирован\w*)",
    re.IGNORECASE,
)
def _creation_only_event(event: str) -> bool:
    without_time = re.sub(
        r"^\s*(?:(?:\d{1,2}[.,/\-]\d{1,2}[.,/\-]\d{2,4})[T\s,]+)?"
        r"\d{1,2}[:.]\d{2}(?::\d{2})?\s*[-—–:]?\s*",
        "",
        event,
    )
    without_ids = re.sub(r"\b(?:INC|CI|PM|OPLOT|JIRA)-?\d+\b", "", without_time, flags=re.IGNORECASE)
    if not _CREATION_EVENT.search(without_ids):
        return False
    remainder = re.sub(
        r"(?:создан\w*|создал\w*|автосоздан\w*|зарегистрирован\w*|регистрац\w*)"
        r"[ \t]+(?:авто[ \t-]*)?(?:инцидент|автоцидент)\w*|"
        r"(?:авто[ \t-]*)?(?:инцидент|автоцидент)\w*[ \t]+"
        r"(?:создан\w*|зарегистрирован\w*)",
        " ",
        without_ids,
        flags=re.IGNORECASE,
    )
    remainder = re.sub(
        r"(?:по|из-за)[ \t]+(?:срабатывани\w*[ \t]+)?"
        r"(?:алерт\w*|сигнал\w*|мониторинг\w*)(?:[ \t]+\w+){0,4}",
        " ",
        remainder,
        flags=re.IGNORECASE,
    )
    separate_action = re.search(
        r"обнаруж|выяв|зафиксир|недоступ|деградац|отказ|ошиб|устран|"
        r"восстанов|замен|перезапущ|переключ|исправ",
        remainder,
        re.IGNORECASE,
    )
    return separate_action is None


def _chronology_checks(
    parsed: ParsedSolution,
    incident: Mapping[str, Any],
    config: AuditConfig,
) -> list[dict[str, Any]]:
    substantive_events = tuple(
        event for event in parsed.chronology_events if not _creation_only_event(event)
    )
    checks = [
        _required_check(
            parsed.chronology_present,
            "CHRONOLOGY_REQUIRED",
            "Хронология",
            "Заголовок хронологии отсутствует",
            "Добавьте раздел «Краткая хронология».",
        ),
        _required_check(
            len(substantive_events) >= config.minimum_chronology_events,
            "CHRONOLOGY_EVENTS",
            "События хронологии",
            "В хронологии недостаточно содержательных событий со временем",
            "Укажите минимум два события: обнаружение/начало и устранение/окончание.",
            missing_severity="warning",
        ),
    ]
    factual_start = parse_incident_datetime(incident.get("Фактическое время возникновения"))
    event_times = _event_datetimes(
        parsed.chronology_events,
        factual_start,
        parsed.chronology_text,
    )
    invalid_events = [
        event for event, event_time in zip(parsed.chronology_events, event_times) if event_time is None
    ]
    if parsed.chronology_events:
        checks.append(
            make_check(
                "CHRONOLOGY_TIME_VALID",
                "Корректность времени хронологии",
                "remark" if invalid_events else "passed",
                "В хронологии есть нераспознаваемое или невозможное время" if invalid_events else "Время событий распознано",
                evidence={"events": invalid_events} if invalid_events else {},
                recommendation="Исправьте невозможные или нераспознаваемые значения времени." if invalid_events else "",
                severity="warning" if invalid_events else "none",
            )
        )
    comparable = [value for value in event_times if value is not None]
    if len(comparable) >= 2:
        conflicts = _chronology_conflicts(parsed.chronology_events, event_times)
        ordered = not conflicts
        first_conflict = conflicts[0] if conflicts else None
        checks.append(
            make_check(
                "CHRONOLOGY_ORDER",
                "Порядок хронологии",
                "passed" if ordered else "remark",
                (
                    "События идут в хронологическом порядке"
                    if ordered
                    else (
                        f"После «{first_conflict['previous_event']}» указано более раннее "
                        f"время в событии «{first_conflict['next_event']}»"
                    )
                ),
                evidence={"conflicts": conflicts} if conflicts else {},
                recommendation=(
                    ""
                    if ordered
                    else "Проверьте порядок строк или опечатку во времени."
                ),
                severity="warning" if conflicts else "none",
            )
        )

    creation_index = next(
        (
            index
            for index, event in enumerate(parsed.chronology_events)
            if _CREATION_EVENT.search(event)
        ),
        None,
    )
    created = parse_incident_datetime(incident.get("Создан"))
    if creation_index is None:
        checks.append(
            make_check(
                "CREATED_EVENT_MATCH",
                "Создание инцидента",
                "info",
                "Отдельное событие создания в хронологии не указано; это не является ошибкой",
            )
        )
    else:
        event_time = event_times[creation_index]
        if event_time is None or created is None:
            checks.append(
                make_check(
                    "CREATED_EVENT_MATCH",
                    "Создание инцидента",
                    "remark",
                    "Не удалось сравнить время события создания с Excel",
                    evidence={"event": parsed.chronology_events[creation_index], "excel": normalize_text(incident.get("Создан"))},
                    recommendation="Укажите распознаваемое время создания инцидента.",
                    severity="warning",
                )
            )
        else:
            difference = round(abs((event_time - created).total_seconds()) / 60, 1)
            matches = difference <= config.tolerance_minutes
            checks.append(
                make_check(
                    "CREATED_EVENT_MATCH",
                    "Создание инцидента",
                    "passed" if matches else "remark",
                    "Время создания согласовано с Excel" if matches else f"Время создания отличается от Excel более чем на {config.tolerance_minutes} минут",
                    evidence={
                        "event": _format_datetime(event_time),
                        "excel": _format_datetime(created),
                        "difference_minutes": difference,
                    },
                    recommendation="" if matches else "Исправьте время создания в хронологии.",
                    severity="none" if matches else "warning",
                )
            )
    return checks


def _competency_check(solution: str) -> dict[str, Any]:
    role_pattern = re.compile(
        r"администратор\w*|сопровождени\w*|разработчик\w*|вендор\w*|дежурн\w*|"
        r"команд[аы]\w*|оплот|зпи",
        re.IGNORECASE,
    )
    negated_involvement = re.compile(
        r"\bне[ \t]+(?:был\w*[ \t]+)?(?:привлек\w*|участв\w*)",
        re.IGNORECASE,
    )

    def role_is_negated(match: re.Match[str]) -> bool:
        line_start = solution.rfind("\n", 0, match.start()) + 1
        line_end = solution.find("\n", match.end())
        if line_end == -1:
            line_end = len(solution)
        return bool(negated_involvement.search(solution[line_start:line_end]))

    role_matches = [match for match in role_pattern.finditer(solution) if not role_is_negated(match)]
    positive_lines = [
        line for line in solution.splitlines() if not negated_involvement.search(line)
    ]
    involvement = bool(
        re.search(
            r"привлеч\w*|эскалир\w*|совместно\s+с",
            "\n".join(positive_lines),
            re.IGNORECASE,
        )
    )
    roles = sorted({match.group(0) for match in role_matches}, key=str.casefold)
    references = sorted(
        {
            match.upper()
            for match in re.findall(
                r"\b(?:INCT|PM|EMRM|OPLOT|SMECLM|SMECSC|DRMMMB|JIRA)-?\d+\b",
                solution,
                re.IGNORECASE,
            )
        }
    )
    action_pattern = re.compile(
        r"выполн|подключ|провер|проанализ|анализ|диагност|перезапуст|замен|настро|"
        r"устран|восстанов|эскалир|согласов|предостав|создан|очист|отключ|"
        r"разблокир|скоррект|установ|получ|сообщ|информ|выстав|назнач|"
        r"взят|обратил|запрос|напис|отправ|направ",
        re.IGNORECASE,
    )
    has_role_action = bool(references) or any(
        action_pattern.search(
            solution[solution.rfind("\n", 0, match.start()) + 1 : match.end() + 160]
        )
        for match in role_matches
    )
    if involvement and not roles:
        return make_check(
            "COMPETENCIES",
            "Привлечённые компетенции",
            "remark",
            "Указано привлечение, но не названа команда или роль",
            recommendation="Укажите, кто был привлечён и что выполнял.",
            severity="error",
        )
    if roles and not has_role_action:
        return make_check(
            "COMPETENCIES",
            "Привлечённые компетенции",
            "remark",
            "Компетенция названа, но не описано выполненное ею действие",
            evidence={"roles": roles, "references": references},
            recommendation="Укажите, что именно выполнила привлечённая команда или роль.",
            severity="error",
        )
    return make_check(
        "COMPETENCIES",
        "Привлечённые компетенции",
        "passed" if roles else "info",
        "Найдены привлечённые роли и их действия" if roles else "Явных признаков привлечения других команд нет",
        evidence={"roles": roles, "references": references, "action_found": has_role_action},
    )


def _duplicate_checks(incident: Mapping[str, Any], solution: str) -> list[dict[str, Any]]:
    references = sorted({value.upper() for value in re.findall(r"\bINC\d+\b", solution, re.IGNORECASE)})
    parent = normalize_text(incident.get("ID Родителя")).upper()
    valid = bool(references) and (not parent or parent in references)
    if valid:
        message = "Указан основной инцидент, ссылка согласована с ID Родителя"
    elif not references:
        message = "Для дубликата не указан основной инцидент"
    else:
        message = "Номер основного инцидента не совпадает с ID Родителя"
    return [
        make_check(
            "DUPLICATE_REFERENCE",
            "Оформление дубликата",
            "passed" if valid else "remark",
            message,
            evidence={"references": references, "parent_id": parent},
            recommendation="" if valid else "Укажите корректный номер основного INC и согласуйте его с ID Родителя.",
            severity="none" if valid else "error",
        )
    ]


def _spelling_checks(solution: str, checker: Any) -> list[dict[str, Any]]:
    if not getattr(checker, "available", False):
        return [
            make_check(
                "SPELLCHECK_UNAVAILABLE",
                "Орфография",
                "info",
                "Локальный орфографический словарь недоступен; остальные проверки выполнены",
            )
        ]
    try:
        findings = checker.check(solution)
    except Exception:
        logging.exception("Local spelling check failed")
        return [
            make_check(
                "SPELLCHECK_FAILED",
                "Орфография",
                "info",
                "Локальная проверка орфографии завершилась с ошибкой",
                recommendation="Повторите проверку или проверьте локальные словари приложения.",
            )
        ]
    if not findings:
        return [make_check("SPELLING", "Орфография", "passed", "Орфографических замечаний не найдено")]
    checks = []
    kind_labels = {
        "spelling": "Возможная орфографическая ошибка",
        "mixed_script": "В слове смешаны кириллица и латиница",
        "repeated_word": "Повтор слова",
    }
    for finding in findings:
        suggestions = list(finding.get("suggestions") or [])
        word = normalize_text(finding.get("word"))
        message = f"{kind_labels.get(finding.get('kind'), 'Замечание к тексту')}: «{word}»"
        checks.append(
            make_check(
                "SPELLING",
                "Орфография",
                "remark",
                message,
                evidence={
                    "word": word,
                    "position": finding.get("position"),
                    "suggestions": suggestions,
                    "kind": finding.get("kind"),
                },
                recommendation=(
                    f"Проверьте слово. Возможные варианты: {', '.join(suggestions)}."
                    if suggestions
                    else "Проверьте написание и используемый алфавит."
                ),
                severity="warning",
            )
        )
    return checks


def _safe_check_group(title: str, callback: Any) -> list[dict[str, Any]]:
    try:
        return list(callback())
    except Exception:
        logging.exception("Incident audit rule group failed: %s", title)
        return [
            make_check(
                "AUDIT_RULE_ERROR",
                title,
                "info",
                f"Группа проверки «{title}» не выполнена из-за внутренней ошибки",
                evidence={"group": title},
                recommendation="Повторите проверку; если ошибка сохраняется, передайте её разработчику.",
            )
        ]


def _safe_time_group(title: str, callback: Any) -> tuple[list[dict[str, Any]], datetime | None]:
    try:
        return callback()
    except Exception:
        logging.exception("Incident audit time rule group failed: %s", title)
        return [
            make_check(
                "AUDIT_RULE_ERROR",
                title,
                "info",
                f"Группа проверки «{title}» не выполнена из-за внутренней ошибки",
                evidence={"group": title},
                recommendation="Повторите проверку; если ошибка сохраняется, передайте её разработчику.",
            )
        ], None


def audit_incident(
    incident: Mapping[str, Any],
    config: AuditConfig = AuditConfig(),
    spelling_checker: Any = None,
) -> dict[str, Any]:
    """Return an explainable audit while preserving the application's existing result keys."""
    profile = classify_incident(incident)
    solution = normalize_text(incident.get("Решение"))
    description = normalize_text(incident.get("Описание"))
    subject = normalize_text(incident.get("Тема инцидента"))
    parsed = parse_solution(solution)
    cause_category = normalize_text(incident.get("Причина"))
    close_code = normalize_text(incident.get("Код закрытия"))
    context = "\n".join(part for part in (subject, description, solution) if part)

    if profile in {"in_work", "automatic", "test"}:
        labels = {
            "in_work": "Инцидент ещё находится в работе",
            "automatic": "Инцидент закрыт автоматически",
            "test": "Инцидент относится к тестовому стенду",
        }
        checks = [
            make_check(
                "AUDIT_PROFILE",
                "Профиль проверки",
                "skipped",
                labels[profile],
            )
        ]
        status = "Проверка не проводится"
    elif profile == "duplicate":
        checks = _duplicate_checks(incident, solution)
        status = (
            "Есть замечания"
            if any(check["status"] == "remark" for check in checks)
            else "Дубликат оформлен корректно"
        )
    else:
        start_checks, solution_start = _safe_time_group(
            "Время начала",
            lambda: _time_field_checks(
                title="Время начала",
                required_rule_id="TIME_START_REQUIRED",
                solution_text=parsed.start_text,
            ),
        )
        end_checks, solution_end = _safe_time_group(
            "Время окончания",
            lambda: _time_field_checks(
                title="Время окончания",
                required_rule_id="TIME_END_REQUIRED",
                solution_text=parsed.end_text,
            ),
        )
        checks = start_checks + end_checks
        checks.extend(_safe_check_group("Что произошло и пунктуация", lambda: _text_quality_checks(parsed)))
        checks.extend(_safe_check_group("Причина", lambda: _cause_checks(parsed.cause, cause_category)))
        checks.extend(_safe_check_group("Способ устранения", lambda: _remediation_checks(parsed.remediation, close_code, config)))
        checks.extend(
            _safe_check_group(
                "Влияние",
                lambda: _impact_checks(
                parsed.impact_text,
                normalize_text(incident.get("Влияние на клиентский сервисе")),
                ),
            )
        )
        checks.extend(_safe_check_group("Хронология", lambda: _chronology_checks(parsed, incident, config)))
        checks.extend(_safe_check_group("Привлечённые компетенции", lambda: [_competency_check(solution)]))
        if solution_start is not None and solution_end is not None:
            ordered = solution_start <= solution_end
            checks.append(
                make_check(
                    "TIME_ORDER",
                    "Порядок времени",
                    "passed" if ordered else "remark",
                    "Время начала не позже времени окончания" if ordered else "Время начала указано позже времени окончания",
                    evidence={
                        "start": _format_datetime(solution_start),
                        "end": _format_datetime(solution_end),
                    },
                    recommendation="" if ordered else "Исправьте время начала или окончания.",
                    severity="none" if ordered else "error",
                )
            )

    if profile in {"manual", "duplicate"}:
        if spelling_checker is None:
            try:
                from TA.audit_spelling import get_spelling_checker

                spelling_checker = get_spelling_checker()
            except (ImportError, OSError, RuntimeError):
                spelling_checker = None
        checks.extend(_spelling_checks(solution, spelling_checker))
        has_remarks = any(check["status"] == "remark" for check in checks)
        if profile == "duplicate":
            status = "Есть замечания" if has_remarks else "Дубликат оформлен корректно"
        else:
            status = "Есть замечания" if has_remarks else "Инцидент закрыт корректно"

    remarks = [check["message"] for check in checks if check["status"] == "remark"]
    errors = [check["message"] for check in checks if check.get("severity") == "error"]
    warnings = [check["message"] for check in checks if check.get("severity") == "warning"]
    outcome = _derive_outcome(checks, profile)
    status_labels = {
        "error": "Требует исправления",
        "warning": "Есть замечания",
        "passed": "Инцидент закрыт корректно",
        "skipped": "Проверка не проводится",
        "system_error": "Проверка выполнена не полностью",
    }
    status = (
        "Дубликат оформлен корректно"
        if profile == "duplicate" and outcome == "passed"
        else status_labels[outcome]
    )
    competencies = _extract_named_terms(
        solution,
        {"Оплот", "ЗПИ", "Администраторы", "Сопровождение", "Разработчики", "Вендор"},
    )
    what_happened = description or subject or "Не указано"
    if subject and description and subject.casefold() not in description.casefold():
        what_happened = f"{subject}. {description}"

    return {
        "Статус": status,
        "Что произошло": what_happened,
        "Почему произошло": parsed.cause or "Не указано",
        "Привлечённые компетенции": " / ".join(competencies) if competencies else "Не указано",
        "Ход устранения": parsed.remediation or "Не указано",
        "Дата начала": "Да" if parsed.start_text else "Нет",
        "Дата окончания": "Да" if parsed.end_text else "Нет",
        "outcome": outcome,
        "Ошибки": errors,
        "Предупреждения": warnings,
        "Замечания": remarks,
        "Jira_links": _extract_jira_links(solution),
        "Affected_systems": _extract_named_terms(
            context,
            {
                "Oracle", "PostgreSQL", "Kafka", "RabbitMQ", "Redis", "Nginx", "Apache",
                "Tomcat", "Kubernetes", "Docker", "Linux", "Windows", "Zabbix", "Prometheus",
                "Grafana", "Jenkins", "GitLab", "OpenShift", "VMware", "MQ", "WebSphere",
            },
        ),
        "Problem_types": _extract_named_terms(
            context,
            {
                "Диск", "Файловая система", "CPU", "Память", "OutOfMemory", "OOM", "GC",
                "Сеть", "DNS", "SSL", "Сертификат", "SQL", "Deadlock", "Replication",
                "Очередь", "Блокировка", "Timeout", "Авторизация", "LDAP", "Kerberos",
                "SSO", "Интеграция", "REST", "SOAP", "API",
            },
        ),
        "Код закрытия": close_code,
        "Причина из файла": cause_category,
        "profile": profile,
        "checks": checks,
        "summary": _summary(checks),
        "parsed": asdict(parsed),
    }
