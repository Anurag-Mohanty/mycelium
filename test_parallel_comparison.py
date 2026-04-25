"""Parallel-path comparison: existing Planner vs new first-node.

Both receive the same charter (from run 16dc20f6). Compare:
- Scopes/departments produced
- Information carried (role definitions vs scope descriptions)
- Budget allocations
- Which output you'd rather hire from
"""

import asyncio
import json
import datetime
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/parallel_comparison")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Load the charter from run 16dc20f6
CHARTER = Path("output/16dc20f6/workspace/charter.md").read_text()


async def run_existing_planner():
    """Run the existing Planner (OPERATIONAL_PLAN_PROMPT) against the charter."""
    print("\n" + "="*60)
    print("PATH 1: Existing Planner (OPERATIONAL_PLAN_PROMPT)")
    print("="*60)

    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import OPERATIONAL_PLAN_PROMPT

    corpus_shape = json.dumps({
        "source": "npm_registry",
        "total_records": 100726,
    }, indent=2)

    prompt = OPERATIONAL_PLAN_PROMPT.format(
        charter=CHARTER,
        corpus_shape=corpus_shape,
        budget=10.0,
        leaf_viable_envelope=0.12,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text
    cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        result = json.loads(raw[start:end]) if start >= 0 and end > start else {}

    print(f"\n  Cost: ${cost:.4f}")

    scopes = result.get("initial_scopes", [])
    budget_alloc = result.get("budget_allocation", {})
    rules = result.get("rules_of_engagement", "")

    print(f"  Scopes: {len(scopes)}")
    print(f"  Rules length: {len(rules)} chars")
    print(f"  Budget allocation:")
    for k, v in budget_alloc.items():
        if k != "reasoning" and isinstance(v, (int, float)):
            print(f"    {k}: ${v:.2f}")

    print(f"\n  Scopes:")
    for i, s in enumerate(scopes, 1):
        level = s.get("scope_level", "?")
        budget_s = s.get("budget", 0)
        desc = s.get("description", "")[:120]
        bar = s.get("success_criteria", "")[:120]
        print(f"\n  {i}. {s.get('name', '?')} [${budget_s:.2f}, {level}]")
        print(f"     Desc: {desc}")
        print(f"     Bar:  {bar}")

    # Save
    with open(OUT_DIR / "planner_output.json", "w") as f:
        json.dump(result, f, indent=2)

    return {
        "path": "existing_planner",
        "scopes": scopes,
        "budget_allocation": budget_alloc,
        "rules_length": len(rules),
        "cost": cost,
    }


async def run_new_first_node():
    """Run the new first-node (NODE_REASONING_PROMPT_V2) against the charter."""
    print("\n" + "="*60)
    print("PATH 2: New First-Node (Role-Authoring Path)")
    print("="*60)

    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import NODE_REASONING_PROMPT_V2

    # Minimal synthetic records so the first node has data context
    records = json.dumps([
        {"id": f"record-{i}", "monthly_interactions": [500000000, 300000, 0, 851000000, 100000][i % 5],
         "controller_count": [1, 3, 0, 1, 2][i % 5],
         "version_count": [114, 45, 1, 228, 4631][i % 5]}
        for i in range(20)
    ], indent=2)

    prompt = NODE_REASONING_PROMPT_V2.format(
        current_date=datetime.date.today().isoformat(),
        role_name="engagement lead",
        role_bar=(
            "Design and staff an organization that produces findings meeting "
            "the charter's standards. Success means: the team you hire covers "
            "the engagement's territory without gaps or overlaps, each hire has "
            "a concrete bar you can judge their output against, and the findings "
            "that come back would satisfy the person who wrote the charter."
        ),
        role_heuristic=(
            "When uncertain whether to investigate directly or hire, ask: "
            "does this scope require distinct kinds of work that a single "
            "pass cannot cover? If yes, hire."
        ),
        scope_description=CHARTER,
        purpose="Design and execute the investigation the charter demands.",
        parent_context="You are the first node. The charter above is your engagement directive.",
        workspace_context=f"## ORGANIZATIONAL CHARTER\n\n{CHARTER}",
        filter_schema="(not applicable — you are designing the organization, not querying data)",
        budget_remaining=7.50,  # after overhead
        parent_pool_remaining=10.0,
        phase_remaining=10.0,
        segment_context="Total engagement budget: $10.00\n",
        current_depth=0,
        max_depth=6,
        leaf_viable_envelope=0.12,
        depth_guidance="Each hire must receive at least $0.12.",
        budget_stage="EARLY — design your team.",
        doc_count=20,
        fetched_data=records,
        force_resolve="",
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        thinking={"type": "enabled", "budget_tokens": 10000},
        messages=[{"role": "user", "content": prompt}],
    )

    thinking = ""
    output_text = ""
    for block in response.content:
        if block.type == "thinking":
            thinking = block.thinking
        elif block.type == "text":
            output_text = block.text

    cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000

    try:
        result = json.loads(output_text)
    except json.JSONDecodeError:
        start = output_text.find("{")
        end = output_text.rfind("}") + 1
        result = json.loads(output_text[start:end]) if start >= 0 and end > start else {}

    formation = result.get("formation_assessment", {})
    hires = result.get("hire_directives", [])

    print(f"\n  Cost: ${cost:.4f}")
    print(f"  Decision: {formation.get('decision', '?')}")
    print(f"  Reasoning: {formation.get('reasoning', '')[:200]}")
    print(f"  Hires: {len(hires)}")

    total_hire_budget = sum(h.get("budget", 0) for h in hires)
    print(f"  Total hire budget: ${total_hire_budget:.2f}")

    print(f"\n  Hires:")
    for i, h in enumerate(hires, 1):
        role = h.get("role", {})
        budget_h = h.get("budget", 0)
        name = role.get("name", "?")
        bar = role.get("success_bar", "")[:150]
        heuristic = role.get("heuristic", "")[:100]
        scope = h.get("scope_description", "")[:120]
        print(f"\n  {i}. {name} [${budget_h:.2f}]")
        print(f"     Bar:       {bar}")
        print(f"     Heuristic: {heuristic}")
        print(f"     Scope:     {scope}")

    # Save
    with open(OUT_DIR / "first_node_output.json", "w") as f:
        json.dump(result, f, indent=2)
    with open(OUT_DIR / "first_node_thinking.md", "w") as f:
        f.write(f"# First-Node Thinking Trace\n\n{thinking}")

    return {
        "path": "new_first_node",
        "hires": hires,
        "formation": formation,
        "total_hire_budget": total_hire_budget,
        "cost": cost,
    }


async def main():
    print("PARALLEL-PATH COMPARISON")
    print("="*60)
    print(f"Charter source: output/16dc20f6/workspace/charter.md")
    print(f"Charter length: {len(CHARTER)} chars, {len(CHARTER.split())} words")

    planner = await run_existing_planner()
    first_node = await run_new_first_node()

    # Side-by-side comparison
    print(f"\n{'='*60}")
    print("SIDE-BY-SIDE COMPARISON")
    print(f"{'='*60}")

    scopes = planner["scopes"]
    hires = first_node["hires"]

    print(f"\n  Existing Planner: {len(scopes)} scopes")
    print(f"  New First-Node:   {len(hires)} hires")

    print(f"\n  --- Existing Planner Scopes ---")
    for s in scopes:
        level = s.get("scope_level", "?")
        print(f"    {s.get('name', '?'):40s} ${s.get('budget', 0):.2f}  [{level}]")
        print(f"      Criteria: {s.get('success_criteria', '?')[:100]}")

    print(f"\n  --- New First-Node Hires ---")
    for h in hires:
        role = h.get("role", {})
        print(f"    {role.get('name', '?'):40s} ${h.get('budget', 0):.2f}")
        print(f"      Bar:       {role.get('success_bar', '?')[:100]}")
        print(f"      Heuristic: {role.get('heuristic', '?')[:80]}")

    # Information density comparison
    print(f"\n  --- Information Density ---")
    planner_info_per_scope = []
    for s in scopes:
        info = len(s.get("description", "")) + len(s.get("success_criteria", ""))
        planner_info_per_scope.append(info)

    first_node_info_per_hire = []
    for h in hires:
        role = h.get("role", {})
        info = (len(role.get("success_bar", "")) + len(role.get("heuristic", "")) +
                len(h.get("scope_description", "")) + len(h.get("purpose", "")))
        first_node_info_per_hire.append(info)

    avg_planner = sum(planner_info_per_scope) / max(1, len(planner_info_per_scope))
    avg_first_node = sum(first_node_info_per_hire) / max(1, len(first_node_info_per_hire))
    print(f"    Planner avg chars/scope:     {avg_planner:.0f}")
    print(f"    First-node avg chars/hire:   {avg_first_node:.0f}")
    print(f"    First-node carries {avg_first_node/max(1,avg_planner):.1f}x more context per unit")

    # Budget comparison
    print(f"\n  --- Budget ---")
    planner_investigation = planner["budget_allocation"].get("investigation_total", 0)
    first_node_total = first_node["total_hire_budget"]
    print(f"    Planner investigation: ${planner_investigation:.2f}")
    print(f"    First-node hires:     ${first_node_total:.2f}")

    total_cost = planner["cost"] + first_node["cost"]
    print(f"\n  Total comparison cost: ${total_cost:.4f}")

    # Save comparison
    with open(OUT_DIR / "comparison.md", "w") as f:
        f.write("# Parallel-Path Comparison\n\n")
        f.write(f"Charter: output/16dc20f6/workspace/charter.md ({len(CHARTER.split())} words)\n\n")

        f.write("## Existing Planner\n\n")
        f.write(f"Scopes: {len(scopes)} | Investigation budget: ${planner_investigation:.2f}\n\n")
        for s in scopes:
            f.write(f"### {s.get('name', '?')} [${s.get('budget', 0):.2f}, {s.get('scope_level', '?')}]\n")
            f.write(f"**Description:** {s.get('description', '?')}\n")
            f.write(f"**Criteria:** {s.get('success_criteria', '?')}\n\n")

        f.write("## New First-Node\n\n")
        f.write(f"Hires: {len(hires)} | Total hire budget: ${first_node_total:.2f}\n\n")
        for h in hires:
            role = h.get("role", {})
            f.write(f"### {role.get('name', '?')} [${h.get('budget', 0):.2f}]\n")
            f.write(f"**Bar:** {role.get('success_bar', '?')}\n")
            f.write(f"**Heuristic:** {role.get('heuristic', '?')}\n")
            f.write(f"**Scope:** {h.get('scope_description', '?')}\n")
            f.write(f"**Purpose:** {h.get('purpose', '?')}\n\n")

        f.write("## Assessment\n\n")
        f.write(f"Info density: first-node carries {avg_first_node/max(1,avg_planner):.1f}x more context per unit\n")

    print(f"\n  Saved to: {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
