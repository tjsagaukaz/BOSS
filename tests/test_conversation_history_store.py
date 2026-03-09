from boss.conversation.history_store import ConversationHistoryStore


def test_history_store_creates_and_lists_threads(tmp_path):
    store = ConversationHistoryStore(tmp_path / "boss.db")

    thread = store.start_thread(project_name="legion", title="Fresh chat")
    store.append_turn(
        project_name="legion",
        thread_id=thread["id"],
        message="Explain the architecture",
        response="Here is the current architecture.",
        intent="conversation",
    )

    threads = store.list_threads(project_name="legion")

    assert threads[0]["id"] == thread["id"]
    assert threads[0]["turn_count"] == 1
    history = store.recent(project_name="legion", thread_id=thread["id"])
    assert history[0]["thread_id"] == thread["id"]


def test_history_store_can_delete_thread(tmp_path):
    store = ConversationHistoryStore(tmp_path / "boss.db")

    thread = store.start_thread(project_name="legion", title="Throwaway")
    store.append_turn(
        project_name="legion",
        thread_id=thread["id"],
        message="Test",
        response="Done",
        intent="conversation",
    )

    assert store.delete_thread(thread["id"], project_name="legion") is True
    assert store.list_threads(project_name="legion") == []


def test_history_store_exposes_legacy_history_as_thread(tmp_path):
    store = ConversationHistoryStore(tmp_path / "boss.db")

    store.append_turn(
        project_name="legion",
        message="Legacy message",
        response="Legacy reply",
        intent="conversation",
    )
    with store._connect() as conn:
        conn.execute("UPDATE conversation_history SET thread_id = NULL")

    threads = store.list_threads(project_name="legion")
    legacy_thread = next(thread for thread in threads if thread.get("legacy"))

    assert legacy_thread["legacy"] is True
    legacy_id = legacy_thread["id"]
    history = store.recent(project_name="legion", thread_id=legacy_id)
    assert history[0]["message"] == "Legacy message"
