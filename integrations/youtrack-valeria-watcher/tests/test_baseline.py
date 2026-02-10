from youtrack_valeria_watcher.baseline import record_baseline
from youtrack_valeria_watcher.store import Store


def test_baseline_is_atomic_and_idempotent(tmp_path):
    database = tmp_path / "state.db"

    first = record_baseline(database, 1_000)
    second = record_baseline(database, 9_000)

    assert first == (1_000, True)
    assert second == (1_000, False)
    store = Store(database)
    try:
        assert store.get_meta_int("initialized_at_ms") == 1_000
        assert store.get_meta_int("last_success_at_ms") == 1_000
    finally:
        store.close()
