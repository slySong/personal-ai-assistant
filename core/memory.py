"""偏好记忆系统：规则 + LLM 混合提取，注入 system prompt。

这是本项目区别于普通聊天机器人的核心：记住用户的称呼、职业、技术栈、
代码风格、回复格式偏好，每次对话自动注入 system prompt，让助手越用越懂用户。

提取策略：
- 规则提取：每轮用户消息后即时跑正则（毫秒级，高置信模式）
- LLM 提取：每 N 轮用户消息异步跑结构化提取（本地推理零成本）

合并策略：高置信覆盖低置信，同 key 更新。
注入策略：confidence >= min_confidence 的偏好注入，hit_count 高的排前。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from config import MemoryConfig, DB_FILE


# 规则提取模式：(正则, key, category, confidence)
# 按优先级排序，先匹配高置信模式
RULES: list[tuple[str, str, str, float]] = [
    # 称呼：我叫小张 / 我名叫李明 / 我是王五（注意"我是做X的"要排除，放后面）
    (r"(?:我名叫?|我叫|本人叫|鄙人叫|我的名字叫|叫我)([^\s,，。.!！?？]{1,10})", "name", "identity", 0.9),
    # 职业：我是做前端开发的 / 我是前端开发 / 我在做设计工作
    (r"我是做(.{1,15}?)(?:的|工作|的活)", "occupation", "work", 0.85),
    (r"我是(.{1,10}?)(?:工程师|开发者|程序员|设计师|产品经理|运营|老师|学生|研究员)", "occupation", "work", 0.85),
    # 主用语言：我用 TypeScript 写代码 / 我平时用 Python 编程
    (r"(?:我用|我平时用|我主要用|我习惯用)\s*([A-Za-z+#]{2,20})(?:语言)?\s*(?:编程|写代码|开发|敲代码)", "primary_language", "work", 0.8),
    # 回复格式偏好：回复简洁 / 回答要详细
    (r"(?:回复|回答|答案)(?:要|尽量|请|应该)(简洁|简短|精简|详细|带代码|带示例|分步骤|分点)", "reply_format", "style", 0.75),
    (r"(?:我喜欢|我想要|我希望)(?:你)?(简洁|简短|详细|带代码|带示例)的?(?:回复|回答|风格)?", "reply_format", "style", 0.7),
    # 代码风格：代码用 2 空格缩进 / 用箭头函数
    (r"(?:代码|缩进)(?:用|是|要)\s*(\d)\s*个?空格", "code_indent", "style", 0.75),
    (r"(?:优先|尽量)(?:用|使用)(箭头函数|函数式|面向对象|类|异步|await)", "code_style", "style", 0.7),
]

# key 的中文显示名（注入时用）
KEY_LABELS = {
    "name": "称呼",
    "occupation": "职业",
    "primary_language": "主用语言",
    "reply_format": "回复格式",
    "code_indent": "代码缩进",
    "code_style": "代码风格",
}


@dataclass
class Preference:
    """单条偏好。"""
    key: str
    value: str
    category: str  # identity | work | style | preference
    confidence: float
    source: str = "rule"  # rule | llm | manual
    hit_count: int = 0
    updated_at: str = ""


class MemoryStore:
    """偏好记忆持久化与注入。实现 MemoryProtocol 供 Agent 使用。"""

    def __init__(
        self,
        db_path: Path = DB_FILE,
        cfg: Optional[MemoryConfig] = None,
        llm_client=None,
    ):
        self.db_path = Path(db_path)
        self.cfg = cfg or MemoryConfig()
        self.llm_client = llm_client
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        # 每 session 的用户消息计数（内存，重启归零，仅用于触发 LLM 提取节奏）
        self._turn_counts: dict[int, int] = {}
        self._lock = threading.Lock()

    @contextmanager
    def _conn(self):
        """连接上下文管理器：成功时 commit，无论如何都 close。

        注意 sqlite3.Connection 自带的 __enter__/__exit__ 只管事务
        （commit/rollback），不会关闭连接，Windows 上会导致文件锁
        不释放、临时目录无法清理。这里显式 close。
        """
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT NOT NULL,
                    category TEXT NOT NULL,
                    confidence REAL DEFAULT 0.5,
                    source TEXT DEFAULT 'rule',
                    hit_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime'))
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER,
                    action TEXT,
                    detail TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                )
                """
            )

    # ---- 增删改查 ----

    def upsert(self, pref: Preference) -> None:
        """插入或更新。高置信覆盖低置信。"""
        with self._lock, self._conn() as c:
            existing = c.execute(
                "SELECT confidence, hit_count FROM preferences WHERE key = ?",
                (pref.key,),
            ).fetchone()
            if existing is None:
                c.execute(
                    "INSERT INTO preferences (key, value, category, confidence, source, hit_count) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (pref.key, pref.value, pref.category, pref.confidence,
                     pref.source, pref.hit_count),
                )
            else:
                # 高置信覆盖低置信；同置信则更新值
                if pref.confidence >= existing["confidence"]:
                    new_hit = existing["hit_count"]  # 保留原 hit_count
                    c.execute(
                        "UPDATE preferences SET value=?, category=?, confidence=?, source=?, "
                        "updated_at=datetime('now','localtime') WHERE key=?",
                        (pref.value, pref.category, pref.confidence, pref.source, pref.key),
                    )
                # 低置信不覆盖

    def get_all_active(self) -> list[Preference]:
        """获取所有偏好（按 hit_count 降序、category 分组）。"""
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT key, value, category, confidence, source, hit_count, updated_at "
                "FROM preferences ORDER BY hit_count DESC, category, key"
            ).fetchall()
        return [Preference(
            key=r["key"], value=r["value"], category=r["category"],
            confidence=r["confidence"], source=r["source"],
            hit_count=r["hit_count"], updated_at=r["updated_at"],
        ) for r in rows]

    def delete(self, key: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM preferences WHERE key = ?", (key,))
            return cur.rowcount > 0

    def update_value(self, key: str, value: str) -> bool:
        """手动编辑偏好值。"""
        with self._lock, self._conn() as c:
            cur = c.execute(
                "UPDATE preferences SET value=?, source='manual', "
                "updated_at=datetime('now','localtime') WHERE key=?",
                (value, key),
            )
            return cur.rowcount > 0

    def _log(self, session_id: int, action: str, detail: str) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO memory_log (session_id, action, detail) VALUES (?, ?, ?)",
                    (session_id, action, detail),
                )
        except sqlite3.Error:
            pass

    # ---- 注入 ----

    def build_profile_prompt(self) -> str:
        """生成注入 system prompt 的偏好块。"""
        prefs = self.get_all_active()
        # 过滤低置信
        prefs = [p for p in prefs if p.confidence >= self.cfg.min_confidence_to_inject]
        if not prefs:
            return ""

        lines = ["[用户偏好 - 每次对话自动注入，请严格遵守以下偏好定制回复]"]
        # 按 category 分组
        by_cat: dict[str, list[Preference]] = {}
        for p in prefs:
            by_cat.setdefault(p.category, []).append(p)
        for cat in ["identity", "work", "style", "preference"]:
            if cat not in by_cat:
                continue
            for p in by_cat[cat]:
                label = KEY_LABELS.get(p.key, p.key)
                lines.append(f"- {label}：{p.value}")

        # 递增 hit_count（异步、非阻塞，失败无所谓）
        try:
            with self._lock, self._conn() as c:
                for p in prefs:
                    c.execute(
                        "UPDATE preferences SET hit_count = hit_count + 1 WHERE key = ?",
                        (p.key,),
                    )
        except sqlite3.Error:
            pass

        return "\n".join(lines)

    # ---- 提取 ----

    def maybe_extract(
        self, session_id: int, user_input: str, assistant_text: str
    ) -> None:
        """触发偏好提取。规则每轮跑，LLM 每 N 轮跑。

        Agent 在最终答案后调用此方法。为避免阻塞 UI，LLM 提取在后台线程。
        """
        # 1. 规则提取（即时，毫秒级）
        new_prefs = self._extract_rules(user_input)
        for pref in new_prefs:
            self.upsert(pref)
        if new_prefs:
            self._log(
                session_id, "extract_rule",
                json.dumps([asdict(p) for p in new_prefs], ensure_ascii=False),
            )

        # 2. LLM 提取（每 N 轮）
        with self._lock:
            self._turn_counts[session_id] = self._turn_counts.get(session_id, 0) + 1
            turn = self._turn_counts[session_id]

        if (
            self.llm_client is not None
            and turn % self.cfg.llm_extract_interval == 0
        ):
            # 后台线程跑 LLM 提取，不阻塞
            t = threading.Thread(
                target=self._extract_llm_async,
                args=(session_id,),
                daemon=True,
            )
            t.start()

    def _extract_rules(self, text: str) -> list[Preference]:
        """正则规则提取。"""
        results: list[Preference] = []
        for pattern, key, category, confidence in RULES:
            m = re.search(pattern, text)
            if m:
                value = m.group(1).strip()
                if value and len(value) <= 30:
                    results.append(Preference(
                        key=key, value=value, category=category,
                        confidence=confidence, source="rule",
                    ))
        return results

    def _extract_llm_async(self, session_id: int) -> None:
        """后台 LLM 提取。从 conversation 拉最近消息，调 LLM 结构化提取。"""
        try:
            # 延迟导入避免循环依赖
            from core.conversation import ConversationStore
            conv = ConversationStore(self.db_path)
            messages = conv.get_messages(session_id, limit=20)
            if len(messages) < 2:
                return

            # 构造提取 prompt
            recent = [
                {"role": m["role"], "content": m.get("content") or ""}
                for m in messages
                if m["role"] in ("user", "assistant") and m.get("content")
            ][-10:]  # 最近 10 条
            if not recent:
                return

            prefs = self._extract_llm(recent)
            for pref in prefs:
                self.upsert(pref)
            if prefs:
                self._log(
                    session_id, "extract_llm",
                    json.dumps([asdict(p) for p in prefs], ensure_ascii=False),
                )
        except Exception:
            # 后台提取失败不影响主流程
            pass

    def _extract_llm(self, recent_messages: list[dict]) -> list[Preference]:
        """调用 LLM 做结构化偏好提取。返回 Preference 列表。"""
        if self.llm_client is None:
            return []

        prompt = """你是偏好提取器。从下面对话中提取用户的稳定偏好。

只提取以下类别中明确或强暗示的偏好，不要臆测：
- 称呼（name）：用户怎么称呼
- 职业（occupation）：用户做什么工作
- 主用语言（primary_language）：主要编程语言
- 回复格式（reply_format）：简洁/详细/带代码等
- 代码风格（code_style, code_indent）：缩进、函数式等

输出 JSON 数组，每项 {"key","value","category","confidence"}。
confidence 取 0.5-0.9。无偏好则输出 []。只输出 JSON，不要解释。

对话：
""" + json.dumps(recent_messages, ensure_ascii=False, indent=2)

        try:
            # 非流式调用（直接用 OpenAI SDK）
            resp = self.llm_client.client.chat.completions.create(
                model=self.llm_client.cfg.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=512,
                stream=False,
            )
            text = resp.choices[0].message.content or ""
            # 提取 JSON（容错：可能包裹在 ```json 里）
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            data = json.loads(text)
            results: list[Preference] = []
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    key = item.get("key", "")
                    value = item.get("value", "")
                    if not key or not value:
                        continue
                    results.append(Preference(
                        key=key,
                        value=str(value)[:100],
                        category=item.get("category", "preference"),
                        confidence=min(0.9, max(0.5, float(item.get("confidence", 0.6)))),
                        source="llm",
                    ))
            return results
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return []
        except Exception:
            return []
