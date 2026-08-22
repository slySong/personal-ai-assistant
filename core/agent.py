"""Agent 循环：基于 DeepSeek 原生 tool calling 的多步推理。

核心流程：
  装配 system prompt（人设 + 用户偏好注入）
  → 拉历史 + 当前输入（滑动窗口裁剪）
  → 流式调用 LLM，边收边推事件给 UI
  → 流结束判断：
      无 tool_calls → 存最终答案，触发偏好提取，结束
      有 tool_calls → 回填 assistant 消息（含 tool_calls）
                   → 逐个执行工具（需确认的等待用户确认）
                   → tool 结果以 role=tool 回传
                   → 循环回流式调用，max_iterations 防死循环

当使用推理模型（如 deepseek-reasoner）时，tool calling 伴随 CoT reasoning。
reasoning 通过单独事件类型上报（UI 折叠显示），assistant 消息回填时如有
reasoning 则附加到 content，保证后续轮次上下文完整。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from core.llm_client import LLMClient
from core.conversation import ConversationStore
from tools.base import ToolRegistry


BASE_SYSTEM_PROMPT = """你是用户的私人 AI 助手，运行在用户本地机器上，完全离线。

你的能力：
- 自然语言对话，友好、简洁、实用
- 文件读写：在沙箱目录内读取、写入、列出文件
- 代码执行：运行 Python 脚本或 shell 命令（shell 需用户确认）
- 联网搜索：用 DuckDuckGo 查询实时信息

行为准则：
- 用中文回答（除非用户用其他语言提问）
- 需要外部信息时主动调用工具，不要编造
- 代码块标注语言，输出格式清晰
- 执行有风险操作前会请求用户确认，请如实说明将做什么
- 工具执行失败时分析原因并重试或换方案，不要卡住

