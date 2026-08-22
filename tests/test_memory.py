"""偏好记忆系统测试。"""
from config import MemoryConfig
from core.memory import MemoryStore, Preference


def test_rule_extract_name(tmp_path):
    """规则提取：我叫X → name。"""
    store = MemoryStore(tmp_path / "test.db")
    prefs = store._extract_rules("我叫小张，我是做前端开发的")
    keys = {p.key: p for p in prefs}
    assert "name" in keys
    assert keys["name"].value == "小张"
    assert keys["name"].confidence == 0.9
    assert "occupation" in keys


def test_rule_extract_language(tmp_path):
    """规则提取：用X语言 → primary_language。"""
    store = MemoryStore(tmp_path / "test.db")
    prefs = store._extract_rules("我平时用 TypeScript 写代码")
    keys = {p.key: p for p in prefs}
    assert "primary_language" in keys
    assert "TypeScript" in keys["primary_language"].value


def test_rule_extract_reply_format(tmp_path):
    """规则提取：回复简洁 → reply_format。"""
    store = MemoryStore(tmp_path / "test.db")
    prefs = store._extract_rules("回复尽量简洁")
    keys = {p.key: p for p in prefs}
    assert "reply_format" in keys
    assert "简洁" in keys["reply_format"].value


def test_rule_extract_no_match(tmp_path):
    """无偏好时不提取。"""
    store = MemoryStore(tmp_path / "test.db")
    prefs = store._extract_rules("今天天气不错")
    assert prefs == []


def test_upsert_high_confidence_overrides(tmp_path):
    """高置信覆盖低置信。"""
    store = MemoryStore(tmp_path / "test.db")
    store.upsert(Preference(key="name", value="旧名", category="identity", confidence=0.5))
    store.upsert(Preference(key="name", value="新名", category="identity", confidence=0.9))
    prefs = {p.key: p for p in store.get_all_active()}
    assert prefs["name"].value == "新名"


def test_upsert_low_confidence_not_override(tmp_path):
    """低置信不覆盖高置信。"""
    store = MemoryStore(tmp_path / "test.db")
    store.upsert(Preference(key="name", value="高置信名", category="identity", confidence=0.9))
    store.upsert(Preference(key="name", value="低置信名", category="identity", confidence=0.5))
    prefs = {p.key: p for p in store.get_all_active()}
    assert prefs["name"].value == "高置信名"


def test_build_profile_prompt(tmp_path):
    """注入块生成。"""
    store = MemoryStore(tmp_path / "test.db")
    store.upsert(Preference(key="name", value="小张", category="identity", confidence=0.9))
    store.upsert(Preference(key="occupation", value="前端开发", category="work", confidence=0.85))
    prompt = store.build_profile_prompt()
    assert "小张" in prompt
    assert "前端开发" in prompt
    assert "用户偏好" in prompt


def test_build_profile_prompt_filters_low_confidence(tmp_path):
    """低于阈值的偏好不注入。"""
    cfg = MemoryConfig(min_confidence_to_inject=0.5)
    store = MemoryStore(tmp_path / "test.db", cfg=cfg)
    store.upsert(Preference(key="name", value="小张", category="identity", confidence=0.9))
    store.upsert(Preference(key="hobby", value="低置信", category="preference", confidence=0.3))
    prompt = store.build_profile_prompt()
    assert "小张" in prompt
    assert "低置信" not in prompt


def test_delete(tmp_path):
    """删除偏好。"""
    store = MemoryStore(tmp_path / "test.db")
    store.upsert(Preference(key="name", value="小张", category="identity", confidence=0.9))
    assert store.delete("name")
    assert not store.delete("name")  # 已删除
    assert store.get_all_active() == []


def test_update_value(tmp_path):
    """手动编辑偏好值。"""
    store = MemoryStore(tmp_path / "test.db")
    store.upsert(Preference(key="name", value="小张", category="identity", confidence=0.9))
    assert store.update_value("name", "老张")
    prefs = {p.key: p for p in store.get_all_active()}
    assert prefs["name"].value == "老张"
    assert prefs["name"].source == "manual"


def test_maybe_extract_rules(tmp_path):
    """maybe_extract 触发规则提取。"""
    store = MemoryStore(tmp_path / "test.db")
    store.maybe_extract(1, "我叫小张", "你好小张")
    prefs = {p.key: p for p in store.get_all_active()}
    assert "name" in prefs
    assert prefs["name"].value == "小张"
    assert prefs["name"].source == "rule"


def test_hit_count_increments(tmp_path):
    """注入时 hit_count 递增。"""
    store = MemoryStore(tmp_path / "test.db")
    store.upsert(Preference(key="name", value="小张", category="identity", confidence=0.9))
    store.build_profile_prompt()
    store.build_profile_prompt()
    prefs = {p.key: p for p in store.get_all_active()}
    assert prefs["name"].hit_count == 2
