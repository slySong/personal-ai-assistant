"""Agent 循环测试：mock LLM，验证工具调用流程、死循环保护、取消机制。"""
import json

import pytest

from core.agent import Agent, CancelledError
from core.conversation import ConversationStore
from core.llm_client import StreamChunk
from tools.base import Tool, ToolRegistry, ToolResult


class MockTool(Tool):
    """测试用工具。"""
    name = "mock_tool"
    description = "测试工具"
    parameters = {
        "type": "object",
        "properties": {"arg": {"type": "string", "description": "参数"}},
        "required": ["arg"],
    }

    def __init__(self):
        self.call_count = 0
        self.last_arg = ""

    def execute(self, arg: str = "") -> ToolResult:
        self.call_count += 1
        self.last_arg = arg
        return ToolResult(ok=True, output=f"工具执行成功，参数={arg}")


def _make_tool_calls_json(tool_name: str, args: dict, call_id: str = "call_1") -> str:
    """构造完整的 tool_calls JSON（agent 从 done chunk 解析）。"""
    return json.dumps(
        [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        }],
        ensure_ascii=False,
    )


class _MockCfg:
    """mock LLMConfig。"""
    model = "mock-model"


def test_agent_tool_call_flow(tmp_path):
    """测试完整工具调用流程：LLM 调工具 → 执行 → LLM 给最终答案。"""

    class MockLLMClient:
        def __init__(self):
            self.call_count = 0
            self.cfg = _MockCfg()

        def chat_stream(self, messages, tools=None):
            if self.call_count == 0:
                self.call_count += 1
                yield StreamChunk(
                    type="tool_call",
                    tool_call_index=0,
                    tool_call_id="call_1",
                    tool_name="mock_tool",
                    tool_args_delta='{"arg": "test_value"}',
                )
                yield StreamChunk(
                    type="done",
                    finish_reason="tool_calls",
                    text=_make_tool_calls_json("mock_tool", {"arg": "test_value"}),
                )
            else:
                yield StreamChunk(type="content", text="任务完成了")
                yield StreamChunk(type="done", finish_reason="stop", text="")

    llm = MockLLMClient()
    conversation = ConversationStore(tmp_path / "test.db")
    sid = conversation.create_session()
    tools = ToolRegistry()
    mock_tool = MockTool()
    tools.register(mock_tool)

    events = []
    agent = Agent(
        llm=llm, tools=tools, conversation=conversation, memory=None,
        max_iterations=5,
    )
    result = agent.run(sid, "执行测试", on_event=lambda ev: events.append(ev))

    # 工具被调用一次，参数正确
    assert mock_tool.call_count == 1
    assert mock_tool.last_arg == "test_value"
    # 最终答案正确
    assert "任务完成了" in result
    # 事件序列正确
    types = [e.type for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert "content" in types
    assert "done" in types
    # 对话历史已持久化
    msgs = conversation.get_messages(sid)
    roles = [m["role"] for m in msgs]
    assert "user" in roles
    assert "assistant" in roles
    assert "tool" in roles


def test_agent_max_iterations(tmp_path):
    """测试死循环保护：LLM 永远返回 tool_call，应被 max_iterations 拦截。"""

    class LoopLLMClient:
        def __init__(self):
            self.cfg = _MockCfg()

        def chat_stream(self, messages, tools=None):
            yield StreamChunk(
                type="done",
                finish_reason="tool_calls",
                text=_make_tool_calls_json("mock_tool", {"arg": "loop"}, "call_loop"),
            )

    llm = LoopLLMClient()
    conversation = ConversationStore(tmp_path / "test.db")
    sid = conversation.create_session()
    tools = ToolRegistry()
    mock_tool = MockTool()
    tools.register(mock_tool)

    events = []
    agent = Agent(
        llm=llm, tools=tools, conversation=conversation, memory=None,
        max_iterations=3,
    )
    agent.run(sid, "死循环测试", on_event=lambda ev: events.append(ev))

    # 工具被调用 max_iterations 次（3次）
    assert mock_tool.call_count == 3
    # 有 error 事件通知死循环
    assert any(e.type == "error" for e in events)


def test_agent_cancel(tmp_path):
    """测试取消机制：is_cancelled 返回 True 时抛 CancelledError。"""

    class CancelLLMClient:
        def __init__(self):
            self.cfg = _MockCfg()

        def chat_stream(self, messages, tools=None):
            yield StreamChunk(type="content", text="开始回答")
            yield StreamChunk(type="done", finish_reason="stop", text="")

    llm = CancelLLMClient()
    conversation = ConversationStore(tmp_path / "test.db")
    sid = conversation.create_session()
    tools = ToolRegistry()

    agent = Agent(llm=llm, tools=tools, conversation=conversation, memory=None)
    with pytest.raises(CancelledError):
        agent.run(
            sid, "测试取消",
            on_event=lambda ev: None,
            is_cancelled=lambda: True,
        )


def test_agent_confirm_rejected(tmp_path):
    """测试确认机制：用户拒绝时工具不执行。"""

    class ConfirmLLMClient:
        def __init__(self):
            self.call_count = 0
            self.cfg = _MockCfg()

        def chat_stream(self, messages, tools=None):
            if self.call_count == 0:
                self.call_count += 1
                yield StreamChunk(
                    type="done",
                    finish_reason="tool_calls",
                    text=_make_tool_calls_json("mock_tool", {"arg": "need_confirm"}),
                )
            else:
                yield StreamChunk(type="content", text="好的，跳过了")
                yield StreamChunk(type="done", finish_reason="stop", text="")

    llm = ConfirmLLMClient()
    conversation = ConversationStore(tmp_path / "test.db")
    sid = conversation.create_session()
    tools = ToolRegistry()
    mock_tool = MockTool()
    # 标记需要确认
    mock_tool.requires_confirmation = True
    tools.register(mock_tool)

    agent = Agent(llm=llm, tools=tools, conversation=conversation, memory=None)
    # confirm_callback 返回 False（拒绝）
    result = agent.run(
        sid, "测试拒绝",
        on_event=lambda ev: None,
        confirm_callback=lambda name, args: False,
    )
    # 工具未被实际执行
    assert mock_tool.call_count == 0
    assert "跳过" in result


def test_agent_no_tools_simple_chat(tmp_path):
    """无工具时纯对话流程。"""

    class SimpleLLMClient:
        def __init__(self):
            self.cfg = _MockCfg()

        def chat_stream(self, messages, tools=None):
            yield StreamChunk(type="content", text="你好！")
            yield StreamChunk(type="done", finish_reason="stop", text="")

    llm = SimpleLLMClient()
    conversation = ConversationStore(tmp_path / "test.db")
    sid = conversation.create_session()
    tools = ToolRegistry()  # 空注册表

    agent = Agent(llm=llm, tools=tools, conversation=conversation, memory=None)
    result = agent.run(sid, "你好", on_event=lambda ev: None)
    assert "你好" in result