可用工具见 tools 参数。优先用工具完成任务，再基于结果给出回答。"""


class MemoryProtocol(Protocol):
    """记忆系统协议。agent 通过此接口注入偏好和触发提取。"""

    def build_profile_prompt(self) -> str: ...

    def maybe_extract(
        self, session_id: int, user_input: str, assistant_text: str
    ) -> None: ...


@dataclass
class AgentEvent:
    """Agent 运行时事件。UI 据此更新界面。"""
    type: str  # content | reasoning | tool_call | tool_result | confirm | error | done
    payload: Any = None


class CancelledError(Exception):
    """用户主动停止。"""


class Agent:
    """Agent 主控制器。协调 LLM、工具、记忆、对话历史。"""

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        conversation: ConversationStore,
        memory: Optional[MemoryProtocol] = None,
        max_iterations: int = 10,
        context_max_tokens: int = 16000,
    ):
        self.llm = llm
        self.tools = tools
        self.conversation = conversation
        self.memory = memory
        self.max_iterations = max_iterations
        self.context_max_tokens = context_max_tokens

    def run(
        self,
        session_id: int,
        user_input: str,
        on_event: Callable[[AgentEvent], None],
        confirm_callback: Optional[Callable[[str, dict], bool]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> str:
        """运行一轮对话（可能含多步工具调用）。

        参数：
            session_id: 会话 ID
            user_input: 用户输入
            on_event: 事件回调（流式输出、工具调用等）
            confirm_callback: 需确认工具的确认回调，返回 True/False
            is_cancelled: 返回 True 时中断执行（用户点了停止）

        返回：助手最终回答文本
        """
        # 1. 装配 system prompt
        profile = self.memory.build_profile_prompt() if self.memory else ""
        system_content = BASE_SYSTEM_PROMPT
        if profile:
            system_content += "\n\n" + profile

        # 2. 存用户消息，拉历史并裁剪
        self.conversation.add_message(session_id, "user", user_input)
        raw_history = self.conversation.get_messages(session_id)
        # 把 system 消息放最前（历史里没有 system，手动加）
        history_with_system = [{"role": "system", "content": system_content}] + raw_history
        trimmed = self.conversation.trim_for_context(
            history_with_system, self.context_max_tokens
        )
        messages = self.conversation.to_api_messages(trimmed)

        tool_schemas = self.tools.schemas() or None

        final_answer = ""

        for iteration in range(self.max_iterations):
            # 检查取消
            if is_cancelled and is_cancelled():
                raise CancelledError("用户停止")

            # 3. 流式调用，边收边推
            content_buf = ""
            reasoning_buf = ""
            complete_tool_calls: list[dict] = []

            try:
                for chunk in self.llm.chat_stream(messages, tools=tool_schemas):
                    if is_cancelled and is_cancelled():
                        raise CancelledError("用户停止")

                    if chunk.type == "content":
                        content_buf += chunk.text
                        on_event(AgentEvent("content", chunk.text))
                    elif chunk.type == "reasoning":
                        reasoning_buf += chunk.text
                        on_event(AgentEvent("reasoning", chunk.text))
                    elif chunk.type == "tool_call":
                        # 实时推送工具调用流入（UI 可显示参数累积）
                        on_event(
                            AgentEvent(
                                "tool_call",
                                {
                                    "name": chunk.tool_name,
                                    "args_delta": chunk.tool_args_delta,
                                    "streaming": True,
                                },
                            )
                        )
                    elif chunk.type == "done":
                        # done 分片的 text 字段携带完整 tool_calls JSON
                        if chunk.text:
                            try:
                                complete_tool_calls = json.loads(chunk.text)
                            except json.JSONDecodeError:
                                complete_tool_calls = []
            except CancelledError:
                raise
            except Exception as e:
                on_event(AgentEvent("error", f"LLM 调用失败：{type(e).__name__}: {e}"))
                return content_buf or ""

            # 4. 流结束，判断是否调工具
            if not complete_tool_calls:
                # 最终答案
                final_answer = content_buf
                self.conversation.add_message(session_id, "assistant", content_buf)
                # 异步触发偏好提取
                if self.memory:
                    try:
                        self.memory.maybe_extract(session_id, user_input, content_buf)
                    except Exception:
                        pass  # 偏好提取失败不影响主流程
                on_event(AgentEvent("done", content_buf))
                return final_answer

            # 5. 有 tool_calls：回填 assistant 消息
            # 推理模型 CoT：如有 reasoning，附加到 content 保证上下文完整
            assistant_content = content_buf
            if not assistant_content and reasoning_buf:
                # 无 content 时用 reasoning 摘要作为 content（部分 API 要求 content 非空）
                # 但 OpenAI 允许 assistant 含 tool_calls 时 content 为 null
                assistant_content = None

            assistant_msg: dict = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": complete_tool_calls,
            }
            messages.append(assistant_msg)
            # 持久化（tool_calls 存 JSON 字符串）
            self.conversation.add_message(
                session_id,
                "assistant",
                content_buf,
                tool_calls=json.dumps(complete_tool_calls, ensure_ascii=False),
            )

            # 6. 逐个执行工具
            for tc in complete_tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                args_json = func.get("arguments", "{}")
                tc_id = tc.get("id", "")

                try:
                    args = json.loads(args_json) if args_json else {}
                except json.JSONDecodeError:
                    args = {}

                on_event(
                    AgentEvent("tool_call", {"name": name, "args": args, "streaming": False})
                )

                # 确认机制
                tool = self.tools.get(name)
                if tool is not None and getattr(tool, "requires_confirmation", False):
                    if confirm_callback is None:
                        result_text = "此操作需要用户确认，但未提供确认回调，已跳过。"
                    elif confirm_callback(name, args):
                        result = self.tools.execute(name, args)
                        result_text = result.output
                    else:
                        result_text = "用户拒绝了此操作。"
                else:
                    result = self.tools.execute(name, args)
                    result_text = result.output

                on_event(
                    AgentEvent("tool_result", {"name": name, "result": result_text})
                )

                # tool 结果消息（OpenAI 格式）
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result_text,
                }
                messages.append(tool_msg)
                self.conversation.add_message(
                    session_id, "tool", result_text, tool_call_id=tc_id
                )

            # 7. 循环回到步骤 3，让模型基于工具结果继续推理

        # 达到 max_iterations
        on_event(
            AgentEvent(
                "error",
                f"达到最大迭代次数（{self.max_iterations}），强制停止。"
                "可能工具调用陷入循环，请检查任务或增大迭代上限。",
            )
        )
        return final_answer
