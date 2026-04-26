"""Offline validation for unified floor/ceiling re-assessment.

Five scenarios — verify the reasoning trace shows floor/ceiling tests
being re-run, not menu selection, and the outcome emerges from test results.

1. Simple scope, work complete → RESOLVE
2. Moderate scope, one thread deeper → INVESTIGATE_FURTHER
3. Broad scope, distinct dimensions → HIRE
4. Abundant budget, mission pushes more → INVESTIGATE_FURTHER
5. Thin budget, can't afford more → RESOLVE
"""

import asyncio
import json
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/unified_assessment_traces")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def call_reassessment(role_name, role_mission, role_bar,
                            formation_summary, observations_summary,
                            observation_count, budget_allocated,
                            budget_spent, budget_remaining):
    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import WORKER_REASSESSMENT_PROMPT_V2

    prompt = WORKER_REASSESSMENT_PROMPT_V2.format(
        role_name=role_name,
        role_mission=role_mission,
        role_bar=role_bar,
        formation_summary=formation_summary,
        observation_count=observation_count,
        observations_summary=observations_summary,
        budget_allocated=budget_allocated,
        budget_spent=budget_spent,
        budget_remaining=budget_remaining,
        leaf_viable_envelope=0.12,
        min_hire_budget=0.24,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        thinking={"type": "enabled", "budget_tokens": 8000},
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
        "name": "1_simple_resolve",
        "description": "Simple scope, findings match formation, work is complete",
        "role_name": "license distribution analyst",
        "role_mission": "Map the complete license distribution with all anomalies identified.",
        "role_bar": "Identify license distribution with named packages and percentages.",
        "formation_summary": (
            "Decision: investigate\n"
            "Scope: 100 packages, license field analysis\n"
            "Bar depth: per-item categorization, cross-item statistics\n"
            "Capacity: one pass can cover 100 items at this depth\n"
            "Reasoning: Scope fits single-pass capacity for categorization work."
        ),
        "observations": (
            "  1. [data_originated_novel] 78% MIT, 12% Apache-2.0, 5% ISC, 3% BSD-3, 2% other — complete distribution mapped\n"
            "  2. [data_originated_novel] 3 packages changed from MIT to proprietary: pkg-A, pkg-B, pkg-C — dates and motivations documented\n"
            "  3. [data_originated_novel] 15 packages have no declared license — all identified by name, each categorized by maintenance status\n"
            "  4. [data_originated_novel] License field contradicts repository for 8 packages — each contradiction documented with both values"
        ),
        "observation_count": 4,
        "budget_allocated": 0.50,
        "budget_spent": 0.17,
        "budget_remaining": 0.33,
        "expected": "RESOLVE",
        "why": "Work is thorough — all anomalies named, categorized, documented. Mission satisfied.",
    },
    {
        "name": "2_moderate_investigate_further",
        "description": "Found thread that needs deeper push with same cognition",
        "role_name": "dependency chain analyst",
        "role_mission": "Trace every hidden dependency chain and quantify blast radii with specific impact numbers.",
        "role_bar": "Map dependency chains with named entities and impact estimates.",
        "formation_summary": (
            "Decision: investigate\n"
            "Scope: 100 packages, dependency relationships\n"
            "Bar depth: cross-item chain tracing\n"
            "Capacity: one pass can identify chains, may need deeper tracing\n"
            "Reasoning: Cross-item analysis feasible in one pass."
        ),
        "observations": (
            "  1. [data_originated_novel] react-is has 1.095B downloads but 0 direct dependents listed — hidden transitive dependency\n"
            "  2. [data_originated_novel] 5 packages form circular chain A→B→C→D→E→A — identified but blast radius not quantified\n"
            "  3. [data_originated_novel] entity-X controls 7 packages in chain, all single-maintainer — concentration identified but impact unknown\n"
            "  4. [data_originated_novel] 3 packages have identical timestamps suggesting coordination — flagged but mechanism not traced"
        ),
        "observation_count": 4,
        "budget_allocated": 1.00,
        "budget_spent": 0.17,
        "budget_remaining": 0.83,
        "expected": "INVESTIGATE_FURTHER",
        "why": "Chains identified but blast radii not quantified — same cognition, more depth needed",
    },
    {
        "name": "3_broad_hire",
        "description": "Multiple distinct dimensions needing different analytical methods",
        "role_name": "ecosystem structure investigator",
        "role_mission": "Uncover every hidden structural relationship in the ecosystem.",
        "role_bar": "Identify cross-entity patterns with named entities and evidence chains.",
        "formation_summary": (
            "Decision: investigate\n"
            "Scope: 100 packages, broad structural analysis\n"
            "Bar depth: cross-entity relationship mapping\n"
            "Capacity: assessed as feasible for one pass\n"
            "Reasoning: Broad but manageable scope."
        ),
        "observations": (
            "  1. [data_originated_novel] 3 entities control 62% of critical infrastructure through dependency chains — requires DEPENDENCY GRAPH ANALYSIS to trace full chains\n"
            "  2. [data_originated_novel] 47 packages abandoned in 2016-2017 wave correlated with framework migration — requires TEMPORAL EVENT ANALYSIS to establish causation\n"
            "  3. [data_originated_novel] vue shows 48M downloads/month but search score is 0 — requires DOWNLOAD FORENSICS to investigate data pipeline\n"
            "  4. [data_originated_novel] 8 packages share identical commit timestamps across different maintainers — requires COORDINATION DETECTION ANALYSIS\n"
            "  5. [data_originated_novel] 12 packages changed license type simultaneously — requires ORGANIZATIONAL BEHAVIOR ANALYSIS"
        ),
        "observation_count": 5,
        "budget_allocated": 2.00,
        "budget_spent": 0.18,
        "budget_remaining": 1.82,
        "expected": "HIRE",
        "why": "5 distinct analytical methods identified (graph, temporal, forensics, coordination, organizational) — ceiling test fails, can't cover all at mission depth",
    },
    {
        "name": "4_abundant_investigate_further",
        "description": "Bar met but mission demands more depth — same cognition",
        "role_name": "power concentration analyst",
        "role_mission": "Identify every single point of failure and quantify its blast radius with specific downstream impact numbers.",
        "role_bar": "Identify entities controlling >5% of critical resources.",
        "formation_summary": (
            "Decision: investigate\n"
            "Scope: 100 packages, entity concentration\n"
            "Bar depth: per-entity percentage calculation\n"
            "Capacity: one pass covers this\n"
            "Reasoning: Straightforward percentage analysis."
        ),
        "observations": (
            "  1. [data_originated_novel] sindresorhus controls 23% of top-100 (single maintainer) — identified but blast radius not computed\n"
            "  2. [data_originated_novel] jdalton controls 15% through lodash ecosystem — identified but downstream chain not traced\n"
            "  3. [data_originated_novel] top 5 entities control 62% of ecosystem — aggregate stat, individual impacts unknown"
        ),
        "observation_count": 3,
        "budget_allocated": 1.50,
        "budget_spent": 0.16,
        "budget_remaining": 1.34,
        "expected": "INVESTIGATE_FURTHER",
        "why": "Bar met but mission demands blast radius quantification — same cognition (concentration analysis), needs more depth not different methods",
    },
    {
        "name": "5_thin_budget_resolve",
        "description": "Scope bigger than expected but can't afford more work",
        "role_name": "temporal pattern detector",
        "role_mission": "Discover temporal coordination patterns revealing hidden organizational dynamics.",
        "role_bar": "Identify temporal patterns with specific timestamps and entity names.",
        "formation_summary": (
            "Decision: investigate\n"
            "Scope: 100 packages, temporal analysis\n"
            "Bar depth: timestamp correlation\n"
            "Capacity: one pass for pattern detection\n"
            "Reasoning: Temporal analysis fits single pass."
        ),
        "observations": (
            "  1. [data_originated_novel] 47 packages abandoned 2016-2017 correlated with framework migration\n"
            "  2. [data_originated_novel] 8 packages share identical modification timestamps\n"
            "  3. [data_originated_novel] Seasonal creation patterns suggest organizational cycles\n"
            "  4. [data_originated_novel] 12 packages show coordinated version bumps within 60-second windows"
        ),
        "observation_count": 4,
        "budget_allocated": 0.25,
        "budget_spent": 0.18,
        "budget_remaining": 0.07,
        "expected": "RESOLVE",
        "why": "Budget below $0.15 — cannot afford another reasoning turn",
    },
]


