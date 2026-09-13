"""对话历史存储测试。"""
from core.conversation import ConversationStore


def test_get_messages_returns_latest(tmp_path):
    """超过 limit 条消息时，应返回最新的 limit 条，顺序从旧到新。"""
    store = ConversationStore(tmp_path / "test.db")
    sid = store.create_session()
    for i in range(105):
        store.add_message(sid, "user", f"消息{i}")

    msgs = store.get_messages(sid)  # 默认 limit=100
    assert len(msgs) == 100
    # 包含最新的一条消息
    assert msgs[-1]["content"] == "消息104"
    # 最早 5 条被丢弃
    assert msgs[0]["content"] == "消息5"
    # 顺序从旧到新
    contents = [m["content"] for m in msgs]
    assert contents == [f"消息{i}" for i in range(5, 105)]


def test_clear_session_removes_messages(tmp_path):
    """清空会话后消息删除，但会话本身保留。"""
    store = ConversationStore(tmp_path / "test.db")
    sid = store.create_session()
    store.add_message(sid, "user", "你好")
    store.add_message(sid, "assistant", "你好！")

    store.clear_session(sid)
    assert store.get_messages(sid) == []
    sessions = store.list_sessions()
    assert any(s["id"] == sid for s in sessions)
