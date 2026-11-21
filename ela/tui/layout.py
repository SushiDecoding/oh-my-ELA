"""Layout construction, key bindings, header/footer, and the spinner."""

from __future__ import annotations

import asyncio

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    BufferControl,
    ConditionalContainer,
    Dimension,
    Float,
    FloatContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import BeforeInput, PasswordProcessor
from prompt_toolkit.utils import get_cwidth

from .. import __version__
from ..config import HISTORY_PATH, shorten_cwd
from ..ui import LOGO_LINES, make_slash_completer
from .style import SPINNER, STYLE
from .transcript import TranscriptLexer


class LayoutMixin:
    """Layout construction and the header, footer, and spinner."""

    def _build_layout(self) -> None:
        self.transcript_buffer = Buffer(multiline=True, name="transcript")
        self.transcript_control = BufferControl(
            buffer=self.transcript_buffer,
            lexer=TranscriptLexer(self),
            focusable=False,
        )
        self.transcript_window = Window(
            content=self.transcript_control,
            height=Dimension(weight=1),
            wrap_lines=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
            style="class:transcript",
        )

        self.input_buffer = Buffer(
            multiline=True,
            history=FileHistory(str(HISTORY_PATH)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=make_slash_completer(),
            complete_while_typing=True,
            name="input",
        )
        self.input_control = BufferControl(
            buffer=self.input_buffer,
            focusable=True,
            key_bindings=self._input_bindings(),
            input_processors=[
                BeforeInput(
                    [("class:input.prompt", "❯ ")],
                    style="class:input.prompt",
                )
            ],
        )
        self.input_window = Window(
            content=self.input_control,
            height=Dimension(min=1, max=6),
            dont_extend_height=True,
            wrap_lines=True,
            style="class:input",
        )

        self.logo_window = Window(
            FormattedTextControl(self._logo_fragments),
            width=Dimension.exact(20),
            height=Dimension.exact(4),
            style="class:header.logo",
            dont_extend_height=True,
        )
        self.header_meta_window = Window(
            FormattedTextControl(self._header_meta_fragments),
            height=Dimension.exact(4),
            wrap_lines=False,
            style="class:header",
        )
        header = VSplit(
            [self.logo_window, self.header_meta_window],
            height=Dimension.exact(4),
        )
        footer = Window(
            FormattedTextControl(self._footer_fragments),
            height=1,
            style="class:footer",
        )
        separator = Window(height=1, char="─", style="class:separator")
        top_separator = Window(height=1, char="─", style="class:separator")

        self.effort_body = Window(
            content=FormattedTextControl(
                self._effort_fragments,
                focusable=True,
                key_bindings=self._effort_bindings(),
            ),
            height=Dimension.exact(8),
            style="class:modal",
            wrap_lines=True,
        )
        self.effort_window = self._panel(self.effort_body)
        self.permission_body = Window(
            content=FormattedTextControl(
                self._permission_fragments,
                focusable=True,
                key_bindings=self._permission_bindings(),
            ),
            height=Dimension.exact(12),
            style="class:modal",
            wrap_lines=True,
        )
        self.permission_window = self._panel(self.permission_body)

        self.login_url_buffer = Buffer(multiline=False, name="login-url")
        self.login_key_buffer = Buffer(multiline=False, name="login-key")
        login_bindings = self._login_bindings()
        self.login_url_window = Window(
            content=BufferControl(
                buffer=self.login_url_buffer,
                focusable=True,
                key_bindings=login_bindings,
            ),
            height=Dimension.exact(1),
            style="class:login.input",
        )
        self.login_key_window = Window(
            content=BufferControl(
                buffer=self.login_key_buffer,
                focusable=True,
                key_bindings=login_bindings,
                input_processors=[PasswordProcessor()],
            ),
            height=Dimension.exact(1),
            style="class:login.input",
        )
        self.provider_body = Window(
            content=FormattedTextControl(
                self._provider_fragments,
                focusable=True,
                key_bindings=self._provider_bindings(),
            ),
            height=Dimension.exact(11),
            style="class:modal",
            wrap_lines=True,
        )
        self.login_credentials = HSplit(
            [
                Window(
                    FormattedTextControl(self._login_title_fragments),
                    height=Dimension.exact(1),
                    style="class:modal",
                ),
                VSplit(
                    [
                        Window(
                            FormattedTextControl(
                                [("class:modal.label", "  Base URL   ")]
                            ),
                            width=Dimension.exact(14),
                        ),
                        self.login_url_window,
                    ]
                ),
                VSplit(
                    [
                        Window(
                            FormattedTextControl(
                                [("class:modal.label", "  API key    ")]
                            ),
                            width=Dimension.exact(14),
                        ),
                        self.login_key_window,
                    ]
                ),
                Window(
                    FormattedTextControl(self._login_error_fragments),
                    height=Dimension.exact(1),
                    style="class:modal",
                ),
                Window(
                    FormattedTextControl(self._login_hint_fragments),
                    height=Dimension.exact(1),
                    style="class:modal",
                ),
            ]
        )
        self.login_window = HSplit(
            [
                Window(height=1, char="─", style="class:separator"),
                ConditionalContainer(
                    self.provider_body,
                    filter=Condition(lambda: self.login_stage == "provider"),
                ),
                ConditionalContainer(
                    self.login_credentials,
                    filter=Condition(lambda: self.login_stage == "credentials"),
                ),
            ]
        )

        self.model_picker_body = Window(
            content=FormattedTextControl(
                self._model_picker_fragments,
                focusable=True,
                key_bindings=self._model_picker_bindings(),
            ),
            height=Dimension.exact(11),
            style="class:modal",
            wrap_lines=True,
        )
        self.model_picker_window = self._panel(self.model_picker_body)

        self.askuser_custom_buffer = Buffer(multiline=False, name="askuser-other")
        self.askuser_options_window = Window(
            content=FormattedTextControl(
                self._askuser_fragments,
                focusable=True,
                key_bindings=self._askuser_bindings(),
            ),
            height=Dimension.exact(9),
            style="class:modal",
            wrap_lines=True,
        )
        self.askuser_input_window = Window(
            content=BufferControl(
                buffer=self.askuser_custom_buffer,
                focusable=True,
                key_bindings=self._askuser_input_bindings(),
                input_processors=[
                    BeforeInput(
                        [("class:modal.label", "  Other: ")],
                        style="class:modal.label",
                    )
                ],
            ),
            height=Dimension.exact(1),
            style="class:login.input",
        )
        self.askuser_input_container = ConditionalContainer(
            self.askuser_input_window,
            filter=Condition(lambda: self.askuser_other),
        )
        self.askuser_hint_window = Window(
            FormattedTextControl(self._askuser_hint_fragments),
            height=Dimension.exact(1),
            style="class:modal",
        )
        self.askuser_window = HSplit(
            [
                Window(height=1, char="─", style="class:separator"),
                self.askuser_options_window,
                self.askuser_input_container,
                self.askuser_hint_window,
            ]
        )
        self.modal_container = HSplit(
            [
                ConditionalContainer(
                    self.effort_window,
                    filter=Condition(lambda: self.effort_open),
                ),
                ConditionalContainer(
                    self.permission_window,
                    filter=Condition(lambda: self.permission_open),
                ),
                ConditionalContainer(
                    self.login_window,
                    filter=Condition(lambda: self.login_open),
                ),
                ConditionalContainer(
                    self.model_picker_window,
                    filter=Condition(lambda: self.model_picker_open),
                ),
                ConditionalContainer(
                    self.askuser_window,
                    filter=Condition(lambda: self.askuser_open),
                ),
            ]
        )
        root = HSplit(
            [
                header,
                top_separator,
                self.transcript_window,
                self.modal_container,
                separator,
                self.input_window,
                footer,
            ]
        )
        root = FloatContainer(
            content=root,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=8),
                ),
            ],
        )
        self.app = Application(
            layout=Layout(root, focused_element=self.input_window),
            full_screen=True,
            mouse_support=False,
            style=STYLE,
            input=self._input,
            output=self._output,
        )

    def _panel(self, body: Window) -> HSplit:
        """A modal panel rendered above the input row, with no fill."""
        return HSplit(
            [
                Window(height=1, char="─", style="class:separator"),
                body,
            ]
        )

    def _input_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _submit(event):
            if self.busy:
                return
            text = event.current_buffer.text
            if not text.strip():
                return
            # Keep the input history working in the full-screen UI too.
            event.current_buffer.append_to_history()
            event.current_buffer.text = ""
            self._submit_text(text.strip())

        @kb.add("escape", "enter")
        @kb.add("c-j")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        @kb.add("pageup")
        def _page_up(event):
            self._scroll_page(-1)

        @kb.add("pagedown")
        def _page_down(event):
            self._scroll_page(1)

        @kb.add("c-c")
        def _cancel(event):
            if self.busy:
                self.status_label = "cancelling"
                if self._agent is not None:
                    self._agent.cancel()
            else:
                event.current_buffer.text = ""

        @kb.add("c-d")
        def _exit(event):
            if event.current_buffer.text:
                event.current_buffer.delete()
            elif not self.busy:
                event.app.exit()

        @kb.add("c-o")
        def _verbose(event):
            self.verbose = not self.verbose
            self._post("info", f"verbose {'on' if self.verbose else 'off'}")

        @kb.add("s-tab")
        def _cycle_permission(event):
            if self._agent is None:
                return
            self._agent.permissions.cycle()
            self.permission_label = self._agent.permissions.label
            self._append_info(f"permissions → {self.permission_label}")
            self.app.invalidate()

        return kb

    def _term_size(self) -> tuple[int, int]:
        try:
            size = get_app().output.get_size()
            return size.rows, size.columns
        except Exception:
            return 24, 80

    def _pad(
        self,
        left: list[tuple[str, str]],
        right: list[tuple[str, str]],
        style: str,
    ) -> list[tuple[str, str]]:
        width = self._term_size()[1]
        left_width = sum(get_cwidth(text) for _, text in left)
        right_width = sum(get_cwidth(text) for _, text in right)
        gap = max(1, width - left_width - right_width)
        return [*left, (style, " " * gap), *right]

    def _logo_fragments(self) -> list[tuple[str, str]]:
        return [("class:header.logo", "\n".join(LOGO_LINES))]

    def _header_meta_fragments(self) -> list[tuple[str, str]]:
        width = self._term_size()[1]
        cwd = shorten_cwd(self.cwd, max(20, width - 28))
        return [
            ("class:header.title", "  oh-my-elagent"),
            ("class:header.meta", f"  v{__version__}\n"),
            ("class:header.meta", f"  cwd  {cwd}\n"),
            ("class:header.meta", "  /help  /login  /model\n"),
            (
                "class:header.meta",
                "  shift+tab permissions · /effort depth\n",
            ),
        ]

    def _footer_fragments(self) -> list[tuple[str, str]]:
        if self.permission_open:
            left = [("class:footer.busy", "  permission needed")]
        elif self.busy:
            frame = SPINNER[self.spin_frame % len(SPINNER)]
            label = self.status_label[:44]
            left = [("class:footer.busy", f"  {frame} {label}…")]
        else:
            left = [("class:footer.accent", "  ready")]
        identity = self.model if self.logged_in else "not logged in · /login"
        identity_style = "class:footer" if self.logged_in else "class:footer.warn"
        left.append((identity_style, f"  ·  {identity[:30]}"))
        mode_style = {
            "Auto Mode": "class:footer.full",
            "Ask Mode": "class:footer.ask",
            "Readonly Mode": "class:footer.readonly",
            "AutoReview Mode": "class:footer.autoreview",
        }.get(self.permission_label, "class:footer")
        left.append((mode_style, f"  ·  {self.permission_label}"))
        left.append(("class:footer", f"  ·  {self.effort_level}"))
        if self.ultracode:
            left.append(("class:footer.ultra", "  ·  ultra"))
        if self.goal_total:
            left.append(
                (
                    "class:footer.goal",
                    f"  ·  goal {self.goal_done}/{self.goal_total}",
                )
            )
        left.append(("class:footer", f"  ·  ctx {self.context_percent}%"))
        if self.verbose:
            left.append(("class:footer", "  ·  verbose"))
        if self.permission_open:
            hint = "y allow · n deny"
        elif not self.logged_in:
            hint = "/login"
        elif self.busy:
            hint = "ctrl+c cancel · PgUp/PgDn"
        else:
            hint = "PgUp/PgDn · ctrl+j · ctrl+d"
        right = [("class:footer", hint), ("class:footer", "  ")]
        return self._pad(left, right, "class:footer")

    def _start_spinner(self) -> None:
        if self._spin_task is not None and not self._spin_task.done():
            return
        try:
            self._spin_task = self.app.loop.create_task(self._spin())
        except Exception:
            self._spin_task = None

    async def _spin(self) -> None:
        try:
            while self.busy:
                self.spin_frame += 1
                self.app.invalidate()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            self.spin_frame = 0
            self._spin_task = None
            if self._app_running:
                self.app.invalidate()
