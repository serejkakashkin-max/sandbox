"""Local Russian spelling checks with incident-specific technical exclusions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    from spellchecker import SpellChecker
except ImportError:  # The audit engine must keep working without this optional package.
    SpellChecker = None

try:
    from pymorphy3 import MorphAnalyzer
except ImportError:  # Morphology prevents false positives on normal Russian inflections.
    MorphAnalyzer = None


_PROTECTED_PATTERNS = (
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\w)(?:[A-Za-z]:\\|/)(?:[^\s,;]+)"),
    re.compile(r"\b(?:INC|CI|JIRA|OPLOT|SMECLM|SMECSC|EMRM|DRMMMB|PM)-?\d+\b", re.IGNORECASE),
    re.compile(r"\b[\w.-]*\d[\w.-]*\b", re.UNICODE),
    re.compile(r"\b[A-ZА-ЯЁ]{2,}\b"),
    re.compile(r"\b[A-Za-z]+-[А-Яа-яЁё]{1,2}\b"),
)
_WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*")
_KNOWN_CORRECTIONS = {
    "срабатываение": "срабатывание",
}


def _mask_protected(source: str) -> str:
    chars = list(source)
    for pattern in _PROTECTED_PATTERNS:
        for match in pattern.finditer(source):
            chars[match.start() : match.end()] = " " * (match.end() - match.start())
    return "".join(chars)


class RussianSpellingChecker:
    def __init__(self, domain_words_path: str | Path | None = None):
        self.available = SpellChecker is not None and MorphAnalyzer is not None
        self._spell = SpellChecker(language="ru", distance=1) if self.available else None
        self._morph = MorphAnalyzer(lang="ru") if self.available else None
        words_path = Path(domain_words_path) if domain_words_path else Path(__file__).with_name("domain_words.txt")
        self._domain_words = {
            line.strip().casefold()
            for line in words_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        } if words_path.exists() else set()
        if self._spell is not None and self._domain_words:
            self._spell.word_frequency.load_words(sorted(self._domain_words))

    def _suggestions(self, word: str) -> list[str]:
        if word in _KNOWN_CORRECTIONS:
            return [_KNOWN_CORRECTIONS[word]]
        if self._spell is None:
            return []
        candidates = set(self._spell.candidates(word) or ())
        candidates.discard(word)
        correction = self._spell.correction(word)
        ordered = []
        if correction and correction != word:
            ordered.append(correction)
        ordered.extend(sorted(candidates - set(ordered)))
        return ordered[:3]

    def _looks_like_person_name(self, word: str) -> bool:
        if self._morph is None:
            return False
        name_grammemes = {"Name", "Surn", "Patr"}
        return any(name_grammemes & set(parse.tag.grammemes) for parse in self._morph.parse(word))

    def check(self, text: Any) -> list[dict[str, Any]]:
        source = str(text or "")
        masked = _mask_protected(source)
        findings = []
        previous_word = None
        previous_word_end = None

        for match in _WORD_PATTERN.finditer(masked):
            original = match.group(0)
            word = original.casefold()
            has_latin = bool(re.search(r"[A-Za-z]", original))
            has_cyrillic = bool(re.search(r"[А-Яа-яЁё]", original))

            separator = masked[previous_word_end : match.start()] if previous_word_end is not None else ""
            heading_echo = bool(re.fullmatch(r"\s*:\s*", separator))
            if previous_word == word and len(word) > 1 and not heading_echo:
                findings.append(
                    {
                        "kind": "repeated_word",
                        "word": original,
                        "position": match.start(),
                        "suggestions": ["удалить повтор"],
                    }
                )
            previous_word = word
            previous_word_end = match.end()

            if has_latin and has_cyrillic:
                findings.append(
                    {
                        "kind": "mixed_script",
                        "word": original,
                        "position": match.start(),
                        "suggestions": [],
                    }
                )
                continue
            if not has_cyrillic or len(word) < 3 or word in self._domain_words or self._spell is None:
                continue
            if original[:1].isupper() and word not in _KNOWN_CORRECTIONS and self._looks_like_person_name(word):
                continue
            if self._morph is not None and self._morph.word_is_known(word):
                continue
            if word in self._spell.unknown([word]):
                suggestions = self._suggestions(word)
                if not suggestions:
                    continue
                findings.append(
                    {
                        "kind": "spelling",
                        "word": original,
                        "position": match.start(),
                        "suggestions": suggestions,
                    }
                )
        return findings


_CHECKER: RussianSpellingChecker | None = None


def get_spelling_checker() -> RussianSpellingChecker:
    global _CHECKER
    if _CHECKER is None:
        _CHECKER = RussianSpellingChecker()
    return _CHECKER
