"""Offline validation for worker mid-investigation reassessment.

Five scenarios:
1. Simple scope, patterns match formation → CONTINUE_INVESTIGATING
2. Moderate scope, one thread needs deeper push → EXTEND_MYSELF
3. Broad scope, multiple distinct dimensions → BECOME_MANAGER
4. Abundant budget, mission pushes for more → EXTEND_MYSELF
5. Thin budget, scope too big → EXTEND_MYSELF (can't afford hiring)
"""

import asyncio
import json
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/worker_reassessment_traces")
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
        "name": "1_simple_scope_continue",
        "description": "Simple scope, findings match formation — should CONTINUE",
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
            "  1. [data_originated_novel] 78% MIT, 12% Apache-2.0, 5% ISC, 3% BSD-3, 2% other\n"
            "  2. [data_originated_novel] 3 packages changed from MIT to proprietary in last 6 months\n"
            "  3. [data_originated_novel] 15 packages have no declared license despite active maintenance\n"
            "  4. [data_originated_novel] License field contradicts repository for 8 packages"
        ),
        "observation_count": 4,
        "budget_allocated": 0.50,
        "budget_spent": 0.17,
        "budget_remaining": 0.33,
        "expected": "CONTINUE_INVESTIGATING",
        "why": "Simple categorization work, findings match formation assessment, mission satisfied",
    },
    {
        "name": "2_moderate_extend",
        "description": "Found a thread that needs deeper push — should EXTEND",
        "role_name": "dependency chain analyst",
        "role_mission": "Trace every hidden dependency chain and quantify blast radii.",
        "role_bar": "Map dependency chains with named entities and impact estimates.",
        "formation_summary": (
            "Decision: investigate\n"
            "Scope: 100 packages, dependency relationships\n"
            "Bar depth: cross-item chain tracing\n"
            "Capacity: one pass can identify chains, may need deeper tracing\n"
            "Reasoning: Cross-item analysis feasible in one pass."
        ),
        "observations": (
            "  1. [data_originated_novel] react-is has 1.095B downloads but 0 direct dependents listed\n"
            "  2. [data_originated_novel] 5 packages form a circular dependency chain (A→B→C→D→E→A)\n"
            "  3. [data_originated_novel] entity-X controls 7 packages in the chain, all single-maintainer\n"
            "  4. [data_originated_novel] 3 packages have identical modification timestamps suggesting coordination"
        ),
        "observation_count": 4,
        "budget_allocated": 1.00,
        "budget_spent": 0.17,
        "budget_remaining": 0.83,
        "expected": "EXTEND_MYSELF",
        "why": "Circular chain + single maintainer control needs deeper quantification — same cognition, more depth",
    },
    {
        "name": "3_broad_become_manager",
        "description": "Multiple distinct dimensions needing different methods — should BECOME_MANAGER",
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
            "  1. [data_originated_novel] 3 entities control 62% of critical infrastructure through dependency chains\n"
            "  2. [data_originated_novel] Temporal clustering: 47 packages abandoned in 2016-2017 wave\n"
            "  3. [data_originated_novel] Download anomaly: vue shows 48M/month but search score is 0\n"
            "  4. [data_originated_novel] 8 packages share identical commit timestamps across different maintainers\n"
            "  5. [data_originated_novel] License migration pattern: 12 packages changed license type simultaneously"
        ),
        "observation_count": 5,
        "budget_allocated": 2.00,
        "budget_spent": 0.18,
        "budget_remaining": 1.82,
        "expected": "BECOME_MANAGER",
        "why": "Dependency analysis, temporal analysis, download forensics, coordination detection — 4 distinct cognition types",
    },
    {
        "name": "4_abundant_budget_extend",
        "description": "Matching formation but mission pushes for more — should EXTEND",
        "role_name": "power concentration analyst",
        "role_mission": "Identify every single point of failure and quantify its blast radius with specific impact numbers.",
        "role_bar": "Identify entities controlling >5% of critical resources.",
        "formation_summary": (
            "Decision: investigate\n"
            "Scope: 100 packages, entity concentration\n"
            "Bar depth: per-entity percentage calculation\n"
            "Capacity: one pass covers this\n"
            "Reasoning: Straightforward percentage analysis."
        ),
        "observations": (
            "  1. [data_originated_novel] sindresorhus controls 23% of top-100 (single maintainer)\n"
            "  2. [data_originated_novel] jdalton controls 15% through lodash ecosystem\n"
            "  3. [data_originated_novel] top 5 entities control 62% of ecosystem"
        ),
        "observation_count": 3,
        "budget_allocated": 1.50,
        "budget_spent": 0.16,
        "budget_remaining": 1.34,
        "expected": "EXTEND_MYSELF",
        "why": "Bar is met (identified >5% entities) but mission demands blast radius quantification — same cognition, more depth",
    },
    {
        "name": "5_thin_budget_extend",
        "description": "Scope bigger than expected but can't afford hiring — should EXTEND",
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
            "  1. [data_originated_novel] 47 packages abandoned in 2016-2017 correlated with framework migration\n"
            "  2. [data_originated_novel] 8 packages share identical modification timestamps\n"
            "  3. [data_originated_novel] Seasonal creation patterns suggest organizational cycles\n"
            "  4. [data_originated_novel] 12 packages show coordinated version bumps within 60-second windows"
        ),
        "observation_count": 4,
        "budget_allocated": 0.25,
        "budget_spent": 0.18,
        "budget_remaining": 0.07,
        "expected": "CONTINUE_INVESTIGATING",
        "why": "Can't afford extension ($0.07 remaining), should resolve with what it has",
    },
]


