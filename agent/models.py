from __future__ import annotations

import os


def load_model_options() -> dict[str, int | None]:
    """Parse MODELS into {model name: context window in tokens, or None}.

    Format: comma-separated entries, each "name" or "name|context_tokens", e.g.
    MODELS=OpenAI-GPT-5-Mini|400000,Mistral Small 4 119B
    The API does not report context windows, so they are configured by hand.
    MODEL is always included so the default stays selectable.
    """
    options: dict[str, int | None] = {}
    for entry in os.getenv("MODELS", "").split(","):
        name, _, ctx = entry.partition("|")
        name = name.strip()
        if not name:
            continue
        ctx = ctx.strip()
        options[name] = int(ctx) if ctx.isdigit() else None
    default = os.getenv("MODEL")
    if default and default not in options:
        options = {default: None, **options}
    return options
