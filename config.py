"""配置管理：dataclass 配置 + config.json 持久化。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# 项目根目录（config.py 在项目根，父目录就是项目根）
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_FILE = DATA_DIR / "config.json"
DB_FILE = DATA_DIR / "assistant.db"
SANDBOX_DIR = DATA_DIR / "sandbox"


@dataclass
class LLMConfig:
    """LLM 后端配置（OpenAI 兼容 API）。

    连接 DeepSeek 云端 API（https://api.deepseek.com/v1）。
    注意：DeepSeek 的 deepseek-reasoner（R1）不支持工具调用，
    接本助手必须用 deepseek-chat（V3）。
    """
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""  # 在设置里填写（https://platform.deepseek.com 申请）
    model: str = "deepseek-chat"  # 支持工具调用
    temperature: float = 0.6
    max_tokens: int = 4096
    timeout: int = 180  # 网络请求超时
    request_timeout: int = 300  # OpenAI SDK 客户端整体超时


@dataclass
class SandboxConfig:
    """代码执行沙箱配置。"""
    root_dir: str = str(SANDBOX_DIR)
    exec_timeout: int = 30  # 子进程超时秒数
    max_output_chars: int = 5000  # 输出截断长度
    confirm_shell_commands: bool = False  # shell 命令执行是否需要弹窗确认（危险命令始终拦截）


@dataclass
class MemoryConfig:
    """偏好记忆系统配置。"""
    rule_extract_every_turn: bool = True  # 每轮即时规则提取
    llm_extract_interval: int = 5  # 每 N 轮用户消息触发一次 LLM 提取
    min_confidence_to_inject: float = 0.5  # 低于此置信度的偏好不注入


@dataclass
class AgentConfig:
    """Agent 循环配置。"""
    max_iterations: int = 10  # 工具调用最大迭代次数，防死循环
    context_max_tokens: int = 16000  # 历史消息滑动窗口裁剪阈值


@dataclass
class AppConfig:
    """应用总配置。"""
    llm: LLMConfig = field(default_factory=LLMConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    window_width: int = 1200
    window_height: int = 800
    theme: str = "dark"  # light / dark

    @classmethod
    def load(cls) -> "AppConfig":
        """从 config.json 加载配置，文件不存在则用默认值并创建。"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                data: dict[str, Any] = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                return _from_dict(data)
            except (json.JSONDecodeError, KeyError, TypeError):
                # 配置文件损坏则用默认值
                pass
        cfg = cls()
        cfg.save()
        return cfg

    def save(self) -> None:
        """持久化到 config.json。"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _from_dict(data: dict[str, Any]) -> AppConfig:
    """从 dict 重建 AppConfig，容忍字段缺失。"""
    return AppConfig(
        llm=LLMConfig(**data.get("llm", {})),
        sandbox=SandboxConfig(**data.get("sandbox", {})),
        memory=MemoryConfig(**data.get("memory", {})),
        agent=AgentConfig(**data.get("agent", {})),
        window_width=data.get("window_width", 1200),
        window_height=data.get("window_height", 800),
        theme=data.get("theme", "light"),
    )
