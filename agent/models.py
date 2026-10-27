from __future__ import annotations

import os

# Display order for reasoning-effort levels; unknown levels keep config order after these
EFFORT_ORDER = ["none", "low", "medium", "high", "xhigh"]


def _entries():
    for entry in os.getenv("MODELS", "").split(","):
        name, *fields = (part.strip() for part in entry.split("|"))
        if name:
            yield name, fields


def load_model_options() -> dict[str, int | None]:
    """Parse MODELS into {model name: context window in tokens, or None}.

    Format: comma-separated entries, each "name", "name|context_tokens" or
    "name|context_tokens|efforts", e.g.
    MODELS=OpenAI-GPT-5-Mini|272000|low/medium/high,Mistral Small 4 119B||high
    The API does not report context windows, so they are configured by hand.
    MODEL is always included so the default stays selectable.
    """
    options: dict[str, int | None] = {}
    for name, fields in _entries():
        ctx = fields[0] if fields else ""
        options[name] = int(ctx) if ctx.isdigit() else None
    default = os.getenv("MODEL")
    if default and default not in options:
        options = {default: None, **options}
    return options


def load_model_efforts() -> dict[str, tuple[list[str], str]]:
    """Parse the third MODELS field into {model name: (supported efforts, default)}.

    Each model accepts a different set (e.g. Mistral only "none" and "high",
    Qwen no "high"), and the API rejects any other value, so the set is
    configured per model. The level marked with "*" is the model's own default
    and is preselected; without a mark, "medium" (or the first level) is.
    Models without the field get no effort choice.
    """
    rank = {e: i for i, e in enumerate(EFFORT_ORDER)}
    efforts: dict[str, tuple[list[str], str]] = {}
    for name, fields in _entries():
        raw = [e.strip().lower() for e in (fields[1] if len(fields) > 1 else "").split("/") if e.strip()]
        if not raw:
            continue
        marked = [e.rstrip("*") for e in raw if e.endswith("*")]
        levels = sorted(dict.fromkeys(e.rstrip("*") for e in raw), key=lambda e: rank.get(e, len(rank)))
        default = marked[0] if marked else ("medium" if "medium" in levels else levels[0])
        efforts[name] = (levels, default)
    return efforts
