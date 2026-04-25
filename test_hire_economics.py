"""Offline validation for hire economics fix.

Five scenarios testing the economics calculus at formation-time and Turn 2:
1. Formation: abundant budget, complex scope → hire (warranted)
2. Formation: abundant budget, narrow scope → investigate (hiring wasteful)
3. Formation: thin budget, narrow scope → investigate (both reasons)
4. Turn 2: MET children, threads need different cognition → continue
5. Turn 2: MET children, threads are more-of-the-same → resolve/return surplus
"""

import asyncio
import json
import datetime
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/hire_economics_traces")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def call_v2_prompt(role_name, role_bar, role_heuristic, scope, purpose,
                          parent_context, workspace_context, records, budget,
                          force_resolve=""):
    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import NODE_REASONING_PROMPT_V2

    prompt = NODE_REASONING_PROMPT_V2.format(
        current_date=datetime.date.today().isoformat(),
        role_name=role_name, role_bar=role_bar, role_heuristic=role_heuristic,
        scope_description=scope, purpose=purpose, parent_context=parent_context,
        workspace_context=workspace_context,
        filter_schema="(not applicable)",
        budget_remaining=budget, parent_pool_remaining=budget * 2,
        phase_remaining=budget * 3, segment_context="",
        current_depth=0, max_depth=6, leaf_viable_envelope=0.12,
        depth_guidance=f"Each hire must receive at least $0.12.",
        budget_stage="EARLY" if budget > 0.50 else "LATE",
        doc_count=len(json.loads(records)) if records != "(no data)" else 0,
        fetched_data=records, force_resolve=force_resolve,
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

    return {"thinking": thinking, "result": result, "cost": cost}


async def call_turn2(role_name, role_bar, scope, hire_reports, budget):
    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import MANAGER_TURN2_PROMPT_V2

    prompt = MANAGER_TURN2_PROMPT_V2.format(
        budget_remaining=budget, role_name=role_name, role_bar=role_bar,
        scope_description=scope, workspace_context="",
        hire_reports=hire_reports,
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

    return {"thinking": thinking, "result": result, "cost": cost}


# Synthetic records for formation scenarios
RECORDS_COMPLEX = json.dumps([
    {"id": f"record-{i}", "value_a": i * 100 + 50, "value_b": 1000 - i * 30,
     "entity": f"entity-{chr(65+i)}", "category": ["alpha", "beta", "gamma", "delta"][i % 4],
     "status": "active", "created": f"202{i % 6}-0{(i%9)+1}-15",
     "relationships": [f"entity-{chr(65+(i+3)%10)}", f"entity-{chr(65+(i+7)%10)}"]}
    for i in range(30)
], indent=2)

RECORDS_NARROW = json.dumps([
    {"id": "record-A", "value_a": 500, "controller": "entity-X", "version_count": 45},
    {"id": "record-B", "value_a": 300, "controller": "entity-X", "version_count": 12},
    {"id": "record-C", "value_a": 100, "controller": "entity-Y", "version_count": 3},
], indent=2)


async def test_formation_scenarios():
    """Formation-time: scenarios 1-3."""
    print("\n" + "="*60)
    print("FORMATION-TIME SCENARIOS")
    print("="*60)

    scenarios = [
        {
            "name": "1_abundant_complex_hire",
            "budget": 2.00,
            "scope": (
                "Investigate structural relationships across 30 records spanning "
                "4 categories, 10 entities with cross-entity relationships, "
                "covering 6 years of activity. Requires network analysis, "
                "temporal pattern detection, and entity concentration assessment."
            ),
            "records": RECORDS_COMPLEX,
            "expected": "hire",
            "why": "Scope requires genuinely distinct cognition (network, temporal, concentration) — hiring warranted",
        },
        {
            "name": "2_abundant_narrow_investigate",
            "budget": 2.00,
            "scope": (
                "Analyze the relationship between 3 records controlled by 2 entities. "
                "Determine whether the version count disparity indicates different "
                "maintenance patterns."
            ),
            "records": RECORDS_NARROW,
            "expected": "investigate",
            "why": "Scope is narrow, 3 records, one analysis. Hiring overhead exceeds the work itself.",
        },
        {
            "name": "3_thin_narrow_investigate",
            "budget": 0.20,
            "scope": (
                "Analyze the relationship between 3 records controlled by 2 entities."
            ),
            "records": RECORDS_NARROW,
            "expected": "investigate",
            "why": "Both thin budget and narrow scope — investigating is the only sensible option",
        },
    ]

    results = []
    for s in scenarios:
        print(f"\n  {'─'*50}")
        print(f"  {s['name']}: {s['why'][:70]}")
        print(f"  Budget: ${s['budget']:.2f} | Expected: {s['expected']}")

        r = await call_v2_prompt(
            role_name="field analyst",
            role_bar="Produce specific findings with named entities and exact figures.",
            role_heuristic="When uncertain, favor doing the work directly over delegating.",
            scope=s["scope"], purpose="Investigate as your bar demands.",
            parent_context="Assigned by program office.", workspace_context="",
            records=s["records"], budget=s["budget"],
        )

        formation = r["result"].get("formation_assessment", {})
        decision = formation.get("decision", "?")
        reasoning = formation.get("reasoning", "")[:250]
        hires = r["result"].get("hire_directives", [])

        # Check for economics reasoning
        thinking = r["thinking"].lower()
        has_economics = any(p in thinking for p in [
            "overhead", "cost of hiring", "cost of authoring", "cost of setting up",
            "briefing", "reviewing", "wasteful", "fragmentation", "same kind",
            "cheaper", "directly", "myself",
        ])

        correct = decision == s["expected"]
        print(f"  Decision: {decision} {'✓' if correct else '✗'}")
        print(f"  Economics reasoning: {has_economics}")
        print(f"  Reasoning: {reasoning}")
        if hires:
            print(f"  Hires: {len(hires)}")

        results.append({
            "name": s["name"], "expected": s["expected"], "actual": decision,
            "correct": correct, "economics": has_economics, "cost": r["cost"],
        })

    return results


async def test_turn2_scenarios():
    """Turn 2: scenarios 4-5."""
    print("\n" + "="*60)
    print("TURN 2 CONTINUATION SCENARIOS")
    print("="*60)

    scenarios = [
        {
            "name": "4_different_cognition_continue",
            "budget": 0.80,
            "hire_reports": (
                "--- HIRE: structural analyst ---\n"
                "AUTHORED BAR: Map dependency relationships with named entities.\n"
                "AUTHORED HEURISTIC: Favor depth on fewer relationships.\n"
                "SCOPE: Analyze structural patterns.\n"
                "OBSERVATIONS (4):\n"
                "  Obs 1 [data_originated_novel]: entity-D appears across 7 resources "
                "totaling 2.1B interactions but is listed as controller on only 2.\n"
                "  Obs 2 [data_originated_novel]: resource-M is transitive dependency "
                "of 89% of top-100, never listed directly.\n"
                "  Obs 3 [data_originated_novel]: 3 hidden cross-entity ownership chains found.\n"
                "  Obs 4 [data_originated_novel]: resource-K → resource-L → resource-N chain creates unmapped risk.\n"
                "SELF-EVALUATION: bar_met=True, quality=high\n"
                "  Follow-up threads flagged:\n"
                "    - Quantify blast radius of resource-M compromise using network "
                "simulation (requires different analytical approach than mapping)\n"
                "    - Investigate temporal evolution of the hidden ownership chains "
                "(requires historical analysis, different from current snapshot mapping)\n"
            ),
            "expected": "CONTINUE or RESOLVE",
            "why": "Threads need genuinely different cognition (simulation, historical analysis vs structural mapping)",
        },
        {
            "name": "5_more_of_same_resolve",
            "budget": 0.80,
            "hire_reports": (
                "--- HIRE: structural analyst ---\n"
                "AUTHORED BAR: Map dependency relationships with named entities.\n"
                "AUTHORED HEURISTIC: Favor depth on fewer relationships.\n"
                "SCOPE: Analyze structural patterns.\n"
                "OBSERVATIONS (4):\n"
                "  Obs 1 [data_originated_novel]: entity-D cross-resource control.\n"
                "  Obs 2 [data_originated_novel]: resource-M hidden transitive dependency.\n"
                "  Obs 3 [data_originated_novel]: resource-K chain creates unmapped risk.\n"
                "  Obs 4 [data_originated_novel]: entity-E controls 3 resources.\n"
                "SELF-EVALUATION: bar_met=True, quality=high\n"
                "  Follow-up threads flagged:\n"
                "    - Map more dependency relationships in the remaining records\n"
                "    - Check additional entities for cross-resource control patterns\n"
            ),
            "expected": "RESOLVE",
            "why": "Threads are more-of-the-same (more mapping, more checking) — not different cognition",
        },
    ]

    results = []
    for s in scenarios:
        print(f"\n  {'─'*50}")
        print(f"  {s['name']}: {s['why'][:70]}")
        print(f"  Budget: ${s['budget']:.2f} | Expected: {s['expected']}")

        r = await call_turn2(
            role_name="department lead",
            role_bar="Produce findings revealing hidden structural relationships.",
            scope="Investigate structural relationships.",
            hire_reports=s["hire_reports"],
            budget=s["budget"],
        )

        continuation = r["result"].get("continuation_decision", {})
        action = continuation.get("action", "?")
        reasoning = continuation.get("reasoning", "")[:250]
        cont_directives = continuation.get("continuation_directives", [])

        thinking = r["thinking"].lower()
        has_economics = any(p in thinking for p in [
            "overhead", "cost of", "same kind", "more of the same",
            "downstream", "surplus", "wasteful", "fragmentation",
            "different cognition", "genuinely different",
        ])

        expected_actions = s["expected"].split(" or ")
        correct = action in expected_actions
        print(f"  Action: {action} {'✓' if correct else '✗'}")
        print(f"  Economics reasoning: {has_economics}")
        print(f"  Reasoning: {reasoning}")
        if cont_directives:
            for cd in cont_directives[:2]:
                role = cd.get("role", {})
                print(f"    Continuation: {role.get('name', '?')} (${cd.get('budget', 0):.2f})")

        results.append({
            "name": s["name"], "expected": s["expected"], "actual": action,
            "correct": correct, "economics": has_economics, "cost": r["cost"],
        })

    return results


async def main():
    print("HIRE ECONOMICS — Offline Validation")
    print("="*60)
    print("First pass. No iteration.")

    formation_results = await test_formation_scenarios()
    turn2_results = await test_turn2_scenarios()

    all_results = formation_results + turn2_results

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    correct = sum(1 for r in all_results if r["correct"])
    economics = sum(1 for r in all_results if r["economics"])
    for r in all_results:
        s = "✓" if r["correct"] else "✗"
        e = "econ" if r["economics"] else "no-econ"
        print(f"  {s} {r['name']:40s} | expected={r['expected']:25s} | got={r['actual']:12s} | {e}")

    total_cost = sum(r["cost"] for r in all_results)
    print(f"\n  Score: {correct}/{len(all_results)}")
    print(f"  Economics reasoning present: {economics}/{len(all_results)}")
    print(f"  Cost: ${total_cost:.4f}")

    # Save
    with open(OUT_DIR / "results.md", "w") as f:
        f.write("# Hire Economics — Offline Validation\n\n")
        f.write(f"Score: {correct}/{len(all_results)}\n")
        f.write(f"Economics reasoning: {economics}/{len(all_results)}\n")
        f.write(f"Cost: ${total_cost:.4f}\n\n")
        for r in all_results:
            f.write(f"## {r['name']}\n")
            f.write(f"Expected: {r['expected']} | Got: {r['actual']} | "
                    f"{'PASS' if r['correct'] else 'FAIL'} | "
                    f"Economics: {r['economics']}\n\n")


if __name__ == "__main__":
    asyncio.run(main())
