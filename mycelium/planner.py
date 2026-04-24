"""Planner — Phase F operational plan from charter.

Reads the charter and produces: rules of engagement, initial scopes with
scope levels, and budget allocation. Maps scopes to segment format for
backward compatibility with the orchestrator.
"""

import json
import anthropic
from .prompts import OPERATIONAL_PLAN_PROMPT


async def create_plan(genesis_result: dict, total_budget: float) -> dict:
    """Create an operational plan from the charter.

    Args:
        genesis_result: Output from genesis (must contain 'charter')
        total_budget: Total budget in dollars

    Returns:
        Plan dict with segments (mapped from scopes), rules, budget allocation
    """
    from .orchestrator import LEAF_VIABLE_ENVELOPE

    charter = genesis_result.get("charter", "")
    if not charter:
        raise ValueError("Genesis result must contain 'charter' for Phase F planner")

    # Build corpus shape from genesis metadata
    corpus_shape = json.dumps({
        "source": "catalog",
        "charter_word_count": len(charter.split()),
    }, indent=2)

    prompt = OPERATIONAL_PLAN_PROMPT.format(
        charter=charter,
        corpus_shape=corpus_shape,
        budget=total_budget,
        leaf_viable_envelope=LEAF_VIABLE_ENVELOPE,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    try:
        plan = _parse_json(raw_text)
    except (json.JSONDecodeError, ValueError):
        # Fallback: single-scope plan
        plan = {
            "rules_of_engagement": "Investigate thoroughly. Cite specific evidence.",
            "initial_scopes": [{
                "name": "full_investigation",
                "scope_level": "ambiguous",
                "scope_level_reasoning": "Fallback — planner output could not be parsed",
                "description": "Investigate the full corpus",
                "charter_rationale": "Fallback plan",
                "budget": total_budget * 0.50,
                "success_criteria": "Named findings with evidence",
            }],
            "budget_allocation": {
                "investigation_total": total_budget * 0.50,
                "synthesis": total_budget * 0.10,
                "validation": total_budget * 0.10,
                "impact_analysis": total_budget * 0.10,
                "report_generation": total_budget * 0.10,
                "overhead": total_budget * 0.10,
                "reasoning": "Fallback — planner output could not be parsed",
            },
            "depth_policy": "Budget governs depth",
        }

    # Map initial_scopes to segments format for backward compat
    scopes = plan.get("initial_scopes", [])
    plan["segments"] = [
        {
            "name": scope.get("name", f"scope_{i}"),
            "scope_description": scope.get("description", ""),
            "filters": {},  # Phase F scopes don't use keyword filters
            "estimated_complexity": "high" if scope.get("scope_level") == "manager" else "medium",
            "sub_budget": scope.get("budget", 0),
            "estimated_nodes": max(1, int(scope.get("budget", 0) / 0.15)),
            "reasoning": scope.get("charter_rationale", ""),
            # Phase F fields
            "scope_level": scope.get("scope_level", "ambiguous"),
            "scope_level_reasoning": scope.get("scope_level_reasoning", ""),
            "success_criteria": scope.get("success_criteria", ""),
        }
        for i, scope in enumerate(scopes, 1)
    ]

    # Backward compat fields
    budget_alloc = plan.get("budget_allocation", {})
    plan["exploration_budget"] = budget_alloc.get("investigation_total", total_budget * 0.50)
    plan["estimated_total_nodes"] = sum(s.get("estimated_nodes", 1) for s in plan["segments"])
    plan["deep_dive_reserve"] = 0  # Phase F doesn't have a separate deep-dive reserve
    plan["deep_dive_strategy"] = "Deep investigation happens within manager subtrees"

    plan["token_usage"] = usage
    plan["cost"] = cost
    return plan


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            return json.loads(text[start:end].strip())
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError("Could not extract JSON")
