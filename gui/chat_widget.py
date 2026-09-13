"""聊天组件：消息气泡 + Markdown 渲染 + 代码高亮 + 流式节流。

设计：
- 维护消息列表，每次更新用 markdown+pygments 转 HTML，setHtml 到 QTextBrowser
- 流式 content 高频追加时用 QTimer 节流（50ms 一次），避免卡顿
- 用户/助手/工具/错误用不同 CSS 气泡样式
- reasoning 用 <details> 折叠
- Ctrl+Enter 发送，Enter 换行
"""
from __future__ import annotations

import html as html_lib
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

try:
    import markdown as md_lib
    from pygments.formatters import HtmlFormatter

    _MD_AVAILABLE = True
except ImportError:
    _MD_AVAILABLE = False


# Pygments 代码高亮 CSS
_HIGHLIGHT_CSS = HtmlFormatter(style="friendly").get_style_defs(".codehilite") if _MD_AVAILABLE else ""


def _build_chat_css(theme: str) -> str:
    """按主题生成聊天区 HTML 样式。"""
    if theme == "dark":
        return f"""
<style>
{_HIGHLIGHT_CSS}
body {{ font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 14px; line-height: 1.6; color: #e0e0e0; }}
.msg {{ margin: 8px 0; padding: 10px 14px; border-radius: 8px; max-width: 85%; word-wrap: break-word; color: #e0e0e0; }}
.user {{ background-color: #1f3d24; margin-left: 15%; }}
.assistant {{ background-color: #2b2b2b; margin-right: 5%; }}
.tool {{ background-color: #3a2f0a; border-left: 3px solid #ffc107; font-family: Consolas, monospace; font-size: 12px; color: #f3e5ab; }}
.result {{ background-color: #0d2a3a; border-left: 3px solid #2196f3; font-family: Consolas, monospace; font-size: 12px; white-space: pre-wrap; color: #b3e5fc; }}
.error {{ background-color: #3d1a1a; border-left: 3px solid #f44336; color: #ff8a80; }}
.reasoning {{ background-color: #2a2a2a; border-left: 3px solid #9e9e9e; font-size: 12px; color: #aaa; margin: 4px 0; }}
.role-tag {{ font-size: 11px; color: #888; margin-bottom: 4px; font-weight: bold; }}
pre {{ background-color: #1a1a1a; padding: 8px; border-radius: 4px; overflow-x: auto; color: #e0e0e0; }}
code {{ font-family: Consolas, "Courier New", monospace; }}
p {{ margin: 4px 0; }}
</style>
"""
    return f"""
<style>
{_HIGHLIGHT_CSS}
body {{ font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 14px; line-height: 1.6; }}
.msg {{ margin: 8px 0; padding: 10px 14px; border-radius: 8px; max-width: 85%; word-wrap: break-word; }}
.user {{ background-color: #dcf8c6; margin-left: 15%; }}
.assistant {{ background-color: #f0f0f0; margin-right: 5%; }}
.tool {{ background-color: #fff8e1; border-left: 3px solid #ffc107; font-family: Consolas, monospace; font-size: 12px; }}
.result {{ background-color: #f5f5f5; border-left: 3px solid #2196f3; font-family: Consolas, monospace; font-size: 12px; white-space: pre-wrap; }}
.error {{ background-color: #ffebee; border-left: 3px solid #f44336; color: #c62828; }}
.reasoning {{ background-color: #fafafa; border-left: 3px solid #9e9e9e; font-size: 12px; color: #666; margin: 4px 0; }}
.role-tag {{ font-size: 11px; color: #888; margin-bottom: 4px; font-weight: bold; }}
pre {{ background-color: #f8f8f8; padding: 8px; border-radius: 4px; overflow-x: auto; }}
code {{ font-family: Consolas, "Courier New", monospace; }}
p {{ margin: 4px 0; }}
</style>
"""


def _view_bg(theme: str) -> str:
    return "#1e1e1e" if theme == "dark" else "#fafafa"


@dataclass
class ChatMessage:
    """一条聊天消息（用于渲染）。"""
    role: str  # user | assistant | tool | result | error
    content: str = ""
    reasoning: str = ""
    tool_name: str = ""
    tool_args: str = ""
    tool_result: str = ""
    # 标记是否是流式进行中的助手消息（用于增量更新）
    streaming: bool = False


class InputEdit(QPlainTextEdit):
    """输入框：Ctrl+Enter 发送，Enter 换行。"""

    send_pressed = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.send_pressed.emit()
            return
        super().keyPressEvent(event)


