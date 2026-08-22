"""LLM 客户端封装：基于 OpenAI SDK 指向 DeepSeek API，支持流式输出与 tool_calls 分片累积。

关键设计：
- 兼容推理模型的 reasoning 字段（如 deepseek-reasoner 的思考内容），
  本客户端兼容 delta.reasoning / delta.reasoning_content 两种位置。
- OpenAI SDK 流式时 delta.tool_calls 按 index 分片到达（id 只在首片，
  arguments 字符串分多片拼接），需在客户端内累积后上报完整 tool_calls。
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Iterator, Optional

from openai import OpenAI

from config import LLMConfig


@dataclass
class StreamChunk:
    """流式输出的一个分片。type 区分四种内容流。"""
    type: str  # "content" | "reasoning" | "tool_call" | "done"
    text: str = ""
    tool_call_index: int = 0
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args_delta: str = ""
    finish_reason: str = ""


class LLMClient:
    """封装 OpenAI SDK 指向 DeepSeek API 的客户端。"""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.client = OpenAI(
            base_url=cfg.base_url,
            # 空 key 时用占位，避免 OpenAI SDK 抛错；真实请求会因 401 返回不可达
            api_key=cfg.api_key or "not-set",
            timeout=cfg.request_timeout,
        )

    def _auth_get(self, path: str, timeout: int = 8) -> Optional[dict]:
        """带 Bearer 鉴权的 GET，返回解析后的 JSON 或 None。"""
        base = self.cfg.base_url.rstrip("/")
        try:
            req = urllib.request.Request(f"{base}{path}", method="GET")
            req.add_header("Authorization", f"Bearer {self.cfg.api_key}")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return None

    def is_reachable(self) -> bool:
        """探活后端服务。用 OpenAI 兼容的 GET /models（DeepSeek 等 OpenAI 兼容后端通用）。"""
        return self._auth_get("/models") is not None

    def list_models(self) -> list[str]:
        """列出后端可用模型（OpenAI 兼容 /models 端点），供设置界面下拉选择。"""
        data = self._auth_get("/models")
        if not data:
            return []
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]

    def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> Iterator[StreamChunk]:
        """流式对话。产出 StreamChunk 序列。

        推理模型的 reasoning 通过兼容层处理：检查 delta.reasoning 和
        delta.reasoning_content 两个字段（不同后端版本位置不同）。
        tool_calls 按 index 累积分片，流结束后通过 done 分片上报 finish_reason。
        """
        kwargs: dict = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        stream = self.client.chat.completions.create(**kwargs)

        # tool_calls 累积器：index -> {id, name, arguments_buf}
        tool_calls_acc: dict[int, dict] = {}
        finish_reason = ""

        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            # OpenAI SDK 流式时 finish_reason 只在最后一个非空 chunk 出现
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            # 1. reasoning 字段（推理模型 CoT）——兼容两种位置
            reasoning_text = ""
            # 用 getattr 兼容 SDK 版本差异（reasoning 是部分推理模型的扩展字段）
            reasoning_text = getattr(delta, "reasoning", None) or ""
            if not reasoning_text:
                reasoning_text = getattr(delta, "reasoning_content", None) or ""
            if reasoning_text:
                yield StreamChunk(type="reasoning", text=reasoning_text)

            # 2. content 字段（正常回答文本）
            if delta.content:
                yield StreamChunk(type="content", text=delta.content)

            # 3. tool_calls 分片（按 index 累积）
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if tc.index is not None else 0
                    slot = tool_calls_acc.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""}
                    )
                    # id 只在首片出现
                    if tc.id:
                        slot["id"] = tc.id
                    # function.name 可能分片，但通常首片就有
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    # arguments 字符串分多片拼接
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
                        # 实时推送 arguments 增量（UI 可显示工具参数流入）
                        yield StreamChunk(
                            type="tool_call",
                            tool_call_index=idx,
                            tool_call_id=slot["id"],
                            tool_name=slot["name"],
                            tool_args_delta=tc.function.arguments,
                        )

        # 流结束，上报 done 分片（携带完整 tool_calls 和 finish_reason）
        # 完整 tool_calls 通过 tool_calls_acc 可取，但 done 分片本身只传 finish_reason
        # 完整 tool_calls 由调用方在 done 后从 get_accumulated_tool_calls() 取
        # 这里把累积结果通过 done 分片的 text 字段以 JSON 形式回传，简化调用方逻辑
        if tool_calls_acc:
            complete = [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
                for _, slot in sorted(tool_calls_acc.items())
            ]
            yield StreamChunk(
                type="done",
                finish_reason=finish_reason or "tool_calls",
                text=json.dumps(complete, ensure_ascii=False),
            )
        else:
            yield StreamChunk(type="done", finish_reason=finish_reason or "stop")


# 测试探活与连通性的独立入口
if __name__ == "__main__":
    cfg = LLMConfig()
    cfg.model = "deepseek-chat"
    client = LLMClient(cfg)
    print(f"DeepSeek 可达: {client.is_reachable()}")
    print(f"已安装模型: {client.list_models()}")
    print("--- 流式测试 ---")
    for chunk in client.chat_stream([{"role": "user", "content": "你好，请用一句话介绍你自己"}]):
        if chunk.type == "content":
            print(chunk.text, end="", flush=True)
        elif chunk.type == "reasoning":
            pass  # 静默 CoT，或 print(f"[思考]{chunk.text}", end="", flush=True)
        elif chunk.type == "done":
            print(f"\n[finish={chunk.finish_reason}]")
