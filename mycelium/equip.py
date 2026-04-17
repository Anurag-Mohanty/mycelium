"""EQUIP step — dynamic tool discovery via MCP registry.

Before a node can explore, it needs tools to access the data.
The EQUIP step asks the LLM what tools are needed, searches for
available MCP servers, and lets the LLM select which to connect.

If no MCP server matches and a built-in connector exists
(federal_register, npm), the built-in is used as a fallback.

Results are cached per scope type — if three nodes all need
the same data access, they reuse the same connection.
"""

import json
import anthropic
from .prompts import EQUIP_PROMPT


# Cache: scope_keyword -> selected tool connections
_equip_cache: dict[str, dict] = {}


async def equip_node(scope_description: str,
                     available_mcp_servers: list[dict] = None,
                     builtin_sources: list[str] = None) -> dict:
    """Discover and select tools needed for a scope.

    Args:
        scope_description: What this node needs to explore
        available_mcp_servers: List of MCP server dicts with name, description, capabilities
        builtin_sources: Names of built-in connectors available as fallback

    Returns:
        dict with selected_tools, fallback_source, and reasoning
    """
    # Check cache
    cache_key = _cache_key(scope_description)
    if cache_key in _equip_cache:
        return _equip_cache[cache_key]

    available_mcp_servers = available_mcp_servers or []
    builtin_sources = builtin_sources or ["federal_register", "npm"]

    # If no MCP servers configured, use fallback immediately
    if not available_mcp_servers:
        result = {
            "reasoning": "No MCP servers configured — using built-in connector",
            "selected_tools": [],
            "fallback_source": _match_builtin(scope_description, builtin_sources),
            "missing_tools": "",
        }
        _equip_cache[cache_key] = result
        return result

    # Format MCP servers for the prompt
    servers_text = "\n".join(
        f"- {s.get('name', '?')}: {s.get('description', '')} "
        f"[capabilities: {', '.join(s.get('capabilities', []))}]"
        for s in available_mcp_servers
    ) or "(no MCP servers available)"

    prompt = EQUIP_PROMPT.format(
        scope_description=scope_description,
        available_servers=servers_text,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    try:
        result = _parse_json(raw_text)
    except (json.JSONDecodeError, ValueError):
        result = {
            "reasoning": "Failed to parse EQUIP response",
            "selected_tools": [],
            "missing_tools": scope_description,
        }

    # If no tools selected, try builtin fallback
    if not result.get("selected_tools"):
        result["fallback_source"] = _match_builtin(scope_description, builtin_sources)

    result["cost"] = cost
    result["token_usage"] = usage

    _equip_cache[cache_key] = result
    return result


def _cache_key(scope: str) -> str:
    """Generate a cache key from scope description."""
    words = scope.lower().split()[:5]
    return "_".join(sorted(set(words)))


def _match_builtin(scope: str, builtins: list[str]) -> str:
    """Match a scope to a built-in data source."""
    scope_lower = scope.lower()
    for name in builtins:
        if name.replace("_", " ") in scope_lower or name in scope_lower:
            return name
    # Default heuristics
    if any(w in scope_lower for w in ["npm", "package", "node", "javascript", "react", "express"]):
        return "npm"
    if any(w in scope_lower for w in ["federal", "regulation", "agency", "cfr"]):
        return "federal_register"
    return builtins[0] if builtins else ""


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        s = text.find("```json") + 7
        e = text.find("```", s)
        if e > s:
            return json.loads(text[s:e].strip())
    s = text.find("{")
    e = text.rfind("}") + 1
    if s >= 0 and e > s:
        return json.loads(text[s:e])
    raise ValueError("Could not extract JSON")