class ChatWidget(QWidget):
    """聊天区组件。"""

    message_sent = Signal(str)
    stop_requested = Signal()

    def __init__(self, theme: str = "light", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.theme = theme if theme in ("light", "dark") else "light"
        self._css = _build_chat_css(self.theme)
        self._messages: list[ChatMessage] = []
        self._dirty = False
        self._init_ui()
        # 渲染节流定时器
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(50)
        self._render_timer.timeout.connect(self._render)

    def set_theme(self, theme: str) -> None:
        """切换主题并重渲染。"""
        theme = theme if theme in ("light", "dark") else "light"
        if theme == self.theme:
            return
        self.theme = theme
        self._css = _build_chat_css(self.theme)
        self.view.setStyleSheet(f"QTextBrowser {{ background-color: {_view_bg(self.theme)}; border: none; }}")
        self._mark_dirty()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 消息显示区
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet(f"QTextBrowser {{ background-color: {_view_bg(self.theme)}; border: none; }}")
        layout.addWidget(self.view, stretch=1)

        # 输入区
        self.input_edit = InputEdit()
        self.input_edit.setPlaceholderText("输入消息...（Ctrl+Enter 发送）")
        self.input_edit.setMaximumHeight(120)
        self.input_edit.send_pressed.connect(self._on_send)
        layout.addWidget(self.input_edit)

        # 按钮行
        btn_layout = QVBoxLayout()
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._on_send)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.stop_btn.setEnabled(False)
        btn_layout.addWidget(self.send_btn)
        btn_layout.addWidget(self.stop_btn)
        layout.addLayout(btn_layout)

        self._render()

    def _on_send(self) -> None:
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        self.input_edit.clear()
        self.message_sent.emit(text)

    def set_busy(self, busy: bool) -> None:
        """忙碌时禁用发送、启用停止。"""
        self.send_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    # ---- 消息追加接口 ----

    def append_user(self, text: str) -> None:
        self._messages.append(ChatMessage(role="user", content=text))
        self._mark_dirty()

    def append_assistant(self, text: str) -> None:
        """追加一条完整的助手消息（用于加载历史等非流式场景）。"""
        self._messages.append(ChatMessage(role="assistant", content=text))
        self._mark_dirty()

    def start_assistant(self) -> None:
        """开始一条新的助手消息（流式）。"""
        self._messages.append(ChatMessage(role="assistant", content="", streaming=True))
        self._mark_dirty()

    def append_content(self, text: str) -> None:
        """流式追加助手回答文本。"""
        if self._messages and self._messages[-1].role == "assistant":
            self._messages[-1].content += text
            self._mark_dirty()

    def append_reasoning(self, text: str) -> None:
        """流式追加推理文本。"""
        if self._messages and self._messages[-1].role == "assistant":
            self._messages[-1].reasoning += text
            self._mark_dirty()

    def append_tool_call(self, name: str, args_json: str) -> None:
        """追加工具调用记录。"""
        # 先结束当前流式助手消息
        if self._messages and self._messages[-1].streaming:
            self._messages[-1].streaming = False
        self._messages.append(
            ChatMessage(role="tool", tool_name=name, tool_args=args_json)
        )
        self._mark_dirty()

    def append_tool_result(self, name: str, result: str) -> None:
        """追加工具结果记录。"""
        self._messages.append(
            ChatMessage(role="result", tool_name=name, tool_result=result)
        )
        self._mark_dirty()

    def append_error(self, text: str) -> None:
        if self._messages and self._messages[-1].streaming:
            self._messages[-1].streaming = False
        self._messages.append(ChatMessage(role="error", content=text))
        self._mark_dirty()

    def finish_assistant(self) -> None:
        """标记当前助手消息流式结束。"""
        if self._messages and self._messages[-1].streaming:
            self._messages[-1].streaming = False
        self._mark_dirty()

    def clear_all(self) -> None:
        self._messages.clear()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._dirty = True
        # 节流：50ms 后渲染（如果还没启动）
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render(self) -> None:
        """把消息列表渲染成 HTML 显示。"""
        self._dirty = False
        parts = [self._css]
        for msg in self._messages:
            parts.append(self._render_message(msg))
        self.view.setHtml("".join(parts))
        # 滚动到底部
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.view.setTextCursor(cursor)

    def _render_message(self, msg: ChatMessage) -> str:
        if msg.role == "user":
            body = _md_to_html(msg.content)
            return f'<div class="msg user"><div class="role-tag">你</div>{body}</div>'
        elif msg.role == "assistant":
            body = _md_to_html(msg.content) if msg.content else '<i style="color:#999">思考中...</i>'
            reasoning = ""
            if msg.reasoning:
                reasoning = (
                    f'<details class="reasoning"><summary>推理过程</summary>'
                    f'<div>{html_lib.escape(msg.reasoning)}</div></details>'
                )
            return f'<div class="msg assistant"><div class="role-tag">助手</div>{reasoning}{body}</div>'
        elif msg.role == "tool":
            args_display = html_lib.escape(msg.tool_args)
            return (
                f'<div class="msg tool"><div class="role-tag">🔧 调用工具: '
                f'{html_lib.escape(msg.tool_name)}</div><pre>{args_display}</pre></div>'
            )
        elif msg.role == "result":
            result_display = html_lib.escape(msg.tool_result)
            return (
                f'<div class="msg result"><div class="role-tag">📋 结果: '
                f'{html_lib.escape(msg.tool_name)}</div><pre>{result_display}</pre></div>'
            )
        elif msg.role == "error":
            return f'<div class="msg error">⚠️ {html_lib.escape(msg.content)}</div>'
        return ""


def _md_to_html(text: str) -> str:
    """Markdown 转 HTML。无 markdown 库时退化为转义文本。"""
    if not _MD_AVAILABLE:
        return html_lib.escape(text).replace("\n", "<br>")
    try:
        return md_lib.markdown(
            text,
            extensions=["codehilite", "fenced_code", "tables", "nl2br"],
            extension_configs={"codehilite": {"css_class": "codehilite"}},
        )
    except Exception:
        return html_lib.escape(text).replace("\n", "<br>")
