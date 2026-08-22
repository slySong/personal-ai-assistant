"""CLI 聊天入口（开发调试用，支持工具调用与 Agent 循环）。

集成 LLM 客户端、工具系统、对话历史，可完成多步任务。
P3 之后接入记忆系统，偏好注入。

用法：
    python cli_chat.py [--model qwen3:8b] [--show-reasoning] [--new-session]
"""
from __future__ import annotations

import argparse
import json
import sys

from config import AppConfig, LLMConfig
from core.llm_client import LLMClient
from core.agent import AgentEvent
from core.factory import build_agent


def print_event(ev: AgentEvent, show_reasoning: bool) -> None:
    """CLI 事件展示。"""
    if ev.type == "content":
        print(ev.payload, end="", flush=True)
    elif ev.type == "reasoning" and show_reasoning:
        # 灰色显示推理过程
        print(f"\033[90m{ev.payload}\033[0m", end="", flush=True)
    elif ev.type == "tool_call":
        p = ev.payload
        if p.get("streaming"):
            return  # 流式分片不逐条打印，等完整调用
        name = p.get("name", "?")
        args = p.get("args", {})
        args_str = json.dumps(args, ensure_ascii=False)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        print(f"\n  🔧 调用工具 {name}({args_str})", flush=True)
    elif ev.type == "tool_result":
        p = ev.payload
        name = p.get("name", "?")
        result = p.get("result", "")
        if len(result) > 500:
            result = result[:500] + "\n  ...（截断）"
        print(f"  📋 {name} 结果:\n  {result}", flush=True)
    elif ev.type == "error":
        print(f"\n  ⚠️ {ev.payload}", flush=True)
    elif ev.type == "done":
        pass  # content 已流式打印


def cli_confirm(name: str, args: dict) -> bool:
    """CLI 确认回调。shell 命令执行前询问 y/n。"""
    print(f"\n  ⚠️ 工具 {name} 需要确认。参数：{json.dumps(args, ensure_ascii=False)}")
    try:
        ans = input("  允许执行？[y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


def main() -> int:
    parser = argparse.ArgumentParser(description="私人 AI 助手 CLI")
    parser.add_argument("--model", default=None, help="覆盖模型名，如 qwen3:8b / gpt-oss:20b")
    parser.add_argument("--show-reasoning", action="store_true", help="显示推理过程")
    parser.add_argument("--base-url", default=None, help="覆盖 Ollama API 地址")
    parser.add_argument("--new-session", action="store_true", help="新建会话（不复用最近的）")
    args = parser.parse_args()

    app_cfg = AppConfig.load()
    llm_cfg = LLMConfig(
        base_url=args.base_url or app_cfg.llm.base_url,
        api_key=app_cfg.llm.api_key,
        model=args.model or app_cfg.llm.model,
        temperature=app_cfg.llm.temperature,
        max_tokens=app_cfg.llm.max_tokens,
        timeout=app_cfg.llm.timeout,
        request_timeout=app_cfg.llm.request_timeout,
    )

    # 探活
    print(f"正在连接后端 ({llm_cfg.base_url}) ...", flush=True)
    tmp_client = LLMClient(llm_cfg)
    if not tmp_client.is_reachable():
        print("\n[错误] 无法连接后端服务。请确认：")
        print(f"  1. API 地址正确：{llm_cfg.base_url}")
        print("  2. API Key 已填写且有效（云端服务）")
        print("  3. 本地 Ollama 需已运行：执行 'ollama serve'")
        return 1

    models = tmp_client.list_models()
    print(f"✓ 已连接。可用模型: {', '.join(models) if models else '(无)'}")
    if models and llm_cfg.model not in models:
        print(f"[警告] 模型 '{llm_cfg.model}' 不在可用列表中。")
        if models:
            print(f"  可用模型：{', '.join(models)}")
            print(f"  或换用：python cli_chat.py --model {models[0]}")

    # 组装 Agent（含记忆系统）
    agent = build_agent(app_cfg, llm_cfg)
    conversation = agent.conversation

    # 会话管理
    if args.new_session or not conversation.list_sessions():
        session_id = conversation.create_session()
        print(f"✓ 新建会话 #{session_id}")
    else:
        sessions = conversation.list_sessions()
        session_id = sessions[0]["id"]
        print(f"✓ 继续会话 #{session_id}（{sessions[0]['title']}）")

    print(f"模型: {llm_cfg.model} | 工具: {', '.join(agent.tools.names())}")
    print("输入消息后 Enter 发送，/quit 退出，/clear 清空历史，/new 新建会话\n")

    cancelled = False

    while True:
        try:
            user_input = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            return 0

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit", "/q"):
            print("再见！")
            return 0
        if user_input.lower() in ("/clear", "/c"):
            conversation.delete_session(session_id)
            session_id = conversation.create_session()
            print(f"[已清空，新建会话 #{session_id}]")
            continue
        if user_input.lower() in ("/new", "/n"):
            session_id = conversation.create_session()
            print(f"[新建会话 #{session_id}]")
            continue

        print("\n助手 > ", end="", flush=True)
        try:
            agent.run(
                session_id=session_id,
                user_input=user_input,
                on_event=lambda ev: print_event(ev, args.show_reasoning),
                confirm_callback=cli_confirm,
            )
            print()  # 换行
        except KeyboardInterrupt:
            print("\n[已中断]")
        except Exception as e:
            print(f"\n[错误] {type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
