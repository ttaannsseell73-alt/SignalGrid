from signalgrid.state.store import StateStore


def test_sqlite_state_roundtrip(tmp_path):
    st = StateStore(tmp_path / "state.db")
    st.upsert_position("SOLUSDT", "LONG", 300.0, 1)
    assert st.list_positions() == [("SOLUSDT", "LONG", 300.0, 1)]
    st.delete_position("SOLUSDT")
    assert st.list_positions() == []
