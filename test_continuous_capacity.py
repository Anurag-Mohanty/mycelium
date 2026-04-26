"""Offline validation for continuous capacity-vs-scope reasoning.

Three scenarios testing the Turn 2 arithmetic with observable cost data:
1. Abundant budget, good threads, should CONTINUE
2. Tiny budget remaining, no threads, should RESOLVE
3. Mid-tree manager, limited budget, one thread, should fund it
"""

import asyncio
import json
import datetime
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/continuous_capacity_traces")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def call_turn2(role_name, role_bar, scope, hire_reports, budget, cost_context):
    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import MANAGER_TURN2_PROMPT_V2

    prompt = MANAGER_TURN2_PROMPT_V2.format(
        budget_remaining=budget, role_name=role_name, role_bar=role_bar,
        scope_description=scope, workspace_context="",
        hire_reports=hire_reports, cost_context=cost_context,
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


SCENARIOS = [
    {
        "name": "1_abundant_budget_good_threads",
        "description": "Engagement lead, $8 remaining, hires averaged $0.15, worthwhile threads emerged",
        "budget": 8.00,
        "cost_context": (
            "OBSERVABLE COST DATA:\n"
            "  Your formation cost: $0.170\n"
            "  Hires completed: 4\n"
            "  Average cost per hire: $0.150\n"
            "  Total spent on hires: $0.600\n"
            "  Downstream phases estimate: $0.50 (synthesis + validation + deep-dive + impact + report)\n"
            "  Budget after downstream: $7.50 available for continuation\n"
        ),
        "hire_reports": (
            "--- HIRE: structural analyst ---\n"
            "AUTHORED BAR: Map dependency relationships with named entities and exact figures.\n"
            "ACTUAL COST: $0.150 (of $0.500 allocated)\n"
            "OBSERVATIONS (5):\n"
            "  Obs 1 [data_originated_novel]: entity-D controls 7 resources totaling 2.1B interactions.\n"
            "  Obs 2 [data_originated_novel]: resource-M is transitive dependency of 89% of top-100.\n"
            "  Obs 3 [data_originated_novel]: 3 hidden cross-entity ownership chains found.\n"
            "  Obs 4 [data_originated_novel]: resource-K → L → N chain creates unmapped risk.\n"
            "  Obs 5 [data_originated_novel]: entity-C's 228 versions suggest automated control.\n"
            "SELF-EVALUATION: bar_met=True, quality=high\n"
            "  Follow-up threads flagged:\n"
            "    - Quantify blast radius of resource-M compromise using impact simulation\n"
            "    - Investigate whether entity-D's cross-resource control is organizational or individual\n\n"
            "--- HIRE: temporal analyst ---\n"
            "AUTHORED BAR: Identify temporal patterns revealing coordination or hidden dynamics.\n"
            "ACTUAL COST: $0.150 (of $0.500 allocated)\n"
            "OBSERVATIONS (4):\n"
            "  Obs 1 [data_originated_novel]: 47 resources abandoned in 2016-2017 correlated with framework migration.\n"
            "  Obs 2 [data_originated_novel]: 8 resources share identical modification timestamps.\n"
            "  Obs 3 [data_originated_novel]: resource-G utility exceeds parent by 2.18x.\n"
            "  Obs 4 [data_originated_confirmatory]: Seasonal patterns in creation dates.\n"
            "SELF-EVALUATION: bar_met=True, quality=high\n"
            "  Follow-up threads flagged:\n"
            "    - Trace the 2016-2017 abandonment wave to specific framework events\n"
        ),
        "expected": "CONTINUE",
        "why": "Arithmetic: $7.50 after downstream, $0.15/hire = 50 hires possible, 3 threads need different cognition",
    },
    {
        "name": "2_tiny_budget_no_threads",
        "description": "Engagement lead, $0.30 remaining, no worthwhile threads",
        "budget": 0.30,
        "cost_context": (
            "OBSERVABLE COST DATA:\n"
            "  Your formation cost: $0.170\n"
            "  Hires completed: 4\n"
            "  Average cost per hire: $0.200\n"
            "  Total spent on hires: $0.800\n"
            "  Downstream phases estimate: $0.50 (synthesis + validation + deep-dive + impact + report)\n"
            "  Budget after downstream: $0.00 available for continuation\n"
        ),
        "hire_reports": (
            "--- HIRE: analyst ---\n"
            "AUTHORED BAR: Produce findings with evidence.\n"
            "ACTUAL COST: $0.200 (of $0.250 allocated)\n"
            "OBSERVATIONS (3):\n"
            "  Obs 1 [data_originated_novel]: finding A.\n"
            "  Obs 2 [data_originated_novel]: finding B.\n"
            "  Obs 3 [data_originated_confirmatory]: known pattern.\n"
            "SELF-EVALUATION: bar_met=True, quality=medium\n"
        ),
        "expected": "RESOLVE",
        "why": "Arithmetic: $0.00 after downstream, cannot fund any continuation",
    },
    {
        "name": "3_midtree_one_thread",
        "description": "Mid-tree manager, $0.20 remaining, one worthwhile thread",
        "budget": 0.20,
        "cost_context": (
            "OBSERVABLE COST DATA:\n"
            "  Your formation cost: $0.050\n"
            "  Hires completed: 2\n"
            "  Average cost per hire: $0.060\n"
            "  Total spent on hires: $0.120\n"
            "  Downstream phases estimate: $0.05 (already running at higher level)\n"
            "  Budget after downstream: $0.15 available for continuation\n"
        ),
        "hire_reports": (
            "--- HIRE: detail analyst ---\n"
            "AUTHORED BAR: Trace specific entity relationships with exact figures.\n"
            "ACTUAL COST: $0.060 (of $0.100 allocated)\n"
            "OBSERVATIONS (3):\n"
            "  Obs 1 [data_originated_novel]: entity-X controls hidden chain.\n"
            "  Obs 2 [data_originated_novel]: 5 resources share controller.\n"
            "  Obs 3 [data_originated_novel]: blast radius estimate: 300M interactions.\n"
            "SELF-EVALUATION: bar_met=True, quality=high\n"
            "  Follow-up threads flagged:\n"
            "    - Verify blast radius through reverse dependency analysis (different analytical approach)\n"
        ),
        "expected": "CONTINUE",
        "why": "Arithmetic: $0.15 after downstream, $0.06/hire = 2 hires possible, 1 thread needs different cognition",
    },
]


async def main():
    print("CONTINUOUS CAPACITY REASONING — Offline Validation")
    print("="*60)
    print("First pass. No iteration.")

    results = []
    for s in SCENARIOS:
        print(f"\n{'─'*60}")
        print(f"{s['name']}: {s['description']}")
        print(f"Expected: {s['expected']} — {s['why']}")
        print(f"{'─'*60}")

        r = await call_turn2(
            role_name="department lead",
            role_bar="Produce findings revealing hidden structural relationships.",
            scope="Investigate structural relationships.",
            hire_reports=s["hire_reports"],
            budget=s["budget"],
            cost_context=s["cost_context"],
        )

        cont = r["result"].get("continuation_decision", {})
        action = cont.get("action", "?")
        arith = cont.get("arithmetic", {})
        reasoning = cont.get("reasoning", "")[:200]

        correct = action == s["expected"]
        has_arithmetic = bool(arith.get("available_for_continuation", None) is not None)
        print(f"\n  Action: {action} {'✓' if correct else '✗'}")
        print(f"  Has arithmetic: {has_arithmetic}")
        if arith:
            print(f"  Arithmetic: remaining=${arith.get('remaining_budget', '?')}, "
                  f"downstream=${arith.get('downstream_reservation', '?')}, "
                  f"available=${arith.get('available_for_continuation', '?')}, "
                  f"affordable={arith.get('affordable_hires', '?')}, "
                  f"threads={arith.get('threads_needing_different_cognition', '?')}")
        print(f"  Reasoning: {reasoning}")

        cont_directives = cont.get("continuation_directives", [])
        if cont_directives:
            for cd in cont_directives[:2]:
                role = cd.get("role", {})
                print(f"  Continuation: {role.get('name', '?')} (${cd.get('budget', 0):.2f})")

        results.append({
            "name": s["name"], "expected": s["expected"], "actual": action,
            "correct": correct, "has_arithmetic": has_arithmetic, "cost": r["cost"],
        })

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    correct_count = sum(1 for r in results if r["correct"])
    arith_count = sum(1 for r in results if r["has_arithmetic"])
    for r in results:
        s = "✓" if r["correct"] else "✗"
        a = "arith" if r["has_arithmetic"] else "no-arith"
        print(f"  {s} {r['name']:40s} | expected={r['expected']:10s} | got={r['actual']:10s} | {a}")

    total_cost = sum(r["cost"] for r in results)
    print(f"\n  Score: {correct_count}/{len(results)} | Arithmetic: {arith_count}/{len(results)}")
    print(f"  Cost: ${total_cost:.4f}")

    with open(OUT_DIR / "results.md", "w") as f:
        f.write(f"# Continuous Capacity Reasoning\n\nScore: {correct_count}/{len(results)}\n")
        for r in results:
            f.write(f"\n## {r['name']}\nExpected: {r['expected']} | Got: {r['actual']} | "
                    f"{'PASS' if r['correct'] else 'FAIL'}\n")


if __name__ == "__main__":
    asyncio.run(main())
