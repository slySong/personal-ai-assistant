"""联网搜索工具：优先 DuckDuckGo（ddgs），失败自动降级 Bing（国内可访问）。

原因：DuckDuckGo 走境外，部分网络（尤其国内）会超时/限流。
Bing 国内（cn.bing.com）可直连，作为可靠降级源。
"""
from __future__ import annotations

import html
import re
import time
import urllib.parse
import urllib.request
from typing import Any

from tools.base import Tool, ToolResult


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "联网搜索给定查询词，返回最多 N 条结果（标题、URL、摘要）。"
        "用于获取实时信息或你不确定的事实。适合查最新版本、文档、新闻。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询词",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回条数，默认 5",
            },
        },
        "required": ["query"],
    }

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries

    def execute(self, query: str, max_results: int = 5) -> ToolResult:
        # 1. 优先 DuckDuckGo
        ddgs_result = self._search_ddgs(query, max_results)
        if ddgs_result is not None:
            return ddgs_result

        # 2. 降级 Bing（国内可直连）
        bing_result = self._search_bing(query, max_results)
        if bing_result is not None:
            return bing_result

        return ToolResult(
            ok=False,
            output="搜索失败：DuckDuckGo 与 Bing 均不可用，请检查网络。",
        )

    # ---- DuckDuckGo ----

    def _search_ddgs(self, query: str, max_results: int) -> ToolResult | None:
        try:
            from ddgs import DDGS
        except ImportError:
            return None  # 库缺失，降级

        last_err = ""
        for attempt in range(self.max_retries):
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=max_results))
                if not results:
                    return ToolResult(ok=True, output=f"未找到关于 '{query}' 的结果")
                return self._format_results(query, results)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                if attempt < self.max_retries - 1:
                    time.sleep(1)
        # DuckDuckGo 失败，交给降级
        return None

    # ---- Bing（国内直连）----

    def _search_bing(self, query: str, max_results: int) -> ToolResult | None:
        url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                page = resp.read().decode("utf-8", "replace")
        except Exception:
            return None

        results = self._parse_bing(page)
        if not results:
            return ToolResult(ok=True, output=f"未找到关于 '{query}' 的结果")
        return self._format_results(query, results[:max_results])

    def _parse_bing(self, page: str) -> list[dict]:
        """解析 Bing 搜索结果页中的 b_algo 条目。"""
        out: list[dict] = []
        for li in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
            m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', li, re.S)
            if not m:
                continue
            url = html.unescape(m.group(1))
            title = self._strip_tags(m.group(2))
            # 摘要：标题后的 <p> 标签
            snippet = ""
            pm = re.search(r"<p[^>]*>(.*?)</p>", li, re.S)
            if pm:
                snippet = self._strip_tags(pm.group(1))
            out.append({"title": title, "href": url, "body": snippet})
        return out

    @staticmethod
    def _strip_tags(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s)
        return html.unescape(s).strip()

    # ---- 通用格式化 ----

    def _format_results(self, query: str, results: list) -> ToolResult:
        lines = [f"[搜索 '{query}'，{len(results)} 条结果]\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title") or "无标题"
            url = r.get("href") or r.get("url") or r.get("link", "")
            body = r.get("body") or r.get("snippet", "")
            lines.append(f"{i}. {title}\n   {url}\n   {body}\n")
        return ToolResult(ok=True, output="\n".join(lines), raw=results)