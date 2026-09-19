from agent import __main__ as agent_main


def test_conductor_uses_first_available_allocated_port(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("CONDUCTOR_PORT", "55050")
    monkeypatch.setattr(
        agent_main,
        "is_port_available",
        lambda port: port == 55052,
    )

    assert agent_main.resolve_port() == 55052


def test_explicit_port_is_not_changed(monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("CONDUCTOR_PORT", "55050")

    assert agent_main.resolve_port() == 9000
