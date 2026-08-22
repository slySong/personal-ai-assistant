"""工厂模块：组装 Agent 及其依赖。CLI 和 GUI 共用。"""
from __future__ import annotations

from config import AppConfig, LLMConfig, DB_FILE
from core.agent import Agent
from core.conversation import ConversationStore
from core.llm_client import LLMClient
from core.memory import MemoryStore
from tools.base import ToolRegistry
from tools.code_exec import ExecCommandTool, ExecPythonTool
from tools.file_ops import ListFilesTool, ReadFileTool, WriteFileTool
from tools.web_search import WebSearchTool


def build_tools(app_cfg: AppConfig) -> ToolRegistry:
    """组装默认工具集。"""
    registry = ToolRegistry()
    registry.register(ReadFileTool(app_cfg.sandbox))
    registry.register(WriteFileTool(app_cfg.sandbox))
    registry.register(ListFilesTool(app_cfg.sandbox))
    registry.register(ExecPythonTool(app_cfg.sandbox))
    registry.register(ExecCommandTool(app_cfg.sandbox))
    registry.register(WebSearchTool())
    return registry


def build_agent(app_cfg: AppConfig, llm_cfg: LLMConfig) -> Agent:
    """组装完整 Agent（含 LLM、工具、对话历史、记忆系统）。"""
    llm = LLMClient(llm_cfg)
    conversation = ConversationStore(DB_FILE)
    memory = MemoryStore(DB_FILE, app_cfg.memory, llm_client=llm)
    tools = build_tools(app_cfg)
    return Agent(
        llm=llm,
        tools=tools,
        conversation=conversation,
        memory=memory,
        max_iterations=app_cfg.agent.max_iterations,
        context_max_tokens=app_cfg.agent.context_max_tokens,
    )
