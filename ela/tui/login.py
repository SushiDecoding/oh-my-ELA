"""Login flow: provider picker, credentials, and the model picker."""

from __future__ import annotations

import threading

from prompt_toolkit.key_binding import KeyBindings

from ..providers import PROVIDERS
from .style import MODEL_PAGE, PROVIDER_PAGE


class LoginMixin:
    """Login and model-picker modals."""

    def _provider_fragments(self) -> list[tuple[str, str]]:
        width = max(30, self._term_size()[1] - 6)
        lines = [
            ("class:modal.title", "  Login · choose a provider\n"),
            ("class:modal.sep", "  " + "─" * 56 + "\n"),
        ]
        start = self.provider_offset
        end = min(start + PROVIDER_PAGE, len(PROVIDERS))
        for index in range(start, end):
            provider = PROVIDERS[index]
            selected = index == self.provider_index
            marker = "▸ " if selected else "  "
            url = provider.base_url or "custom URL"
            text = f"  {marker}{provider.label:<22} {url}"
            text = text[:width]
            style = "class:modal.selected" if selected else "class:modal.text"
            lines.append((style, text + "\n"))
        lines.append(
            (
                "class:modal.hint",
                f"  ↑/↓ select · Enter next · Esc cancel · "
                f"{self.provider_index + 1}/{len(PROVIDERS)}",
            )
        )
        return lines

    def _move_provider(self, delta: int) -> None:
        if not PROVIDERS:
            return
        self.provider_index = max(
            0, min(len(PROVIDERS) - 1, self.provider_index + delta)
        )
        if self.provider_index < self.provider_offset:
            self.provider_offset = self.provider_index
        elif self.provider_index >= self.provider_offset + PROVIDER_PAGE:
            self.provider_offset = self.provider_index - PROVIDER_PAGE + 1
        self.app.invalidate()

    def _provider_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("up")
        @kb.add("k")
        def _up(event):
            self._move_provider(-1)

        @kb.add("down")
        @kb.add("j")
        def _down(event):
            self._move_provider(1)

        @kb.add("pageup")
        def _page_up(event):
            self._move_provider(-PROVIDER_PAGE)

        @kb.add("pagedown")
        def _page_down(event):
            self._move_provider(PROVIDER_PAGE)

        @kb.add("enter")
        def _choose(event):
            self._choose_provider()

        @kb.add("escape")
        @kb.add("c-c")
        def _cancel(event):
            self._close_login()

        return kb

    def _choose_provider(self) -> None:
        provider = PROVIDERS[self.provider_index]
        self.login_provider = provider
        self.login_stage = "credentials"
        self.login_error = ""
        self.login_url_buffer.text = (
            provider.base_url or self.login_url_buffer.text
        )
        self.login_key_buffer.text = ""
        if provider.base_url:
            self.app.layout.focus(self.login_key_window)
        else:
            self.app.layout.focus(self.login_url_window)
        self.app.invalidate()

    def _login_title_fragments(self) -> list[tuple[str, str]]:
        label = (
            self.login_provider.label
            if self.login_provider is not None
            else "Custom endpoint"
        )
        return [("class:modal.title", f"  Login · {label}\n")]

    def _login_error_fragments(self) -> list[tuple[str, str]]:
        if not self.login_error:
            return [("class:modal", "")]
        return [("class:modal.error", "  " + self.login_error)]

    def _login_hint_fragments(self) -> list[tuple[str, str]]:
        if self.login_connecting:
            return [
                (
                    "class:modal.hint",
                    "  connecting and probing models · please wait",
                )
            ]
        optional = (
            self.login_provider is not None
            and not self.login_provider.needs_key
        )
        key = "optional" if optional else "required"
        return [
            (
                "class:modal.hint",
                f"  Tab switch · Enter connect · Esc back · key {key}",
            )
        ]

    def _login_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("tab")
        def _switch_field(event):
            if self.login_connecting:
                return
            if self.app.layout.current_window is self.login_url_window:
                self.app.layout.focus(self.login_key_window)
            else:
                self.app.layout.focus(self.login_url_window)

        @kb.add("enter")
        def _submit(event):
            self._submit_login()

        @kb.add("escape")
        def _back(event):
            if self.login_connecting:
                return
            self._back_to_providers()

        @kb.add("c-c")
        def _cancel(event):
            if self.login_connecting:
                return
            self._close_login()

        return kb

    def _submit_login(self) -> None:
        if self.login_connecting:
            return
        base_url = self.login_url_buffer.text.strip().rstrip("/")
        api_key = self.login_key_buffer.text.strip()
        provider = self.login_provider
        if not base_url.startswith(("http://", "https://")):
            self.login_error = "Base URL must start with http:// or https://"
            self.app.invalidate()
            return
        if provider is not None and provider.needs_key and not api_key:
            self.login_error = "API key is required for this provider."
            self.app.invalidate()
            return
        self.login_error = ""
        self.login_connecting = True
        self.busy = True
        self.status_label = "connecting"
        self._start_spinner()
        self.app.invalidate()
        threading.Thread(
            target=self._run_login,
            args=(
                self.login_callback,
                self.login_on_model,
                self.login_current_model,
                base_url,
                api_key,
            ),
            daemon=True,
        ).start()

    def _run_login(self, callback, on_model, current, base_url, api_key) -> None:
        try:
            if callback is None:
                models, error = [], "login not configured"
            else:
                models, error = callback(base_url, api_key)
        except Exception as exc:
            models, error = [], f"{type(exc).__name__}: {exc}"
        self._post(
            "login_result", list(models or []), error or "", on_model, current
        )

    def _on_login_result(self, models, error, on_model, current: str) -> None:
        self.login_connecting = False
        self.busy = False
        self.status_label = "ready"
        if error:
            self.login_error = error
            self.app.layout.focus(self.login_key_window)
            self.app.invalidate()
            return
        self._close_login()
        if not models:
            self._append_warn("the endpoint returned no models")
            self._append_info("set one later with /model <id>")
            return
        if (
            self.input_buffer.text.strip()
            or self.askuser_open
            or self.effort_open
            or self.permission_open
        ):
            self._append_info("models ready · use /model to choose")
            return
        self._open_model_picker(models, current, on_model, note="saved")

    def _back_to_providers(self) -> None:
        self.login_stage = "provider"
        self.login_error = ""
        self.app.layout.focus(self.provider_body)
        self.app.invalidate()

    def _open_login(
        self,
        callback,
        on_model,
        base_url: str,
        current_model: str,
    ) -> None:
        self.login_callback = callback
        self.login_on_model = on_model
        self.login_current_model = current_model
        self.login_connecting = False
        self.login_error = ""
        self.login_stage = "provider"
        self.login_provider = None
        self.login_url_buffer.text = base_url or ""
        self.login_key_buffer.text = ""
        self.provider_index = 0
        self.provider_offset = 0
        if base_url:
            matched = False
            for index, provider in enumerate(PROVIDERS):
                if provider.base_url == base_url:
                    self.provider_index = index
                    self.provider_offset = max(0, index - PROVIDER_PAGE + 1)
                    matched = True
                    break
            if not matched:
                custom = next(
                    (
                        index
                        for index, provider in enumerate(PROVIDERS)
                        if provider.id == "custom"
                    ),
                    0,
                )
                self.provider_index = custom
                self.provider_offset = max(0, custom - PROVIDER_PAGE + 1)
        self.login_open = True
        self.app.layout.focus(self.provider_body)
        self.app.invalidate()

    def _close_login(self) -> None:
        self.login_open = False
        self.login_connecting = False
        self.login_callback = None
        self.login_on_model = None
        self.login_current_model = ""
        self.login_provider = None
        self.login_error = ""
        self.login_stage = "provider"
        self.login_key_buffer.text = ""
        self.app.layout.focus(self.input_window)
        self.app.invalidate()

    def _model_picker_fragments(self) -> list[tuple[str, str]]:
        width = max(24, self._term_size()[1] - 6)
        lines = [
            (
                "class:modal.title",
                f"  Select model · {len(self.model_picker_models)}\n",
            ),
            ("class:modal.sep", "  " + "─" * 56 + "\n"),
        ]
        start = self.model_picker_offset
        end = min(start + MODEL_PAGE, len(self.model_picker_models))
        for index in range(start, end):
            model = self.model_picker_models[index]
            selected = index == self.model_picker_index
            marker = "▸ " if selected else "  "
            current = "  *" if model == self.model_picker_current else ""
            label = f"  {marker}{model[: width - 16]}{current}"
            style = "class:modal.selected" if selected else "class:modal.text"
            lines.append((style, label + "\n"))
        total = len(self.model_picker_models)
        lines.append(
            (
                "class:modal.hint",
                f"  ↑/↓ select · Enter use · Esc cancel"
                + (f" · {self.model_picker_index + 1}/{total}" if total else ""),
            )
        )
        return lines

    def _move_model_picker(self, delta: int) -> None:
        if not self.model_picker_models:
            return
        self.model_picker_index = max(
            0,
            min(
                len(self.model_picker_models) - 1,
                self.model_picker_index + delta,
            ),
        )
        if self.model_picker_index < self.model_picker_offset:
            self.model_picker_offset = self.model_picker_index
        elif (
            self.model_picker_index
            >= self.model_picker_offset + MODEL_PAGE
        ):
            self.model_picker_offset = self.model_picker_index - MODEL_PAGE + 1
        self.app.invalidate()

    def _model_picker_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("up")
        @kb.add("k")
        def _up(event):
            self._move_model_picker(-1)

        @kb.add("down")
        @kb.add("j")
        def _down(event):
            self._move_model_picker(1)

        @kb.add("pageup")
        def _page_up(event):
            self._move_model_picker(-MODEL_PAGE)

        @kb.add("pagedown")
        def _page_down(event):
            self._move_model_picker(MODEL_PAGE)

        @kb.add("enter")
        def _select(event):
            if not self.model_picker_models:
                return
            model = self.model_picker_models[self.model_picker_index]
            callback = self.model_picker_callback
            note = self.model_picker_note
            self._close_model_picker()
            if callback is not None:
                callback(model)
            self._append_info(f"model → {model} ({note})")

        @kb.add("escape")
        @kb.add("c-c")
        def _cancel(event):
            self._close_model_picker()

        return kb

    def _open_model_picker(
        self, models, current: str, on_select, note: str = "session only"
    ) -> None:
        self.model_picker_models = list(models)
        self.model_picker_current = current
        self.model_picker_callback = on_select
        self.model_picker_note = note
        try:
            self.model_picker_index = self.model_picker_models.index(current)
        except ValueError:
            self.model_picker_index = 0
        self.model_picker_offset = max(
            0, self.model_picker_index - MODEL_PAGE + 1
        )
        self.model_picker_open = True
        self.app.layout.focus(self.model_picker_body)
        self.app.invalidate()

    def _close_model_picker(self) -> None:
        self.model_picker_open = False
        self.model_picker_callback = None
        self.app.layout.focus(self.input_window)
        self.app.invalidate()
