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


def test_efforts_parsed_per_model_and_sorted(monkeypatch):
    from agent.models import load_model_efforts
    monkeypatch.setenv("MODELS", "A|272000|high/low/medium, B||high/none*, C|131072, D|1000|xhigh*/low/medium, E||low/high")
    assert load_model_efforts() == {
        "A": (["low", "medium", "high"], "medium"),  # no mark: medium
        "B": (["none", "high"], "none"),
        "D": (["low", "medium", "xhigh"], "xhigh"),
        "E": (["low", "high"], "low"),  # no mark, no medium: first level
    }


def test_effort_field_does_not_break_context_windows(monkeypatch):
    monkeypatch.setenv("MODEL", "A")
    monkeypatch.setenv("MODELS", "A|272000|low/high,B||high")
    assert load_model_options() == {"A": 272000, "B": None}
