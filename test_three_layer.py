"""Offline validation for three-layer fix: mission + criterion + commitment.

Five scenarios:
1. Worker with mission, $7 remaining, 10 threads — should CONTINUE with mission reasoning
2. Worker where threads are genuinely repetitive — should RESOLVE honestly
3. Manager evaluating UNDERFIRED hire (met bar, $0.40 unused, 3 threads)
4. Manager evaluating COMMITTED hire (used budget well, genuinely complete)
5. Manager evaluating OVERFIRED hire (6 continuations on diminishing returns)
"""

import asyncio
import json
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/three_layer_traces")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def call_turn2(role_name, role_mission, role_bar, scope, hire_reports,
                     budget, cost_context):
    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import MANAGER_TURN2_PROMPT_V2

    prompt = MANAGER_TURN2_PROMPT_V2.format(
        budget_remaining=budget, role_name=role_name,
        role_mission=role_mission, role_bar=role_bar,
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


MISSION = (
    "Produce the most insightful investigation this budget can yield. "
    "Uncover what nobody else has noticed — hidden structural relationships, "
    "unexpected failure modes. Push every line of inquiry as far as the budget allows."
)
BAR = (
    "Design and staff an organization that produces findings meeting the "
    "charter's standards. Each hire covers territory without gaps or overlaps."
)

SCENARIOS = [
    {
        "name": "1_mission_abundant_threads",
        "description": "Mission framing + $7 remaining + 10 worthwhile threads",
        "budget": 8.00,
        "cost_context": (
            "OBSERVABLE COST DATA:\n"
            "  Your formation cost: $0.170\n"
            "  Hires completed: 4\n"
            "  Average cost per hire: $0.150\n"
            "  Total spent on hires: $0.600\n"
            "  Downstream phases estimate: $0.50\n"
            "  Budget after downstream: $7.50 available for continuation\n"
        ),
        "hire_reports": (
            "--- HIRE: structural analyst ---\n"
            "AUTHORED MISSION: Map every hidden dependency chain, quantify blast radii, "
            "and identify the most dangerous single points of failure.\n"
            "AUTHORED BAR: Map dependency relationships with named entities and exact figures.\n"
            "AUTHORED HEURISTIC: Favor depth over breadth.\n"
            "SCOPE: Analyze structural dependencies across 100K records.\n"
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
            "    - Investigate whether entity-D's cross-resource control is organizational or individual\n"
            "    - Trace resource-K → L → N chain to identify downstream consumers\n"
            "    - Map entity-C's automation pattern across other high-version resources\n"
            "    - Cross-reference hidden ownership chains with maintainer identity data\n\n"
            "--- HIRE: temporal analyst ---\n"
            "AUTHORED MISSION: Discover temporal coordination patterns that reveal hidden "
            "organizational dynamics no surface-level analysis would catch.\n"
            "AUTHORED BAR: Identify temporal patterns revealing coordination or hidden dynamics.\n"
            "AUTHORED HEURISTIC: Favor novel temporal signals over confirmatory ones.\n"
            "SCOPE: Analyze temporal patterns across 100K records.\n"
            "ACTUAL COST: $0.150 (of $0.500 allocated)\n"
            "OBSERVATIONS (4):\n"
            "  Obs 1 [data_originated_novel]: 47 resources abandoned in 2016-2017 correlated with framework migration.\n"
            "  Obs 2 [data_originated_novel]: 8 resources share identical modification timestamps.\n"
            "  Obs 3 [data_originated_novel]: resource-G utility exceeds parent by 2.18x.\n"
            "  Obs 4 [data_originated_confirmatory]: Seasonal patterns in creation dates.\n"
            "SELF-EVALUATION: bar_met=True, quality=high\n"
            "  Follow-up threads flagged:\n"
            "    - Trace the 2016-2017 abandonment wave to specific framework events\n"
            "    - Investigate the 8 identical-timestamp resources for coordinated control\n"
            "    - Map resource-G's growth trajectory vs parent to test decoupling hypothesis\n"
            "    - Cross-correlate temporal clusters with structural dependency data\n"
            "    - Analyze whether abandoned resources still have active downstream dependents\n"
        ),
        "expected_action": "CONTINUE",
        "expected_mission_ref": True,
        "why": "$7.50 available, ~50 hires affordable, 10 threads with different methods needed",
    },
    {
        "name": "2_genuinely_repetitive_threads",
        "description": "Threads are same-method extensions — should RESOLVE honestly",
        "budget": 3.00,
        "cost_context": (
            "OBSERVABLE COST DATA:\n"
            "  Your formation cost: $0.100\n"
            "  Hires completed: 3\n"
            "  Average cost per hire: $0.200\n"
            "  Total spent on hires: $0.600\n"
            "  Downstream phases estimate: $0.50\n"
            "  Budget after downstream: $2.00 available for continuation\n"
        ),
        "hire_reports": (
            "--- HIRE: dependency mapper ---\n"
            "AUTHORED MISSION: Produce comprehensive dependency map with all chains traced.\n"
            "AUTHORED BAR: Map all direct and transitive dependencies with exact counts.\n"
            "AUTHORED HEURISTIC: Be thorough.\n"
            "SCOPE: Map dependencies across all records.\n"
            "ACTUAL COST: $0.200 (of $0.300 allocated)\n"
            "OBSERVATIONS (4):\n"
            "  Obs 1 [data_originated_novel]: resource-A has 15 direct deps, 847 transitive.\n"
            "  Obs 2 [data_originated_novel]: resource-B has 8 direct deps, 1203 transitive.\n"
            "  Obs 3 [data_originated_novel]: resource-C has 22 direct deps, 2044 transitive.\n"
            "  Obs 4 [data_originated_novel]: 12 circular dependency chains identified.\n"
            "SELF-EVALUATION: bar_met=True, quality=high\n"
            "  Follow-up threads flagged:\n"
            "    - Map more dependencies for resources D through Z\n"
            "    - Count transitive dependencies for additional resources\n"
            "    - Check more circular dependency chains\n"
        ),
        "expected_action": "RESOLVE",
        "expected_mission_ref": True,
        "why": "Threads are same method (dependency counting) — no different cognition needed",
    },
    {
        "name": "3_underfired_hire",
        "description": "Hire met bar but $0.40 unused with 3 worthwhile threads",
        "budget": 2.00,
        "cost_context": (
            "OBSERVABLE COST DATA:\n"
            "  Your formation cost: $0.080\n"
            "  Hires completed: 2\n"
            "  Average cost per hire: $0.120\n"
            "  Total spent on hires: $0.240\n"
            "  Downstream phases estimate: $0.30\n"
            "  Budget after downstream: $1.50 available for continuation\n"
        ),
        "hire_reports": (
            "--- HIRE: risk analyst ---\n"
            "AUTHORED MISSION: Identify every hidden risk vector and quantify potential "
            "impact with specific blast radius estimates.\n"
            "AUTHORED BAR: Identify risk vectors with named entities and impact estimates.\n"
            "AUTHORED HEURISTIC: Push deep on high-impact risks.\n"
            "SCOPE: Analyze risk vectors across critical infrastructure.\n"
            "ACTUAL COST: $0.060 (of $0.500 allocated)\n"
            "OBSERVATIONS (2):\n"
            "  Obs 1 [data_originated_novel]: resource-X has single maintainer controlling 500M interactions.\n"
            "  Obs 2 [data_originated_novel]: 3 resources share hidden controller entity-Y.\n"
            "SELF-EVALUATION: bar_met=True, quality=medium\n"
            "  Follow-up threads flagged:\n"
            "    - Quantify blast radius if entity-Y disappears (different: impact simulation vs structural mapping)\n"
            "    - Trace entity-Y's control history for temporal patterns (different: temporal vs structural)\n"
            "    - Cross-reference with known vulnerability databases (different: external validation vs internal analysis)\n\n"
            "--- HIRE: pattern detector ---\n"
            "AUTHORED MISSION: Find cross-cutting patterns that connect seemingly unrelated entities.\n"
            "AUTHORED BAR: Surface cross-entity patterns with specific evidence chains.\n"
            "AUTHORED HEURISTIC: Look for unexpected connections.\n"
            "SCOPE: Analyze cross-entity patterns.\n"
            "ACTUAL COST: $0.180 (of $0.300 allocated)\n"
            "OBSERVATIONS (4):\n"
            "  Obs 1 [data_originated_novel]: 5 entities share naming convention suggesting common origin.\n"
            "  Obs 2 [data_originated_novel]: resource-P and resource-Q have 94% code similarity.\n"
            "  Obs 3 [data_originated_novel]: entity-Z controls resources in 3 different categories.\n"
            "  Obs 4 [data_originated_novel]: temporal clustering of 12 resources around single event.\n"
            "SELF-EVALUATION: bar_met=True, quality=high\n"
        ),
        "expected_action": "CONTINUE",
        "expected_commitment": {"risk analyst": "UNDERFIRED"},
        "why": "Risk analyst spent $0.06 of $0.50, has 3 threads with different methods, clearly underfired",
    },
    {
        "name": "4_committed_hire",
        "description": "Hire used budget well, genuinely complete",
        "budget": 1.00,
        "cost_context": (
            "OBSERVABLE COST DATA:\n"
            "  Your formation cost: $0.050\n"
            "  Hires completed: 2\n"
            "  Average cost per hire: $0.150\n"
            "  Total spent on hires: $0.300\n"
            "  Downstream phases estimate: $0.30\n"
            "  Budget after downstream: $0.40 available for continuation\n"
        ),
        "hire_reports": (
            "--- HIRE: concentration analyst ---\n"
            "AUTHORED MISSION: Map every concentration point in the ecosystem with definitive evidence.\n"
            "AUTHORED BAR: Identify all entities controlling >5% of critical resources with exact figures.\n"
            "AUTHORED HEURISTIC: Be exhaustive within your scope.\n"
            "SCOPE: Analyze entity concentration across critical resources.\n"
            "ACTUAL COST: $0.140 (of $0.200 allocated)\n"
            "OBSERVATIONS (6):\n"
            "  Obs 1 [data_originated_novel]: entity-A controls 23% of top-100 resources.\n"
            "  Obs 2 [data_originated_novel]: entity-B controls 15% with single maintainer.\n"
            "  Obs 3 [data_originated_novel]: entity-C controls 8% across 3 categories.\n"
            "  Obs 4 [data_originated_novel]: top 5 entities control 62% of ecosystem.\n"
            "  Obs 5 [data_originated_novel]: entity-D has 100% control of auth category.\n"
            "  Obs 6 [data_originated_novel]: concentration increased 12% over last 2 years.\n"
            "SELF-EVALUATION: bar_met=True, quality=high\n\n"
            "--- HIRE: license analyst ---\n"
            "AUTHORED MISSION: Identify all licensing risks and unusual patterns.\n"
            "AUTHORED BAR: Map license distribution with anomaly identification.\n"
            "AUTHORED HEURISTIC: Flag anything unusual.\n"
            "SCOPE: Analyze licensing across all records.\n"
            "ACTUAL COST: $0.160 (of $0.200 allocated)\n"
            "OBSERVATIONS (4):\n"
            "  Obs 1 [data_originated_novel]: 3 resources changed from MIT to proprietary.\n"
            "  Obs 2 [data_originated_novel]: 15% of resources have no declared license.\n"
            "  Obs 3 [data_originated_novel]: license field contradicts repository for 8 resources.\n"
            "  Obs 4 [data_originated_confirmatory]: 78% MIT/Apache as expected.\n"
            "SELF-EVALUATION: bar_met=True, quality=high\n"
        ),
        "expected_action": "CONTINUE",  # $0.40 available, ~2 hires affordable
        "expected_commitment": {"concentration analyst": "COMMITTED", "license analyst": "COMMITTED"},
        "why": "Both used budget well. $0.40 remaining might warrant 1-2 small continuations if threads exist",
    },
    {
        "name": "5_overfired_hire",
        "description": "Hire spawned 6 continuations on diminishing returns",
        "budget": 0.50,
        "cost_context": (
            "OBSERVABLE COST DATA:\n"
            "  Your formation cost: $0.050\n"
            "  Hires completed: 1\n"
            "  Average cost per hire: $0.400\n"
            "  Total spent on hires: $0.400\n"
            "  Downstream phases estimate: $0.30\n"
            "  Budget after downstream: $0.00 available for continuation\n"
        ),
        "hire_reports": (
            "--- HIRE: deep investigator ---\n"
            "AUTHORED MISSION: Trace the most significant dependency chain to its full extent.\n"
            "AUTHORED BAR: Trace one critical chain with all entities named and impacts quantified.\n"
            "AUTHORED HEURISTIC: Push deep but recognize diminishing returns.\n"
            "SCOPE: Deep trace of resource-M dependency chain.\n"
            "ACTUAL COST: $0.400 (of $0.200 allocated)\n"
            "OBSERVATIONS (8):\n"
            "  Obs 1 [data_originated_novel]: resource-M chain depth is 7 levels.\n"
            "  Obs 2 [data_originated_novel]: level-3 entity controls 2 sub-chains.\n"
            "  Obs 3 [data_originated_confirmatory]: level-4 connects to known pattern.\n"
            "  Obs 4 [data_originated_confirmatory]: level-5 is leaf with expected structure.\n"
            "  Obs 5 [data_originated_confirmatory]: level-6 confirms known relationship.\n"
            "  Obs 6 [data_originated_confirmatory]: level-7 is terminal, no new information.\n"
            "  Obs 7 [data_originated_confirmatory]: cross-reference confirms level-2 finding.\n"
            "  Obs 8 [data_originated_confirmatory]: summary statistics match existing knowledge.\n"
            "SELF-EVALUATION: bar_met=True, quality=medium\n"
            "  Hired 6 sub-investigators for levels 3-7 + cross-reference, most returned confirmatory.\n"
        ),
        "expected_action": "RESOLVE",
        "expected_commitment": {"deep investigator": "OVERFIRED"},
        "why": "Spent $0.40 of $0.20 allocated, 6 of 8 obs are confirmatory, clearly overfired",
    },
]


async def main():
    print("THREE-LAYER FIX — Offline Validation")
    print("=" * 60)
    print("Mission framing + Criterion tightening + Commitment classification")

    results = []
    for s in SCENARIOS:
        print(f"\n{'─' * 60}")
        print(f"{s['name']}: {s['description']}")
        print(f"Expected: action={s['expected_action']} — {s['why']}")
        if 'expected_commitment' in s:
            print(f"Expected commitment: {s['expected_commitment']}")
        print(f"{'─' * 60}")

        r = await call_turn2(
            role_name="department lead",
            role_mission=MISSION,
            role_bar=BAR,
            scope="Investigate structural relationships across the ecosystem.",
            hire_reports=s["hire_reports"],
            budget=s["budget"],
            cost_context=s["cost_context"],
        )

        cont = r["result"].get("continuation_decision", {})
        action = cont.get("action", "?")
        arith = cont.get("arithmetic", {})
        mission_assess = cont.get("mission_assessment", "")
        reasoning = cont.get("reasoning", "")[:300]

        correct_action = action == s["expected_action"]

        # Check commitment classifications
        evals = r["result"].get("hire_evaluations", [])
        commitment_results = {}
        for ev in evals:
            name = ev.get("hire_role_name", "?")
            commitment_results[name] = ev.get("commitment", "?")

        commitment_correct = True
        if "expected_commitment" in s:
            for role_name, expected_commit in s["expected_commitment"].items():
                actual = commitment_results.get(role_name, "?")
                if actual != expected_commit:
                    commitment_correct = False

        # Check mission reference
        has_mission_ref = any(
            w in (reasoning + mission_assess).lower()
            for w in ["mission", "best possible", "leaving", "incomplete", "settling"]
        )

        print(f"\n  Action: {action} {'ok' if correct_action else 'WRONG'}")
        print(f"  Mission assessment: {mission_assess[:150]}")
        print(f"  Mission referenced: {has_mission_ref}")
        if arith:
            print(f"  Arithmetic: available=${arith.get('available_for_continuation', '?')}, "
                  f"affordable={arith.get('affordable_hires', arith.get('threads_with_different_methods', '?'))}")
        print(f"  Reasoning: {reasoning[:200]}")

        # Print commitment classifications
        for ev in evals:
            name = ev.get("hire_role_name", "?")
            cls = ev.get("classification", "?")
            commit = ev.get("commitment", "?")
            grounding = ev.get("commitment_grounding", "")[:150]
            expected = s.get("expected_commitment", {}).get(name, "")
            match = "" if not expected else (" ok" if commit == expected else " WRONG")
            print(f"  Hire '{name}': {cls} / {commit}{match}")
            if grounding:
                print(f"    Grounding: {grounding}")

        # Check worthwhile threads have method enumeration
        for ev in evals:
            threads = ev.get("worthwhile_threads", [])
            for t in threads[:2]:
                if isinstance(t, dict):
                    print(f"    Thread: {t.get('thread', '?')[:60]}")
                    print(f"      Prior method: {t.get('prior_method', '?')[:60]}")
                    print(f"      Proposed: {t.get('proposed_different_method', '?')[:60]}")

        results.append({
            "name": s["name"],
            "expected_action": s["expected_action"],
            "actual_action": action,
            "action_correct": correct_action,
            "commitment_correct": commitment_correct,
            "has_mission_ref": has_mission_ref,
            "commitment_results": commitment_results,
            "cost": r["cost"],
        })

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    action_correct = sum(1 for r in results if r["action_correct"])
    commit_correct = sum(1 for r in results if r["commitment_correct"])
    mission_refs = sum(1 for r in results if r["has_mission_ref"])

    for r in results:
        a = "ok" if r["action_correct"] else "WRONG"
        c = "ok" if r["commitment_correct"] else "WRONG"
        m = "mission" if r["has_mission_ref"] else "no-mission"
        print(f"  {a:5s} {c:5s} {m:10s} | {r['name']:40s} | "
              f"expected={r['expected_action']:8s} got={r['actual_action']:8s} | "
              f"commits={r['commitment_results']}")

    total_cost = sum(r["cost"] for r in results)
    print(f"\n  Actions: {action_correct}/{len(results)}")
    print(f"  Commitments: {commit_correct}/{len(results)}")
    print(f"  Mission refs: {mission_refs}/{len(results)}")
    print(f"  Cost: ${total_cost:.4f}")

    with open(OUT_DIR / "results.md", "w") as f:
        f.write(f"# Three-Layer Fix — Offline Validation\n\n")
        f.write(f"Actions: {action_correct}/{len(results)}\n")
        f.write(f"Commitments: {commit_correct}/{len(results)}\n")
        f.write(f"Mission refs: {mission_refs}/{len(results)}\n")
        f.write(f"Cost: ${total_cost:.4f}\n\n")
        for r in results:
            f.write(f"## {r['name']}\n")
            f.write(f"Action: expected={r['expected_action']} got={r['actual_action']} "
                    f"{'PASS' if r['action_correct'] else 'FAIL'}\n")
            f.write(f"Commitment: {'PASS' if r['commitment_correct'] else 'FAIL'} "
                    f"{r['commitment_results']}\n")
            f.write(f"Mission ref: {r['has_mission_ref']}\n\n")


if __name__ == "__main__":
    asyncio.run(main())