async def main():
    print("WORKER REASSESSMENT — Offline Validation")
    print("=" * 60)

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
        has_formation_comparison = bool(reassessment.get("formation_expected"))
        has_reality = bool(reassessment.get("reality_found"))
        has_gap = bool(reassessment.get("gap"))
        has_threads = len(reassessment.get("threads", [])) > 0

        print(f"\n  Decision: {decision} {'ok' if correct else 'WRONG'}")
        print(f"  Formation comparison: {has_formation_comparison}")
        print(f"  Reality grounded: {has_reality}")
        print(f"  Gap identified: {has_gap}")
        print(f"  Threads: {len(reassessment.get('threads', []))}")
        print(f"  Mission met: {reassessment.get('mission_met', '?')}")
        print(f"  Reasoning: {reasoning}")

        # Print threads
        for t in reassessment.get("threads", [])[:3]:
            if isinstance(t, dict):
                same = "same" if t.get("same_cognition") else "different"
                sub = "substantive" if t.get("substantive") else "incremental"
                print(f"    Thread: {t.get('thread', '?')[:60]} ({same}, {sub})")

        results.append({
            "name": s["name"],
            "expected": s["expected"],
            "actual": decision,
            "correct": correct,
            "has_comparison": has_formation_comparison and has_reality,
            "has_threads": has_threads,
            "cost": r["cost"],
        })

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    correct_count = sum(1 for r in results if r["correct"])
    comparison_count = sum(1 for r in results if r["has_comparison"])

    for r in results:
        s = "ok" if r["correct"] else "WRONG"
        c = "compared" if r["has_comparison"] else "no-compare"
        print(f"  {s:5s} {c:10s} | {r['name']:40s} | expected={r['expected']:25s} | got={r['actual']}")

    total_cost = sum(r["cost"] for r in results)
    print(f"\n  Decisions: {correct_count}/{len(results)}")
    print(f"  Formation-reality comparison: {comparison_count}/{len(results)}")
    print(f"  Cost: ${total_cost:.4f}")

    with open(OUT_DIR / "results.md", "w") as f:
        f.write(f"# Worker Reassessment — Offline Validation\n\n")
        f.write(f"Decisions: {correct_count}/{len(results)}\n")
        f.write(f"Comparisons: {comparison_count}/{len(results)}\n")
        f.write(f"Cost: ${total_cost:.4f}\n\n")
        for r in results:
            f.write(f"## {r['name']}\n")
            f.write(f"Expected: {r['expected']} | Got: {r['actual']} | "
                    f"{'PASS' if r['correct'] else 'FAIL'}\n\n")


if __name__ == "__main__":
    asyncio.run(main())
