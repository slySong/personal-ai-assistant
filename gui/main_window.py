"""主窗口：三栏布局 + AgentWorker 生命周期 + 确认弹窗 + 状态栏探活。

布局：
  左栏：会话列表（新建/切换/删除）
  中栏：ChatWidget（聊天区 + 输入区）
  右栏：记忆面板（显示偏好列表，P5 增强为可编辑）

线程模型：
  用户发消息 → 创建 AgentWorker 移到 QThread → 信号驱动 UI 更新
  确认机制：confirm_request 信号 → QMessageBox → receive_confirm_result
"""
from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, Slot
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from config import AppConfig, LLMConfig
from core.agent import Agent
from core.factory import build_agent
from core.llm_client import LLMClient
from gui.chat_widget import ChatWidget
from gui.memory_panel import MemoryPanel
from gui.settings_dialog import SettingsDialog
from gui.workers import AgentWorker


# 全局深色主题 QSS（浅色主题走系统默认，不设 QSS）
_GLOBAL_DARK_QSS = """
QMainWindow, QDialog, QWidget { background-color: #1e1e1e; color: #e0e0e0; }
QLabel { color: #e0e0e0; }
QListWidget, QPlainTextEdit, QTextBrowser, QLineEdit, QComboBox {
    background-color: #252526; color: #e0e0e0;
    border: 1px solid #3a3a3a; border-radius: 4px;
    selection-background-color: #094771;
}
QPushButton {
    background-color: #333333; color: #e0e0e0;
    border: 1px solid #3a3a3a; border-radius: 4px; padding: 4px 10px;
}
QPushButton:hover { background-color: #3d3d3d; }
QPushButton:pressed { background-color: #2a2a2a; }
QPushButton:disabled { color: #666666; }
QMenuBar { background-color: #1e1e1e; color: #e0e0e0; }
QMenuBar::item:selected { background-color: #094771; }
QMenu { background-color: #252526; color: #e0e0e0; }
QMenu::item:selected { background-color: #094771; }
QStatusBar { background-color: #1e1e1e; color: #e0e0e0; }
QSplitter::handle { background-color: #3a3a3a; }
QScrollBar:vertical { background: #252526; }
QScrollBar::handle:vertical { background: #3a3a3a; border-radius: 3px; min-height: 20px; }
"""


def apply_global_theme(app, theme: str) -> None:
    """应用全局主题 QSS。dark 用深色，light 恢复系统默认。"""
    app.setStyleSheet(_GLOBAL_DARK_QSS if theme == "dark" else "")


