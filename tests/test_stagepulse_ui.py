"""Small consistency checks for the browser language catalogs."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class UiLanguageTests(unittest.TestCase):
    def test_language_catalogs_have_matching_keys(self) -> None:
        keys = {}
        for language in ("en", "es"):
            source = (FRONTEND / "locales" / f"{language}.js").read_text(encoding="utf-8")
            keys[language] = set(re.findall(r"^  (\w+):", source, flags=re.MULTILINE))
        self.assertEqual(keys["en"], keys["es"])

    def test_visible_html_translation_keys_exist_in_both_catalogs(self) -> None:
        catalogs = {
            language: set(re.findall(
                r"^  (\w+):", (FRONTEND / "locales" / f"{language}.js").read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            )) for language in ("en", "es")
        }
        for page in ("stage", "audience", "control"):
            html = (FRONTEND / f"{page}.html").read_text(encoding="utf-8")
            keys = set(re.findall(r'data-i18n(?:-alt|-aria-label)?="(\w+)"', html))
            for language in ("en", "es"):
                self.assertFalse(keys - catalogs[language], (page, language, keys - catalogs[language]))

    def test_visible_interface_controls_are_separate_from_caption_language(self) -> None:
        for page in ("stage", "audience", "control"):
            html = (FRONTEND / f"{page}.html").read_text(encoding="utf-8")
            self.assertIn('id="ui-language"', html)
        audience = (FRONTEND / "audience.html").read_text(encoding="utf-8")
        self.assertIn('id="language"', audience)
        self.assertIn('data-i18n="captionLanguage"', audience)

    def test_audience_waiting_message_has_both_languages(self) -> None:
        audience = (FRONTEND / "audience.js").read_text(encoding="utf-8")
        self.assertIn('"waitingForCaptions"', audience)
        self.assertIn('"captionsReconnecting"', audience)
        self.assertIn('waitingForCaptions: "Waiting for captions..."',
                      (FRONTEND / "locales/en.js").read_text(encoding="utf-8"))
        self.assertIn('waitingForCaptions: "Esperando subtítulos..."',
                      (FRONTEND / "locales/es.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
