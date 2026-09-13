"""核心逻辑验证脚本（不依赖 pytest，用标准库）。

运行：py verify.py
验证记忆系统、路径禁锢、危险命令检测、文件/代码执行工具。
"""
import sys
import tempfile
from pathlib import Path

# 确保项目根在 path
sys.path.insert(0, str(Path(__file__).resolve().parent))

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name} {detail}")


print("=" * 50)
print("记忆系统测试")
print("=" * 50)

from config import MemoryConfig
from core.memory import MemoryStore, Preference

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    store = MemoryStore(tmp / "test.db")

    # 规则提取：姓名
    prefs = store._extract_rules("我叫小张，我是做前端开发的")
    keys = {p.key: p for p in prefs}
    check("规则提取姓名", "name" in keys and keys["name"].value == "小张",
          f"got {list(keys.keys())}")

    # 规则提取：职业
    check("规则提取职业", "occupation" in keys,
          f"got {list(keys.keys())}")

    # 规则提取：语言
    prefs2 = store._extract_rules("我平时用 TypeScript 写代码")
    keys2 = {p.key: p for p in prefs2}
    check("规则提取语言", "primary_language" in keys2,
          f"got {list(keys2.keys())}")

    # 规则提取：无匹配
    check("规则提取无匹配", store._extract_rules("今天天气不错") == [])

    # upsert 高置信覆盖
    store.upsert(Preference(key="name", value="旧名", category="identity", confidence=0.5))
    store.upsert(Preference(key="name", value="新名", category="identity", confidence=0.9))
    prefs3 = {p.key: p for p in store.get_all_active()}
    check("upsert高置信覆盖", prefs3["name"].value == "新名")

    # upsert 低置信不覆盖
    store2 = MemoryStore(tmp / "test2.db")
    store2.upsert(Preference(key="name", value="高置信名", category="identity", confidence=0.9))
    store2.upsert(Preference(key="name", value="低置信名", category="identity", confidence=0.5))
    prefs4 = {p.key: p for p in store2.get_all_active()}
    check("upsert低置信不覆盖", prefs4["name"].value == "高置信名")

    # build_profile_prompt
    store3 = MemoryStore(tmp / "test3.db")
    store3.upsert(Preference(key="name", value="小张", category="identity", confidence=0.9))
    store3.upsert(Preference(key="occupation", value="前端", category="work", confidence=0.85))
    prompt = store3.build_profile_prompt()
    check("注入块含姓名", "小张" in prompt)
    check("注入块含职业", "前端" in prompt)
    check("注入块含标题", "用户偏好" in prompt)

    # 低置信过滤
    store3.upsert(Preference(key="hobby", value="低置信", category="preference", confidence=0.3))
    prompt2 = store3.build_profile_prompt()
    check("低置信不注入", "低置信" not in prompt2)

    # 删除
    check("删除偏好", store3.delete("name"))
    check("删除后不存在", "name" not in {p.key for p in store3.get_all_active()})

    # maybe_extract
    store4 = MemoryStore(tmp / "test4.db")
    store4.maybe_extract(1, "我叫小张", "你好")
    prefs5 = {p.key: p for p in store4.get_all_active()}
    check("maybe_extract规则提取", "name" in prefs5)

    # hit_count 递增
    store5 = MemoryStore(tmp / "test5.db")
    store5.upsert(Preference(key="name", value="小张", category="identity", confidence=0.9))
    store5.build_profile_prompt()
    store5.build_profile_prompt()
    prefs6 = {p.key: p for p in store5.get_all_active()}
    check("hit_count递增", prefs6["name"].hit_count == 2)


print()
print("=" * 50)
print("工具系统测试")
print("=" * 50)

