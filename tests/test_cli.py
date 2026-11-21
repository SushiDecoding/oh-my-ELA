from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ela.cli import handle_command, inline_login, restore_session_model
from ela.config import Config, has_credentials


class FakeAgent:
    explicit_model = False

    def __init__(self, model: str = "old-model"):
        self.config = SimpleNamespace(model=model)
        self.models: list[str] = []

    def set_model(self, model: str) -> None:
        self.config.model = model
        self.models.append(model)


class FakeSession:
    def __init__(self, model: str):
        self.model = model


class FakeUI:
    def __init__(self):
        self.messages: list[str] = []
        self.errors: list[str] = []
        self.answers: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)

    def warn(self, message: str) -> None:
        self.messages.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def ask(self, prompt: str = "") -> str:
        return self.answers.pop(0) if self.answers else ""

    def request_start(self, label: str = "thinking") -> None:
        pass

    def request_finish(self) -> None:
        pass


class RestoreSessionModelTests(unittest.TestCase):
    def test_restores_the_session_model(self):
        agent = FakeAgent("launch-model")
        ui = FakeUI()
        restore_session_model(agent, FakeSession("saved-model"), ui)
        self.assertEqual(agent.config.model, "saved-model")
        self.assertEqual(agent.models, ["saved-model"])
        self.assertIn("saved-model", ui.messages[0])

    def test_explicit_model_wins(self):
        agent = FakeAgent("cli-model")
        agent.explicit_model = True
        restore_session_model(agent, FakeSession("saved-model"), FakeUI())
        self.assertEqual(agent.config.model, "cli-model")
        self.assertEqual(agent.models, [])

    def test_same_model_is_a_noop(self):
        agent = FakeAgent("same")
        ui = FakeUI()
        restore_session_model(agent, FakeSession("same"), ui)
        self.assertEqual(agent.models, [])
        self.assertEqual(ui.messages, [])


class CredentialTests(unittest.TestCase):
    def test_cloud_endpoint_without_key_is_not_logged_in(self):
        self.assertFalse(
            has_credentials(Config(base_url="https://api.deepseek.com/v1"))
        )

    def test_local_endpoint_without_key_is_logged_in(self):
        self.assertTrue(
            has_credentials(Config(base_url="http://127.0.0.1:1234/v1"))
        )

    def test_api_key_alone_is_enough(self):
        self.assertTrue(has_credentials(Config(api_key="test-key")))


class MaxStepCommandTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.agent = SimpleNamespace(config=self.config)
        self.ui = FakeUI()

    def run_command(self, line: str):
        with mock.patch("ela.cli.save_config") as save:
            result = handle_command(
                line, self.agent, self.ui, self.config, Path.cwd()
            )
        return result, save

    def test_default_is_unlimited(self):
        self.assertEqual(self.config.max_steps, 0)
        _, save = self.run_command("/maxstep")
        self.assertIn("unlimited", " ".join(self.ui.messages))
        save.assert_not_called()

    def test_set_limit_and_persist(self):
        _, save = self.run_command("/maxstep 60")
        self.assertEqual(self.config.max_steps, 60)
        save.assert_called_once()
        self.assertIn("60", self.ui.messages[-1])

    def test_off_clears_the_limit(self):
        self.config.max_steps = 60
        self.run_command("/maxstep off")
        self.assertEqual(self.config.max_steps, 0)
        self.assertIn("unlimited", self.ui.messages[-1])

    def test_invalid_value_is_rejected(self):
        self.run_command("/maxstep -2")
        self.assertEqual(self.config.max_steps, 0)
        self.assertIn("positive", self.ui.messages[-1])


class InlineLoginTests(unittest.TestCase):
    def test_cloud_provider_rejects_an_empty_key(self):
        ui = FakeUI()
        ui.answers = ["1"]  # OpenAI
        called = []
        with mock.patch("ela.cli.getpass.getpass", return_value=""):
            inline_login(
                FakeAgent(),
                ui,
                SimpleNamespace(),
                lambda url, key: called.append((url, key)) or ([], None),
            )
        self.assertEqual(called, [])
        self.assertTrue(ui.errors)
        self.assertIn("API key is required", ui.errors[0])


if __name__ == "__main__":
    unittest.main()
