"""配置默认值一致性测试。"""
from config import AppConfig, SandboxConfig, MemoryConfig, _from_dict


def test_theme_default_is_dark():
    assert AppConfig().theme == "dark"


def test_from_dict_default_theme_is_dark():
    """缺失 theme 字段时也默认深色。"""
    assert _from_dict({}).theme == "dark"


def test_trusted_mode_default_false():
    """可信模式默认关闭。"""
    assert SandboxConfig().trusted_mode is False


def test_rule_extract_default_true():
    """规则提取默认每轮开启。"""
    assert MemoryConfig().rule_extract_every_turn is True
