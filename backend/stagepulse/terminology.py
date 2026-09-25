"""Explicit, per-language caption terminology replacements."""

from __future__ import annotations

import re
from collections.abc import Mapping


class TerminologyNormalizer:
    """Apply configured terms at word boundaries without changing other text."""

    def __init__(self, terminology: Mapping[str, Mapping[str, str]] | None = None) -> None:
        self._rules: dict[str, tuple[re.Pattern[str], dict[str, str]]] = {}
        if terminology is None:
            return
        if not isinstance(terminology, Mapping):
            raise ValueError("Terminology must map languages to replacement maps")
        for language, replacements in terminology.items():
            if not isinstance(language, str) or not isinstance(replacements, Mapping):
                raise ValueError("Terminology languages must map to replacement maps")
            if any(
                not isinstance(source, str)
                or not source.strip()
                or not isinstance(target, str)
                or not target.strip()
                for source, target in replacements.items()
            ):
                raise ValueError("Terminology replacements must contain nonempty strings")
            if not replacements:
                continue
            targets = {source.casefold(): target for source, target in replacements.items()}
            if len(targets) != len(replacements):
                raise ValueError("Terminology sources must be unique ignoring case")
            alternatives = "|".join(
                re.escape(source)
                for source in sorted(replacements, key=len, reverse=True)
            )
            pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)
            self._rules[language] = (pattern, targets)

    def apply(self, language: str, text: str) -> str:
        rule = self._rules.get(language)
        if rule is None:
            return text
        pattern, targets = rule
        return pattern.sub(lambda match: targets[match.group().casefold()], text)
