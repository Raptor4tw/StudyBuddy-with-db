from agent.models import load_model_options


def test_parses_names_and_context_windows(monkeypatch):
    monkeypatch.setenv("MODEL", "A")
    monkeypatch.setenv("MODELS", "A|400000, B C ,D|n/a")
    assert load_model_options() == {"A": 400000, "B C": None, "D": None}


def test_default_model_added_first_when_missing(monkeypatch):
    monkeypatch.setenv("MODEL", "Default")
    monkeypatch.setenv("MODELS", "X|1000")
    assert list(load_model_options()) == ["Default", "X"]


def test_empty_config(monkeypatch):
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.delenv("MODELS", raising=False)
    assert load_model_options() == {}
