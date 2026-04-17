"""Planner — creates a budget-aware exploration strategy.

Single LLM call after Genesis, before exploration begins. Takes the corpus
shape and budget, returns a plan with segment allocations and depth targets.
The plan is ADVISORY — segment budgets are soft targets, not hard caps.
"""

import json
import anthropic
from .prompts import PLANNER_PROMPT


async def create_plan(genesis_result: dict, total_budget: float) -> dict:
    """Create an exploration plan based on corpus shape and budget.

    Args:
        genesis_result: Output from genesis (corpus_summary, lenses, structure)
        total_budget: Total budget in dollars

    Returns:
        Plan dict with segments, sub-budgets, depth targets, deep-dive strategy
    """
    exploration_budget = total_budget * 0.50
    estimated_nodes = int(exploration_budget / 0.04)

    prompt = PLANNER_PROMPT.format(
        genesis_output=json.dumps({
            "corpus_summary": genesis_result.get("corpus_summary", ""),
            "lenses": genesis_result.get("lenses", []),
            "suggested_entry_points": genesis_result.get("suggested_entry_points", []),
            "natural_structure": genesis_result.get("natural_structure", {}),
        }, indent=2),
        budget=total_budget,
        exploration_budget=exploration_budget,
        synthesis_budget=total_budget * 0.18,
        deep_dive_budget=total_budget * 0.08,
        validation_budget=total_budget * 0.07,
        overhead_budget=total_budget * 0.07,
        estimated_nodes=estimated_nodes,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2500,
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
        # Fallback: single-segment plan
        plan = {
            "exploration_budget": exploration_budget,
            "estimated_total_nodes": estimated_nodes,
            "segments": [{
                "name": "full_corpus",
                "scope_description": genesis_result.get("corpus_summary", "Full exploration"),
                "estimated_complexity": "high",
                "sub_budget": exploration_budget,
                "estimated_nodes": estimated_nodes,
                "target_depth": 3,
                "reasoning": "Fallback plan — planner output could not be parsed",
            }],
            "deep_dive_reserve": total_budget * 0.10,
            "deep_dive_strategy": "Investigate the most surprising findings from initial sweep",
        }

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
