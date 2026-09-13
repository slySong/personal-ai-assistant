"""文件操作工具：读、写、列目录。

路径策略：
- 非可信模式（默认）：绝对路径拒绝，只能访问沙箱目录内。
- 可信模式（trusted_mode=True）：绝对路径放行，可访问电脑任意位置。
- 相对路径始终相对于沙箱目录解析。

内容策略：
- read_file 对 .docx / .pptx / .xlsx 会提取其中的文本内容，
  其余文件按 UTF-8 文本读取。
- write_file 对 .docx 采用「追加段落」，其余按文本覆盖写入。
写操作在非可信模式下需用户确认。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from config import SandboxConfig
from tools.base import Tool, ToolResult

OFFICE_SUFFIXES = {".docx", ".pptx", ".xlsx"}


class SecurityError(Exception):
    """路径越界等安全违规。"""


def _resolve_path(path_text: str, sandbox_root: Path, trusted: bool = False) -> Path:
    """解析目标路径。

    - path_text 为空或 "." 返回 sandbox_root 本身
    - 绝对路径：可信模式放行，非可信模式（默认）拒绝
    - 相对路径解析后必须位于 sandbox_root 之下（Windows 不区分大小写）
    """
    if not path_text or path_text in (".", "./"):
        return sandbox_root

    p = Path(path_text)
    if p.is_absolute():
        if not trusted:
            raise SecurityError(f"非可信模式禁止绝对路径，只能访问沙箱目录：{path_text}")
        return p.resolve()

    base = sandbox_root.resolve()
    target = (base / path_text).resolve()

    base_lower = str(base).lower()
    target_lower = str(target).lower()
    if target_lower != base_lower and not target_lower.startswith(base_lower + "\\"):
        raise SecurityError(f"路径越界，禁止访问沙箱外：{path_text}")

    return target


def _extract_office_text(path: Path, suffix: str) -> str:
    """提取 Office 文档的文本内容（docx / pptx / xlsx）。"""
    if suffix == ".docx":
        import docx
        doc = docx.Document(str(path))
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                lines.append(" | ".join(cells))
        return "\n".join(lines)

    if suffix == ".pptx":
        from pptx import Presentation
        prs = Presentation(str(path))
        lines = []
        for i, slide in enumerate(prs.slides, 1):
            lines.append(f"[第 {i} 页]")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = "".join(r.text for r in para.runs)
                        if t.strip():
                            lines.append(t)
        return "\n".join(lines)

    if suffix == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            lines.append(f"[工作表：{ws.title}]")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                line = " | ".join(cells).rstrip(" |")
                if line.strip():
                    lines.append(line)
        return "\n".join(lines)

    return ""


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "读取指定文件的内容。支持 Word(.docx)、PPT(.pptx)、Excel(.xlsx) 提取文本，"
        "也支持普通文本文件。可信模式下可传绝对路径访问电脑任意位置"
        "（如 C:\\Users\\...\\a.docx），也可传相对路径访问沙箱目录。大文件会被截断。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径：相对沙箱根的路径，或可信模式下的绝对路径",
            },
            "max_bytes": {
                "type": "integer",
                "description": "最多读取的字节数，默认 200000（约 200KB）",
            },
        },
        "required": ["path"],
    }

    def __init__(self, sandbox_cfg: SandboxConfig):
        self.sandbox_root = Path(sandbox_cfg.root_dir)
        self.trusted = sandbox_cfg.trusted_mode

    def execute(self, path: str, max_bytes: int = 200000) -> ToolResult:
        try:
            target = _resolve_path(path, self.sandbox_root, self.trusted)
        except SecurityError as e:
            return ToolResult(ok=False, output=str(e))

        if not target.exists():
            return ToolResult(ok=False, output=f"文件不存在：{path}")
        if not target.is_file():
            return ToolResult(ok=False, output=f"不是文件：{path}")

        try:
            suffix = target.suffix.lower()
            size = target.stat().st_size
            if suffix in OFFICE_SUFFIXES:
                text = _extract_office_text(target, suffix)
                header = f"[文件 {path}，{size} 字节，{suffix} 文档]\n"
            else:
                data = target.read_bytes()[:max_bytes]
                text = data.decode("utf-8", errors="replace")
                truncated = "（已截断）" if size > max_bytes else ""
                header = f"[文件 {path}，{size} 字节{truncated}]\n"
            return ToolResult(ok=True, output=header + text[:max_bytes], raw={"size": size})
        except Exception as e:
            return ToolResult(ok=False, output=f"读取失败：{e}")


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "写入文件内容。对 .docx 采用「追加段落」（在文档末尾新增段落），"
        "其余文件按文本覆盖写入。可信模式下可传绝对路径写到电脑任意位置。此操作需用户确认。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径：相对沙箱根的路径，或可信模式下的绝对路径",
            },
            "content": {
                "type": "string",
                "description": "要写入的文本内容（.docx 按段落追加，多段用换行分隔）",
            },
        },
        "required": ["path", "content"],
    }

    def __init__(self, sandbox_cfg: SandboxConfig):
        self.sandbox_root = Path(sandbox_cfg.root_dir)
        self.trusted = sandbox_cfg.trusted_mode
        self.requires_confirmation = not sandbox_cfg.trusted_mode

    def execute(self, path: str, content: str) -> ToolResult:
        try:
            target = _resolve_path(path, self.sandbox_root, self.trusted)
        except SecurityError as e:
            return ToolResult(ok=False, output=str(e))

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            suffix = target.suffix.lower()

            if suffix == ".docx":
                import docx
                if target.exists():
                    doc = docx.Document(str(target))
                else:
                    doc = docx.Document()
                added = 0
                for line in content.split("\n"):
                    if line.strip():
                        doc.add_paragraph(line)
                        added += 1
                doc.save(str(target))
                return ToolResult(ok=True, output=f"已向 {path} 追加 {added} 个段落")

            target.write_text(content, encoding="utf-8")
            size = target.stat().st_size
            return ToolResult(
                ok=True,
                output=f"已写入 {path}（{size} 字节）",
                raw={"size": size, "path": str(target)},
            )
        except Exception as e:
            return ToolResult(ok=False, output=f"写入失败：{e}")


class ListFilesTool(Tool):
    name = "list_files"
    description = (
        "列出指定目录下的文件和文件夹。可信模式下可传绝对路径查看电脑任意目录"
        "（如 C:\\Users\\...\\Desktop），不传 path 时列出沙箱根目录。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径：相对沙箱根的路径（默认为沙箱根），或可信模式下的绝对路径",
            },
        },
        "required": [],
    }

    def __init__(self, sandbox_cfg: SandboxConfig):
        self.sandbox_root = Path(sandbox_cfg.root_dir)
        self.trusted = sandbox_cfg.trusted_mode

    def execute(self, path: str = ".") -> ToolResult:
        try:
            target = _resolve_path(path, self.sandbox_root, self.trusted)
        except SecurityError as e:
            return ToolResult(ok=False, output=str(e))

        if not target.exists():
            return ToolResult(ok=False, output=f"目录不存在：{path}")
        if not target.is_dir():
            return ToolResult(ok=False, output=f"不是目录：{path}")

        try:
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            if not entries:
                return ToolResult(ok=True, output=f"{path} 目录为空")
            lines = []
            for e in entries:
                tag = "[目录]" if e.is_dir() else f"[{e.stat().st_size}B]"
                lines.append(f"{tag} {e.name}")
            header = f"[目录 {path}]\n"
            return ToolResult(ok=True, output=header + "\n".join(lines))
        except OSError as e:
            return ToolResult(ok=False, output=f"列目录失败：{e}")