async def main():
    print("UNIFIED ASSESSMENT — Offline Validation")
    print("=" * 60)
    print("Floor/ceiling re-assessment, no predetermined menu")

    results = []
    for s in SCENARIOS:
        print(f"\n{'─' * 60}")
        print(f"{s['name']}: {s['description']}")
        print(f"Expected: {s['expected']} — {s['why']}")
        print(f"{'─' * 60}")

        r = await call_reassessment(
            role_name=s["role_name"],
            role_mission=s["role_mission"],
            role_bar=s["role_bar"],
            formation_summary=s["formation_summary"],
            observations_summary=s["observations"],
            observation_count=s["observation_count"],
            budget_allocated=s["budget_allocated"],
            budget_spent=s["budget_spent"],
            budget_remaining=s["budget_remaining"],
        )

        result = r["result"]
        decision = result.get("decision", "?")
        reassessment = result.get("reassessment", {})
        reasoning = result.get("decision_reasoning", "")[:200]

        correct = decision == s["expected"]
        has_floor = bool(reassessment.get("floor_test", {}).get("reasoning"))
        has_ceiling = bool(reassessment.get("ceiling_test", {}).get("reasoning"))
        has_what_changed = bool(reassessment.get("what_changed"))

        floor = reassessment.get("floor_test", {})
        ceiling = reassessment.get("ceiling_test", {})

        print(f"\n  Decision: {decision} {'ok' if correct else 'WRONG'}")
        print(f"  Floor test: delegation_justified={floor.get('delegation_overhead_justified', '?')}")
        print(f"    {floor.get('reasoning', '?')[:150]}")
        print(f"  Ceiling test: scope_exceeds={ceiling.get('scope_exceeds_capacity', '?')}")
        print(f"    {ceiling.get('reasoning', '?')[:150]}")
        print(f"  Mission met: {reassessment.get('mission_met', '?')}")
        print(f"  What changed: {str(reassessment.get('what_changed', '?'))[:150]}")
        print(f"  Reasoning: {reasoning}")

        results.append({
            "name": s["name"],
            "expected": s["expected"],
            "actual": decision,
            "correct": correct,
            "has_tests": has_floor and has_ceiling,
            "has_what_changed": has_what_changed,
            "floor_justified": floor.get("delegation_overhead_justified", None),
            "ceiling_exceeds": ceiling.get("scope_exceeds_capacity", None),
            "cost": r["cost"],
        })

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    correct_count = sum(1 for r in results if r["correct"])
    test_count = sum(1 for r in results if r["has_tests"])

    for r in results:
        s = "ok" if r["correct"] else "WRONG"
        t = "tests" if r["has_tests"] else "no-tests"
        f = f"floor={r['floor_justified']}" if r["floor_justified"] is not None else "floor=?"
        c = f"ceil={r['ceiling_exceeds']}" if r["ceiling_exceeds"] is not None else "ceil=?"
        print(f"  {s:5s} {t:8s} | {r['name']:40s} | expected={r['expected']:22s} | got={r['actual']:22s} | {f} {c}")

    total_cost = sum(r["cost"] for r in results)
    print(f"\n  Decisions: {correct_count}/{len(results)}")
    print(f"  Floor/ceiling tests present: {test_count}/{len(results)}")
    print(f"  Cost: ${total_cost:.4f}")

    with open(OUT_DIR / "results.md", "w") as f:
        f.write(f"# Unified Assessment — Offline Validation\n\n")
        f.write(f"Decisions: {correct_count}/{len(results)}\n")
        f.write(f"Tests present: {test_count}/{len(results)}\n")
        f.write(f"Cost: ${total_cost:.4f}\n\n")
        for r in results:
            f.write(f"## {r['name']}\n")
            f.write(f"Expected: {r['expected']} | Got: {r['actual']} | "
                    f"{'PASS' if r['correct'] else 'FAIL'}\n")
            f.write(f"Floor: {r['floor_justified']} | Ceiling: {r['ceiling_exceeds']}\n\n")


if __name__ == "__main__":
    asyncio.run(main())
