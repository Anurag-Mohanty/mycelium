"""Prompt version dispatcher.

Re-exports all prompts from either prompts_v1 or prompts_v2 based on
the active version. All existing imports (from .prompts import X) continue
to work — they get whichever version is currently active.

Set the version before any imports happen:
    from mycelium import prompts
    prompts.set_version("v2")

Default is v1 (identical to original prompts.py).
"""

_version = "v1"


def set_version(v: str):
    """Set the active prompt version ('v1' or 'v2'). Must be called before prompts are used."""
    global _version
    if v not in ("v1", "v2"):
        raise ValueError(f"Unknown prompt version: {v}. Use 'v1' or 'v2'.")
    _version = v
    _reload()


def get_version() -> str:
    return _version


def _reload():
    """Re-export all prompt constants from the active version module."""
    import importlib
    if _version == "v2":
        mod = importlib.import_module("mycelium.prompts_v2")
    else:
        mod = importlib.import_module("mycelium.prompts_v1")

    # Copy all uppercase constants (prompt strings) into this module's namespace
    g = globals()
    for name in dir(mod):
        if name.isupper() or name.endswith("_PROMPT") or name.endswith("_OVERRIDE"):
            g[name] = getattr(mod, name)


# Initialize with default version
_reload()