from config import SandboxConfig
from tools.file_ops import _resolve_path, ReadFileTool, WriteFileTool, ListFilesTool, SecurityError
from tools.code_exec import _is_dangerous, ExecPythonTool, ExecCommandTool

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    # 路径解析
    check("resolve_path正常", str(_resolve_path("sub/file.txt", tmp)).startswith(str(tmp)))
    try:
        _resolve_path("../../etc/passwd", tmp)
        check("resolve_path拒绝..", False, "未抛异常")
    except SecurityError:
        check("resolve_path拒绝..", True)
    try:
        _resolve_path("C:\\Windows\\system32", tmp)
        check("resolve_path拒绝绝对路径(默认)", False, "未抛异常")
    except SecurityError:
        check("resolve_path拒绝绝对路径(默认)", True)
    p = _resolve_path("C:\\Windows\\system32", tmp, trusted=True)
    check("resolve_path允许绝对路径(可信)", p.is_absolute())

    # 危险命令检测
    check("检测del", _is_dangerous("del C:\\important.txt") is not None)
    check("检测format", _is_dangerous("format C:") is not None)
    check("检测rm", _is_dangerous("rm -rf /") is not None)
    check("检测shutdown", _is_dangerous("shutdown /s /t 0") is not None)
    check("安全命令dir", _is_dangerous("dir") is None)
    check("安全命令echo", _is_dangerous("echo hello") is None)

    # 文件读写
    (tmp / "test.txt").write_text("hello world", encoding="utf-8")
    cfg = SandboxConfig(root_dir=str(tmp))
    read_tool = ReadFileTool(cfg)
    result = read_tool.execute(path="test.txt")
    check("读取文件", result.ok and "hello world" in result.output)

    result = read_tool.execute(path="nonexist.txt")
    check("读取不存在文件", not result.ok)

    write_tool = WriteFileTool(cfg)
    result = write_tool.execute(path="new.txt", content="content")
    check("写入文件", result.ok and (tmp / "new.txt").read_text(encoding="utf-8") == "content")

    result = write_tool.execute(path="../../evil.txt", content="hack")
    check("写入拒绝越界", not result.ok and not (tmp.parent.parent / "evil.txt").exists())

    # 代码执行
    py_tool = ExecPythonTool(cfg)
    result = py_tool.execute(code="print(1+1)")
    check("执行Python", result.ok and "2" in result.output)

    # 代码执行超时
    cfg_timeout = SandboxConfig(root_dir=str(tmp), exec_timeout=2)
    py_tool_timeout = ExecPythonTool(cfg_timeout)
    result = py_tool_timeout.execute(code="import time; time.sleep(10)")
    check("Python超时", not result.ok and "超时" in result.output)

    # 危险命令拦截
    cmd_tool = ExecCommandTool(cfg)
    result = cmd_tool.execute(command="del C:\\Windows\\system32\\xxx")
    check("危险命令拦截", not result.ok and "危险" in result.output)

    # 安全命令执行
    result = cmd_tool.execute(command="echo hello_test")
    check("安全命令执行", result.ok and "hello_test" in result.output)

    # 确认标志（非可信模式默认需确认，可信模式免确认）
    check("shell默认需确认", cmd_tool.requires_confirmation is True)
    cmd_trusted = ExecCommandTool(SandboxConfig(root_dir=str(tmp), trusted_mode=True))
    check("shell可信免确认", cmd_trusted.requires_confirmation is False)
    check("Python无需确认", py_tool.requires_confirmation is False)


print()
print("=" * 50)
print("Agent 循环测试（mock LLM）")
print("=" * 50)

import json
from core.agent import Agent, CancelledError
from core.conversation import ConversationStore
from core.llm_client import StreamChunk
from tools.base import Tool, ToolRegistry, ToolResult


class MockTool(Tool):
    name = "mock_tool"
    description = "测试工具"
    parameters = {"type": "object", "properties": {"arg": {"type": "string"}}, "required": ["arg"]}

    def __init__(self):
        self.call_count = 0

    def execute(self, arg=""):
        self.call_count += 1
        return ToolResult(ok=True, output=f"执行了 {arg}")


