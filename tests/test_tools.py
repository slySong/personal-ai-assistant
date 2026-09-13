"""工具系统测试：路径解析、危险命令拦截、文件/代码执行。"""
import pytest
from config import SandboxConfig
from tools.file_ops import (
    ListFilesTool,
    ReadFileTool,
    SecurityError,
    WriteFileTool,
    _resolve_path,
)
from tools.code_exec import (
    ExecCommandTool,
    ExecPythonTool,
    _is_dangerous,
)


# ---- 路径解析 ----

def test_resolve_path_normal(tmp_path):
    p = _resolve_path("sub/file.txt", tmp_path)
    assert str(p).startswith(str(tmp_path))


def test_resolve_path_root(tmp_path):
    p = _resolve_path(".", tmp_path)
    assert p == tmp_path.resolve()


def test_resolve_path_rejects_dotdot(tmp_path):
    with pytest.raises(SecurityError):
        _resolve_path("../../etc/passwd", tmp_path)


def test_resolve_path_rejects_absolute_by_default(tmp_path):
    with pytest.raises(SecurityError):
        _resolve_path("C:\\Windows\\system32", tmp_path)


def test_resolve_path_allows_absolute_when_trusted(tmp_path):
    p = _resolve_path("C:\\Windows\\system32", tmp_path, trusted=True)
    assert p.is_absolute()


def test_resolve_path_rejects_unc_by_default(tmp_path):
    with pytest.raises(SecurityError):
        _resolve_path("\\\\server\\share", tmp_path)


# ---- 危险命令检测 ----

def test_is_dangerous_del():
    assert _is_dangerous("del C:\\important.txt") is not None


def test_is_dangerous_format():
    assert _is_dangerous("format C:") is not None


def test_is_dangerous_rm():
    assert _is_dangerous("rm -rf /") is not None


def test_is_dangerous_shutdown():
    assert _is_dangerous("shutdown /s /t 0") is not None


def test_is_dangerous_reg_add():
    assert _is_dangerous("reg add HKLM\\Software\\Test") is not None


def test_is_dangerous_powershell_enc():
    assert _is_dangerous("powershell -enc SGVsbG8=") is not None


def test_is_dangerous_safe_commands():
    assert _is_dangerous("dir") is None
    assert _is_dangerous("echo hello") is None
    assert _is_dangerous("python script.py") is None


# ---- 文件工具 ----

def test_read_file(tmp_path):
    (tmp_path / "test.txt").write_text("hello world", encoding="utf-8")
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = ReadFileTool(cfg)
    result = tool.execute(path="test.txt")
    assert result.ok
    assert "hello world" in result.output


def test_read_file_not_exist(tmp_path):
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = ReadFileTool(cfg)
    result = tool.execute(path="nonexist.txt")
    assert not result.ok


def test_read_file_rejects_dotdot(tmp_path):
    (tmp_path.parent / "secret.txt").write_text("secret", encoding="utf-8")
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = ReadFileTool(cfg)
    result = tool.execute(path="../secret.txt")
    assert not result.ok


def test_write_file(tmp_path):
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = WriteFileTool(cfg)
    result = tool.execute(path="new.txt", content="content")
    assert result.ok
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "content"


def test_write_file_creates_subdir(tmp_path):
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = WriteFileTool(cfg)
    result = tool.execute(path="sub/dir/new.txt", content="nested")
    assert result.ok
    assert (tmp_path / "sub/dir/new.txt").read_text(encoding="utf-8") == "nested"


def test_write_file_rejects_dotdot(tmp_path):
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = WriteFileTool(cfg)
    result = tool.execute(path="../../evil.txt", content="hack")
    assert not result.ok
    assert not (tmp_path.parent.parent / "evil.txt").exists()


def test_list_files(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.py").write_text("b", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = ListFilesTool(cfg)
    result = tool.execute(path=".")
    assert result.ok
    assert "a.txt" in result.output
    assert "b.py" in result.output
    assert "sub" in result.output


# ---- 代码执行 ----

def test_exec_python(tmp_path):
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = ExecPythonTool(cfg)
    result = tool.execute(code="print(1+1)")
    assert result.ok
    assert "2" in result.output


def test_exec_python_with_error(tmp_path):
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = ExecPythonTool(cfg)
    result = tool.execute(code="raise ValueError('test error')")
    assert result.ok  # 执行成功（退出码非0但仍返回输出）
    assert "test error" in result.output


def test_exec_python_timeout(tmp_path):
    cfg = SandboxConfig(root_dir=str(tmp_path), exec_timeout=2)
    tool = ExecPythonTool(cfg)
    result = tool.execute(code="import time; time.sleep(10)")
    assert not result.ok
    assert "超时" in result.output


def test_exec_command_rejects_dangerous(tmp_path):
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = ExecCommandTool(cfg)
    result = tool.execute(command="del C:\\Windows\\system32\\xxx")
    assert not result.ok
    assert "危险" in result.output


def test_exec_command_safe(tmp_path):
    cfg = SandboxConfig(root_dir=str(tmp_path))
    tool = ExecCommandTool(cfg)
    result = tool.execute(command="echo hello_test")
    assert result.ok
    assert "hello_test" in result.output


def test_exec_command_requires_confirmation():
    """非可信模式下 shell 命令默认需要确认。"""
    from config import SandboxConfig
    cfg = SandboxConfig(root_dir=".")
    tool = ExecCommandTool(cfg)
    assert tool.requires_confirmation is True


def test_exec_command_no_confirmation_when_trusted():
    """可信模式下 shell 命令不需要确认。"""
    from config import SandboxConfig
    cfg = SandboxConfig(root_dir=".", trusted_mode=True)
    tool = ExecCommandTool(cfg)
    assert tool.requires_confirmation is False


def test_write_file_requires_confirmation_default():
    """非可信模式下写文件需要确认。"""
    cfg = SandboxConfig(root_dir=".")
    assert WriteFileTool(cfg).requires_confirmation is True


def test_write_file_no_confirmation_when_trusted():
    """可信模式下写文件不需要确认。"""
    cfg = SandboxConfig(root_dir=".", trusted_mode=True)
    assert WriteFileTool(cfg).requires_confirmation is False


def test_exec_python_no_confirmation():
    """ExecPythonTool 不需要确认。"""
    cfg = SandboxConfig(root_dir=".")
    tool = ExecPythonTool(cfg)
    assert tool.requires_confirmation is False


def test_tool_registry_unknown_tool(tmp_path):
    """未知工具返回错误。"""
    from tools.base import ToolRegistry
    registry = ToolRegistry()
    result = registry.execute("nonexistent", {})
    assert not result.ok
