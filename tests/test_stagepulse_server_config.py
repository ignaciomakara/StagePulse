"""Server configuration precedence and public audience link tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from serve import load_server_config
from stagepulse.manager import StageManager
from stagepulse.models import StageConfig
from stagepulse.web import create_app


class ServerConfigTests(unittest.TestCase):
    def test_dotenv_public_url_process_override_and_request_fallback(self) -> None:
        with tempfile.NamedTemporaryFile(
            dir=Path(__file__).resolve().parents[1], suffix=".settings", delete=False
        ) as temporary:
            env_file = Path(temporary.name)
        try:
            manager = StageManager(
                [StageConfig("main", "Main", "en", "es", Path("unused.wav"))],
                "unit-test-placeholder",
            )
            env_file.write_text(
                "GEMINI_API_KEY=unit-test-placeholder\n"
                "STAGEPULSE_PUBLIC_BASE_URL=https://captions.example.test\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                api_key, public_url = load_server_config(env_file)
                self.assertEqual(api_key, "unit-test-placeholder")
                with TestClient(create_app(manager, public_base_url=public_url)) as client:
                    self.assertEqual(
                        client.get("/api/stages/main/audience-link").json()["url"],
                        "https://captions.example.test/audience/main",
                    )

            with patch.dict(
                os.environ,
                {
                    "STAGEPULSE_PUBLIC_BASE_URL": "https://process.example.test",
                    "GEMINI_API_KEY": "ignored-process-key",
                },
                clear=True,
            ):
                api_key, public_url = load_server_config(env_file)
                self.assertEqual(api_key, "unit-test-placeholder")
                self.assertEqual(public_url, "https://process.example.test")

            for optional_setting in ("", "STAGEPULSE_PUBLIC_BASE_URL=\n"):
                with self.subTest(optional_setting=optional_setting):
                    env_file.write_text(
                        "GEMINI_API_KEY=unit-test-placeholder\n" + optional_setting,
                        encoding="utf-8",
                    )
                    with patch.dict(os.environ, {}, clear=True):
                        api_key, public_url = load_server_config(env_file)
                        self.assertEqual(api_key, "unit-test-placeholder")
                        with TestClient(create_app(manager, public_base_url=public_url)) as client:
                            self.assertEqual(
                                client.get("/api/stages/main/audience-link").json()["url"],
                                "http://testserver/audience/main",
                            )
        finally:
            env_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