class MainWindow(QMainWindow):
    def __init__(self, app_cfg: AppConfig, llm_cfg: LLMConfig):
        super().__init__()
        self.app_cfg = app_cfg
        self.llm_cfg = llm_cfg
        self.setWindowTitle("私人 AI 助手")
        self.resize(app_cfg.window_width, app_cfg.window_height)

        # Agent（含 conversation + memory + tools）
        self.agent: Agent = build_agent(app_cfg, llm_cfg)
        self.current_session_id: Optional[int] = None

        # Worker 生命周期
        self._worker: Optional[AgentWorker] = None
        self._thread: Optional[QThread] = None

        self._init_ui()
        self._init_menu()
        self._init_status()

        # 启动时探活
        QTimer.singleShot(100, self._check_ollama)
        # 定期探活（每 30 秒）
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(30000)
        self._health_timer.timeout.connect(self._check_ollama)
        self._health_timer.start()

        # 初始化会话
        self._refresh_sessions()
        self._refresh_memory()

    # ---- UI 初始化 ----

    def _init_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        # 左栏：会话列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)
        self.session_list = QListWidget()
        self.session_list.itemClicked.connect(self._on_session_clicked)
        new_btn = QPushButton("+ 新建会话")
        new_btn.clicked.connect(self._new_session)
        del_btn = QPushButton("删除当前会话")
        del_btn.clicked.connect(self._delete_session)
        left_layout.addWidget(QLabel("会话"))
        left_layout.addWidget(self.session_list, stretch=1)
        left_layout.addWidget(new_btn)
        left_layout.addWidget(del_btn)

        # 中栏：聊天区
        self.chat = ChatWidget(self.app_cfg.theme)
        self.chat.message_sent.connect(self._on_message_sent)
        self.chat.stop_requested.connect(self._on_stop)

        # 右栏：记忆面板（可编辑）
        self.memory_panel = MemoryPanel(self.agent.memory)
        self.memory_panel.changed.connect(self._on_memory_changed)

        splitter.addWidget(left)
        splitter.addWidget(self.chat)
        splitter.addWidget(self.memory_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        self.setCentralWidget(splitter)

    def _init_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction("新建会话", self._new_session, "Ctrl+N")
        file_menu.addAction("清空当前会话", self._clear_chat)
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close, "Ctrl+Q")

        set_menu = menubar.addMenu("设置(&S)")
        set_menu.addAction("Ollama 设置...", self._open_settings)
        set_menu.addSeparator()
        set_menu.addAction("切换深色/浅色主题", self._toggle_theme)

        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self._show_about)

    def _init_status(self) -> None:
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.ollama_label = QLabel("● 检查中...")
        self.model_label = QLabel(f"模型: {self.llm_cfg.model}")
        self.status.addWidget(self.ollama_label)
        self.status.addPermanentWidget(self.model_label)

    # ---- Ollama 探活 ----

    @Slot()
    def _check_ollama(self) -> None:
        client = LLMClient(self.llm_cfg)
        if client.is_reachable():
            self.ollama_label.setText("● 后端已连接")
            self.ollama_label.setStyleSheet("color: green;")
        else:
            self.ollama_label.setText("● 后端未连接")
            self.ollama_label.setStyleSheet("color: red;")

    # ---- 会话管理 ----

    def _refresh_sessions(self) -> None:
        self.session_list.clear()
        sessions = self.agent.conversation.list_sessions()
        for s in sessions:
            title = f"#{s['id']} {s['title']}"
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, s["id"])
            self.session_list.addItem(item)
        # 如果没有会话，自动新建
        if not sessions:
            self._new_session()
        else:
            # 默认选第一个
            self.session_list.setCurrentRow(0)
            self._on_session_clicked(self.session_list.item(0))

    def _new_session(self) -> None:
        sid = self.agent.conversation.create_session()
        self.current_session_id = sid
        self.chat.clear_all()
        self._refresh_sessions()
        # 选中新会话
        for i in range(self.session_list.count()):
            item = self.session_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == sid:
                self.session_list.setCurrentRow(i)
                break

    def _delete_session(self) -> None:
        if self.current_session_id is None:
            return
        if self._worker is not None:
            QMessageBox.information(self, "提示", "请等待当前任务完成或停止。")
            return
        sid = self.current_session_id
        self.agent.conversation.delete_session(sid)
        self.current_session_id = None
        self.chat.clear_all()
        self._refresh_sessions()

    def _clear_chat(self) -> None:
        if self.current_session_id is None:
            return
        self.chat.clear_all()
        self._refresh_memory()

    def _on_session_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        if self._worker is not None:
            QMessageBox.information(self, "提示", "请等待当前任务完成或停止。")
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        self.current_session_id = sid
        self._load_history(sid)

    def _load_history(self, session_id: int) -> None:
        """加载会话历史到聊天区。"""
        self.chat.clear_all()
        messages = self.agent.conversation.get_messages(session_id, limit=200)
        for m in messages:
            role = m["role"]
            content = m.get("content") or ""
            if role == "user":
                self.chat.append_user(content)
            elif role == "assistant":
                if content:
                    self.chat.append_user("")  # 占位避免
                    self.chat._messages.pop()  # 撤销占位
                    self.chat.append_content(content)
                    self.chat.finish_assistant()
                # 解析 tool_calls 显示工具调用
                tc = m.get("tool_calls")
                if tc:
                    try:
                        calls = json.loads(tc)
                        for c in calls:
                            func = c.get("function", {})
                            name = func.get("name", "")
                            args = func.get("arguments", "{}")
                            self.chat.append_tool_call(name, args)
                    except json.JSONDecodeError:
                        pass
            elif role == "tool":
                self.chat.append_tool_result("(历史)", content[:500])

    # ---- 发送消息与 Worker 管理 ----

    @Slot(str)
    def _on_message_sent(self, text: str) -> None:
        if self.current_session_id is None:
            QMessageBox.warning(self, "提示", "请先选择或新建会话。")
            return
        if self._worker is not None:
            return  # 忙碌中

        self.chat.append_user(text)
        self.chat.start_assistant()
        self.chat.set_busy(True)

        # 创建 worker + thread
        self._worker = AgentWorker(self.agent, self.current_session_id, text)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        # 连接信号
        self._worker.content_ready.connect(self._on_content)
        self._worker.reasoning_ready.connect(self._on_reasoning)
        self._worker.tool_call.connect(self._on_tool_call)
        self._worker.tool_result.connect(self._on_tool_result)
        self._worker.confirm_request.connect(self._on_confirm_request)
        self._worker.error.connect(self._on_error)
        self._worker.done.connect(self._on_done)
        self._worker.finished.connect(self._on_worker_finished)
        self._thread.started.connect(self._worker.run)

        self._thread.start()

    @Slot()
    def _on_stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel = True

    @Slot(str)
    def _on_content(self, text: str) -> None:
        self.chat.append_content(text)

    @Slot(str)
    def _on_reasoning(self, text: str) -> None:
        self.chat.append_reasoning(text)

    @Slot(str, str)
    def _on_tool_call(self, name: str, args_json: str) -> None:
        self.chat.append_tool_call(name, args_json)
        # 工具调用后，助手会继续输出，需要新的助手消息块
        self.chat.start_assistant()

    @Slot(str, str)
    def _on_tool_result(self, name: str, result: str) -> None:
        self.chat.append_tool_result(name, result)

    @Slot(str, str)
    def _on_confirm_request(self, name: str, args_json: str) -> None:
        """弹窗确认工具执行。"""
        msg = f"工具 {name} 请求执行。\n\n参数：\n{args_json}\n\n是否允许？"
        reply = QMessageBox.question(
            self, "确认执行", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        approved = reply == QMessageBox.StandardButton.Yes
        if self._worker is not None:
            self._worker.receive_confirm_result(approved)

    @Slot(str)
    def _on_error(self, text: str) -> None:
        self.chat.append_error(text)

    @Slot(str)
    def _on_done(self, text: str) -> None:
        self.chat.finish_assistant()

    @Slot()
    def _on_worker_finished(self) -> None:
        """Worker 结束，清理线程，恢复 UI。"""
        self.chat.finish_assistant()
        self.chat.set_busy(False)
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread.deleteLater()
            self._thread = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        # 刷新记忆面板（偏好可能已更新）
        self._refresh_memory()

    # ---- 记忆面板 ----

    def _refresh_memory(self) -> None:
        """刷新记忆面板。"""
        self.memory_panel.refresh()

    @Slot()
    def _on_memory_changed(self) -> None:
        """偏好被手动编辑时触发（MemoryPanel 已自行刷新）。"""
        pass

    # ---- 设置 ----

    @Slot()
    def _toggle_theme(self) -> None:
        """在深色/浅色主题间切换并持久化。"""
        self.app_cfg.theme = "light" if self.app_cfg.theme == "dark" else "dark"
        theme = self.app_cfg.theme
        apply_global_theme(QApplication.instance(), theme)
        self.chat.set_theme(theme)
        self.app_cfg.save()

    def _open_settings(self) -> None:
        """打开设置对话框。"""
        old_model = self.llm_cfg.model
        old_url = self.llm_cfg.base_url
        old_key = self.llm_cfg.api_key
        dlg = SettingsDialog(self.app_cfg, self.llm_cfg, self)
        if dlg.exec():
            # 模型 / 地址 / Key 变化则重建 Agent
            if (
                self.llm_cfg.model != old_model
                or self.llm_cfg.base_url != old_url
                or self.llm_cfg.api_key != old_key
            ):
                self.model_label.setText(f"模型: {self.llm_cfg.model}")
                self.agent = build_agent(self.app_cfg, self.llm_cfg)
                # 更新记忆面板的 memory 引用
                self.memory_panel.memory = self.agent.memory
                self._refresh_sessions()
                self._refresh_memory()
            self._check_ollama()

    def _show_about(self) -> None:
        QMessageBox.about(
            self, "关于",
            "<h3>私人 AI 助手</h3>"
            "<p>基于个人使用习惯定制的桌面 AI 助手。</p>"
            "<p>DeepSeek 云端 API 驱动，对话+偏好记忆本地保存。</p>"
            "<p>能力：对话+偏好记忆 / 文件读写 / 代码执行 / 联网搜索</p>",
        )

    # ---- 关闭清理 ----

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.cancel = True
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(3000)
        # 保存窗口尺寸
        self.app_cfg.window_width = self.width()
        self.app_cfg.window_height = self.height()
        self.app_cfg.save()
        event.accept()
