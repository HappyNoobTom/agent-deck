

def test_streamdock_logical_secondary_keys_map_to_first_layout_slots() -> None:
    from types import SimpleNamespace

    from agent_deck.input.interactions import _streamdock_key_value_to_layout_index

    assert _streamdock_key_value_to_layout_index(1) == 0
    assert _streamdock_key_value_to_layout_index(10) == 9
    assert _streamdock_key_value_to_layout_index(11) == 0
    assert _streamdock_key_value_to_layout_index(14) == 3
    assert _streamdock_key_value_to_layout_index(15) is None
