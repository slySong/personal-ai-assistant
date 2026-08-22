"""QThread AgentWorker：在子线程跑 Agent 循环，通过信号推事件到主线程。

关键设计：
- 所有 LLM/工具调用在 Worker 线程，主线程只做 UI 渲染，避免卡顿
- 流式 content/reasoning 通过信号增量推送
- 确认机制：Worker 的 confirm_callback 发 confirm_request 信号到主线程，
  用 queue.Queue 阻塞等待主线程回填结果（超时 120s 默认拒绝）
- cancel 标志：用户点停止时置位，Agent 在下次 chunk 检查时抛 CancelledError
"""
from __future__ import annotations

import json
import queue
from typing import Optional

from PySide6.QtCore import QObject, Signal

from core.agent import Agent, AgentEvent, CancelledError


class AgentWorker(QObject):
    """在子线程执行 Agent.run() 的 Worker。"""

    # 信号（全部连接到主线程的槽）
    content_ready = Signal(str)       # 流式回答文本增量
    reasoning_ready = Signal(str)     # CoT 推理文本增量
    tool_call = Signal(str, str)      # name, args_json（完整调用，非流式分片）
    tool_result = Signal(str, str)    # name, result_text
    confirm_request = Signal(str, str)  # name, args_json（请求主线程弹框确认）
    error = Signal(str)
    done = Signal(str)                # 最终完整回答
    finished = Signal()               # 整轮结束（含出错）

    def __init__(self, agent: Agent, session_id: int, user_input: str):
        super().__init__()
        self.agent = agent
        self.session_id = session_id
        self.user_input = user_input
        self.cancel = False
        self._confirm_queue: queue.Queue[bool] = queue.Queue()

    def receive_confirm_result(self, approved: bool) -> None:
        """主线程调用：回填用户确认结果。"""
        self._confirm_queue.put(approved)

    def _confirm_callback(self, name: str, args: dict) -> bool:
        """Agent 调用的确认回调。发信号问主线程，阻塞等待结果。"""
        args_json = json.dumps(args, ensure_ascii=False)
        self.confirm_request.emit(name, args_json)
        try:
            return self._confirm_queue.get(timeout=120)
        except queue.Empty:
            return False  # 超时默认拒绝

    def _on_event(self, ev: AgentEvent) -> None:
        """Agent 事件回调。转成 Qt 信号发到主线程。"""
        if ev.type == "content":
            self.content_ready.emit(ev.payload)
        elif ev.type == "reasoning":
            self.reasoning_ready.emit(ev.payload)
        elif ev.type == "tool_call":
            # 只推送完整调用（streaming 分片忽略，避免噪音）
            if not ev.payload.get("streaming"):
                name = ev.payload.get("name", "")
                args = ev.payload.get("args", {})
                self.tool_call.emit(name, json.dumps(args, ensure_ascii=False))
        elif ev.type == "tool_result":
            name = ev.payload.get("name", "")
            result = ev.payload.get("result", "")
            self.tool_result.emit(name, result)
        elif ev.type == "error":
            self.error.emit(str(ev.payload))
        elif ev.type == "done":
            self.done.emit(ev.payload or "")

    def run(self) -> None:
        """Worker 入口。在子线程执行。"""
        try:
            self.agent.run(
                session_id=self.session_id,
                user_input=self.user_input,
                on_event=self._on_event,
                confirm_callback=self._confirm_callback,
                is_cancelled=lambda: self.cancel,
            )
        except CancelledError:
            self.error.emit("已停止")
        except Exception as e:
            self.error.emit(f"运行出错：{type(e).__name__}: {e}")
        finally:
            self.finished.emit()
