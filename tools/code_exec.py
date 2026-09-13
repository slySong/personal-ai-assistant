"""代码与命令执行工具（Windows 防御性沙箱）。

诚实声明：v1 是防御性限制非真隔离。真隔离需 Docker/WSL，作为可选加固。

安全策略：
- 工作目录禁锢在沙箱根
- 危险命令黑名单（正则）拦截 rm/del/format/diskpart/shutdown/reg/taskkill 等
- 超时控制（默认 30s）
- 输出截断（默认 5000 字符）
- shell 命令非可信模式默认弹窗确认（危险命令始终直接拒绝，不进入执行）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from config import SandboxConfig
from tools.base import Tool, ToolResult


# 危险命令模式（正则，忽略大小写）
DANGEROUS_PATTERNS = [
    r"\b(rm|del|erase|rmdir|rd)\b\s",  # 删除文件/目录
    r"\bformat\b",  # 格式化
    r"\bdiskpart\b",  # 磁盘分区操作
    r"\bshutdown\b",  # 关机
    r"\bshutdown\b",  # 关机
    r"\brestart\b",
    r"\breg\b\s+(add|delete|import|load|restore)",  # 注册表改写
    r"\btaskkill\b",  # 杀进程
    r"\bnet\b\s+(user|share|stop|localgroup)",  # 用户/共享管理
    r"\b(ipconfig|netsh)\s.*/(release|renew)",  # 释放网络
    r"[>:].*[Cc]:\\(Windows|System32)",  # 重定向到系统目录
    r"\bpowershell\b.*-enc(odedcommand)?",  # base64 混淆
    r"\bwscript\b|\bcscript\b",  # 脚本宿主
    r"\bmshta\b",  # HTA 应用
    r"https?://[^\s|]+\|\s*(iex|Invoke-Expression)",  # 远程脚本执行
    r"\bcurl\b.*\|\s*(sh|bash|python)",  # 远程脚本管道执行
    r"\biwr\b.*\|\s*iex",  # Invoke-WebRequest | Invoke-Expression
]


def _is_dangerous(command: str) -> str | None:
    """检查命令是否命中危险模式，返回命中的模式描述，否则 None。"""
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, command, re.IGNORECASE):
            return pat
    return None


class ExecPythonTool(Tool):
    name = "exec_python"
    description = (
        "在沙箱目录内执行一段 Python 代码，返回 stdout/stderr。"
        "工作目录为沙箱根，可读写沙箱内文件。超时默认 30 秒。"
        "适合数据分析、脚本验证、文件批处理。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码（UTF-8 文本）",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 30",
            },
        },
        "required": ["code"],
    }

    def __init__(self, sandbox_cfg: SandboxConfig):
        self.sandbox_root = Path(sandbox_cfg.root_dir).resolve()
        self.timeout = sandbox_cfg.exec_timeout
        self.max_output = sandbox_cfg.max_output_chars

    def execute(self, code: str, timeout: int | None = None) -> ToolResult:
        timeout = timeout or self.timeout
        # 写入临时 .py 文件，子进程执行，cwd 限定沙箱
        try:
            self.sandbox_root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                dir=str(self.sandbox_root),
                delete=False,
                encoding="utf-8",
                prefix="_exec_",
            ) as f:
                f.write(code)
                script_path = Path(f.name)
        except OSError as e:
            return ToolResult(ok=False, output=f"创建临时脚本失败：{e}")

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(self.sandbox_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            output = (result.stdout or "") + (result.stderr or "")
            output = output[: self.max_output]
            truncated = "（输出已截断）" if len((result.stdout or "") + (result.stderr or "")) > self.max_output else ""
            header = f"[退出码 {result.returncode}{truncated}]\n"
            return ToolResult(ok=True, output=header + output, raw={"returncode": result.returncode})
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output=f"执行超时（{timeout}s），已终止")
        except OSError as e:
            return ToolResult(ok=False, output=f"启动失败：{e}")
        finally:
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass


class ExecCommandTool(Tool):
    name = "exec_command"
    description = (
        "在沙箱目录内执行一条 shell 命令（cmd/powershell 自动选择），返回输出。"
        "工作目录为沙箱根。"
        "危险命令（删除、格式化、注册表改写、关机等）会被直接拒绝；"
        "非可信模式下执行前需用户确认。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令字符串",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 30",
            },
        },
        "required": ["command"],
    }

    def __init__(self, sandbox_cfg: SandboxConfig):
        self.sandbox_root = Path(sandbox_cfg.root_dir).resolve()
        self.timeout = sandbox_cfg.exec_timeout
        self.max_output = sandbox_cfg.max_output_chars
        # 非可信模式需要确认，可信模式免确认（危险命令仍直接拒绝）
        self.requires_confirmation = not sandbox_cfg.trusted_mode

    def execute(self, command: str, timeout: int | None = None) -> ToolResult:
        timeout = timeout or self.timeout

        # 危险命令拦截
        dangerous = _is_dangerous(command)
        if dangerous:
            return ToolResult(
                ok=False,
                output=f"命令命中危险模式（{dangerous}），已拒绝执行。",
            )

        try:
            # Windows 默认用 cmd，命令里含 powershell 关键字时自动用 powershell
            if re.search(r"\b(powershell|pwsh)\b", command, re.IGNORECASE):
                full_cmd = command
                executable = None
                shell_flag = True
            else:
                full_cmd = command
                executable = None
                shell_flag = True

            result = subprocess.run(
                full_cmd,
                shell=shell_flag,
                cwd=str(self.sandbox_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            output = (result.stdout or "") + (result.stderr or "")
            truncated = "（输出已截断）" if len(output) > self.max_output else ""
            output = output[: self.max_output]
            header = f"[退出码 {result.returncode}{truncated}]\n"
            return ToolResult(ok=True, output=header + output, raw={"returncode": result.returncode})
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output=f"执行超时（{timeout}s），已终止")
        except OSError as e:
            return ToolResult(ok=False, output=f"启动失败：{e}")