class _MockCfg:
    model = "mock-model"


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    # 完整工具调用流程
    class MockLLM:
        def __init__(self):
            self.call_count = 0
            self.cfg = _MockCfg()

        def chat_stream(self, messages, tools=None):
            if self.call_count == 0:
                self.call_count += 1
                tc_json = json.dumps([{
                    "id": "call_1", "type": "function",
                    "function": {"name": "mock_tool", "arguments": json.dumps({"arg": "test_value"})},
                }], ensure_ascii=False)
                yield StreamChunk(type="tool_call", tool_call_index=0, tool_call_id="call_1",
                                  tool_name="mock_tool", tool_args_delta='{"arg":"test_value"}')
                yield StreamChunk(type="done", finish_reason="tool_calls", text=tc_json)
            else:
                yield StreamChunk(type="content", text="完成了")
                yield StreamChunk(type="done", finish_reason="stop", text="")

    llm = MockLLM()
    conv = ConversationStore(tmp / "test.db")
    sid = conv.create_session()
    tools = ToolRegistry()
    mock = MockTool()
    tools.register(mock)

    events = []
    agent = Agent(llm=llm, tools=tools, conversation=conv, memory=None, max_iterations=5)
    result = agent.run(sid, "测试", on_event=lambda ev: events.append(ev))

    check("工具被调用", mock.call_count == 1, f"count={mock.call_count}")
    check("最终答案正确", "完成了" in result)
    types = [e.type for e in events]
    check("事件含tool_call", "tool_call" in types)
    check("事件含tool_result", "tool_result" in types)
    check("事件含done", "done" in types)

    # 历史持久化
    msgs = conv.get_messages(sid)
    roles = [m["role"] for m in msgs]
    check("历史含user", "user" in roles)
    check("历史含assistant", "assistant" in roles)
    check("历史含tool", "tool" in roles)

    # 死循环保护
    class LoopLLM:
        def __init__(self):
            self.cfg = _MockCfg()

        def chat_stream(self, messages, tools=None):
            tc_json = json.dumps([{
                "id": "c", "type": "function",
                "function": {"name": "mock_tool", "arguments": json.dumps({"arg": "loop"})},
            }], ensure_ascii=False)
            yield StreamChunk(type="done", finish_reason="tool_calls", text=tc_json)

    llm2 = LoopLLM()
    conv2 = ConversationStore(tmp / "test2.db")
    sid2 = conv2.create_session()
    tools2 = ToolRegistry()
    mock2 = MockTool()
    tools2.register(mock2)
    events2 = []
    agent2 = Agent(llm=llm2, tools=tools2, conversation=conv2, memory=None, max_iterations=3)
    agent2.run(sid2, "循环", on_event=lambda ev: events2.append(ev))
    check("死循环保护(3次)", mock2.call_count == 3, f"count={mock2.call_count}")
    check("死循环报错", any(e.type == "error" for e in events2))

    # 取消机制
    class CancelLLM:
        def __init__(self):
            self.cfg = _MockCfg()

        def chat_stream(self, messages, tools=None):
            yield StreamChunk(type="content", text="开始")
            yield StreamChunk(type="done", finish_reason="stop", text="")

    llm3 = CancelLLM()
    conv3 = ConversationStore(tmp / "test3.db")
    sid3 = conv3.create_session()
    agent3 = Agent(llm=llm3, tools=ToolRegistry(), conversation=conv3, memory=None)
    try:
        agent3.run(sid3, "取消", on_event=lambda ev: None, is_cancelled=lambda: True)
        check("取消机制", False, "未抛异常")
    except CancelledError:
        check("取消机制", True)

    # 确认拒绝
    class ConfirmLLM:
        def __init__(self):
            self.cc = 0
            self.cfg = _MockCfg()

        def chat_stream(self, messages, tools=None):
            if self.cc == 0:
                self.cc += 1
                tc_json = json.dumps([{
                    "id": "c1", "type": "function",
                    "function": {"name": "mock_tool", "arguments": json.dumps({"arg": "x"})},
                }], ensure_ascii=False)
                yield StreamChunk(type="done", finish_reason="tool_calls", text=tc_json)
            else:
                yield StreamChunk(type="content", text="跳过了")
                yield StreamChunk(type="done", finish_reason="stop", text="")

    llm4 = ConfirmLLM()
    conv4 = ConversationStore(tmp / "test4.db")
    sid4 = conv4.create_session()
    tools4 = ToolRegistry()
    mock4 = MockTool()
    mock4.requires_confirmation = True
    tools4.register(mock4)
    agent4 = Agent(llm=llm4, tools=tools4, conversation=conv4, memory=None)
    r4 = agent4.run(sid4, "确认", on_event=lambda ev: None, confirm_callback=lambda n, a: False)
    check("确认拒绝不执行", mock4.call_count == 0)
    check("确认拒绝后继续", "跳过" in r4)


print()
print("=" * 50)
print(f"结果：✓ {passed} 通过  ✗ {failed} 失败")
print("=" * 50)
sys.exit(0 if failed == 0 else 1)
