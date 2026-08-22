"""工具基类与注册表。

所有工具继承 Tool，实现 execute()。ToolRegistry 统一管理，
向 LLM 提供 OpenAI function schema，并按名称分发执行。

确认机制：requires_confirmation=True 的工具，Agent 在执行前
通过 confirm_callback 向 UI 请求用户确认（如 shell 命令执行）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolResult:
    """工具执行结果。output 是给 LLM 看的文本。"""
    ok: bool
    output: str
    raw: Any = None
    # 工具本身是否需要确认（与 Tool.requires_confirmation 配合）
    # 此字段用于运行时动态判断，一般直接读 Tool.requires_confirmation
    needs_confirmation: bool = False


class Tool(ABC):
    """工具抽象基类。子类需定义类属性 name/description/parameters 并实现 execute()。"""

    name: str = ""
    description: str = ""
    # JSON Schema，描述工具参数，喂给 LLM 的 tools 参数
    parameters: dict = {"type": "object", "properties": {}}
    # 是否需要用户确认才执行（高风险工具设为 True）
    requires_confirmation: bool = False

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """执行工具。kwargs 来自 LLM 解析的 tool arguments。"""
        ...

    def schema(self) -> dict:
        """转成 OpenAI tools 数组项格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表。管理所有可用工具，提供 schema 列表和按名执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError(f"工具 {tool.__class__.__name__} 未定义 name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        """所有工具的 schema 列表，传给 LLM 的 tools 参数。"""
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def execute(self, name: str, args: dict) -> ToolResult:
        """按名称执行工具。未知工具返回错误结果。"""
        tool = self.get(name)
        if tool is None:
            return ToolResult(ok=False, output=f"错误：未知工具 '{name}'")
        try:
            return tool.execute(**args)
        except TypeError as e:
            return ToolResult(ok=False, output=f"参数错误：{e}")
        except Exception as e:
            return ToolResult(ok=False, output=f"执行失败：{type(e).__name__}: {e}")
