"""Offline validation for recalibration changes.

Tests:
A. Ceiling reasoning (3 scenarios)
B. Reasoning-evidence quality (3 scenarios)
C. Dual-scoring reader test (score existing findings)
"""

import asyncio
import json
import datetime
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/recalibration_traces")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def call_v2(role_name, role_bar, role_heuristic, scope, purpose,
                   parent_context, workspace_context, records, budget,
                   force_resolve=""):
    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import NODE_REASONING_PROMPT_V2

    prompt = NODE_REASONING_PROMPT_V2.format(
        current_date=datetime.date.today().isoformat(),
        role_name=role_name, role_bar=role_bar, role_heuristic=role_heuristic,
        scope_description=scope, purpose=purpose, parent_context=parent_context,
        workspace_context=workspace_context, filter_schema="(not applicable)",
        budget_remaining=budget, parent_pool_remaining=budget * 2,
        phase_remaining=budget * 3, segment_context="",
        current_depth=0, max_depth=6, leaf_viable_envelope=0.12,
        depth_guidance="Each hire must receive at least $0.12.",
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


# Generate records of different sizes
def make_records(n):
    return json.dumps([
        {"id": f"record-{i}", "value_a": i * 100, "entity": f"entity-{chr(65+i%26)}",
         "category": ["alpha", "beta", "gamma", "delta"][i % 4],
         "created": f"202{i%6}-0{(i%9)+1}-15",
         "relationships": [f"entity-{chr(65+(i+3)%26)}"]}
        for i in range(n)
    ], indent=2)


async def test_a_ceiling():
    """Ceiling reasoning: scope-vs-capacity with same role, varying scope size."""
    print("\n" + "="*60)
    print("A. CEILING REASONING")
    print("="*60)

    bar = (
        "Produce specific findings about cross-entity relationships with named "
        "entities and exact figures. Requires cross-item analysis — comparing "
        "entities against each other, not analyzing items in isolation."
    )

    scenarios = [
        ("small_scope_investigate", 5, 2.00, "investigate",
         "5 items, cross-item analysis feasible in one pass"),
        ("large_scope_hire", 100, 2.00, "hire",
         "100 items requiring cross-item analysis exceeds one-pass capacity"),
        ("large_scope_thin_budget", 100, 0.25, "investigate",
         "100 items but $0.25 can't fund hires — must investigate despite scope"),
    ]

    results = []
    for name, n_records, budget, expected, why in scenarios:
        print(f"\n  {name}: {n_records} records, ${budget:.2f}, expected={expected}")
        r = await call_v2(
            role_name="cross-entity analyst", role_bar=bar,
            role_heuristic="Favor thorough coverage over speed.",
            scope=f"Analyze cross-entity relationships across {n_records} records.",
            purpose="Find cross-entity patterns.", parent_context="Assigned.",
            workspace_context="", records=make_records(n_records), budget=budget,
        )
        fa = r["result"].get("formation_assessment", {})
        decision = fa.get("decision", "?")
        has_scope = bool(fa.get("scope_size", ""))
        has_bar = bool(fa.get("bar_depth", ""))
        has_capacity = bool(fa.get("capacity_estimate", ""))
        correct = decision == expected
        print(f"  Decision: {decision} {'✓' if correct else '✗'} | scope={has_scope} bar={has_bar} capacity={has_capacity}")
        print(f"  Reasoning: {fa.get('reasoning', '')[:150]}")
        results.append({"name": name, "expected": expected, "actual": decision,
                        "correct": correct, "has_traces": has_scope and has_bar and has_capacity,
                        "cost": r["cost"]})
    return results


async def test_b_reasoning_evidence():
    """Reasoning-evidence: verify decisions include grounded traces."""
    print("\n" + "="*60)
    print("B. REASONING-EVIDENCE QUALITY")
    print("="*60)

    # Scenario a: Worker decides to investigate alone with large scope
    print("\n  b1: Large-scope investigate decision — should show capacity reasoning")
    r1 = await call_v2(
        role_name="structural analyst",
        role_bar="Map all cross-entity dependency chains. Must trace every entity's relationships.",
        role_heuristic="Be thorough.",
        scope="Analyze structural relationships across 10,000 records.",
        purpose="Map all dependency chains.", parent_context="Assigned.",
        workspace_context="", records=make_records(20), budget=2.00,
    )
    fa1 = r1["result"].get("formation_assessment", {})
    reasoning1 = fa1.get("reasoning", "")
    has_concrete = any(w in reasoning1.lower() for w in
                       ["record", "item", "scope", "capacity", "pass", "depth"])
    print(f"  Decision: {fa1.get('decision', '?')} | Concrete reasoning: {has_concrete}")
    print(f"  Reasoning: {reasoning1[:200]}")

    # Scenario b: Check if hires have justifications
    print("\n  b2: Hire justifications — should ground each hire")
    r2 = await call_v2(
        role_name="engagement lead",
        role_bar="Design team to cover this scope meeting charter standards.",
        role_heuristic="Hire when distinct cognition needed.",
        scope="Investigate 100 records across 4 categories with cross-category relationships.",
        purpose="Design the investigation.", parent_context="Charter demands thorough coverage.",
        workspace_context="## CHARTER\nFind hidden structures. Named entities required.",
        records=make_records(30), budget=3.00,
    )
    hires = r2["result"].get("hire_directives", [])
    justified = sum(1 for h in hires if h.get("justification", "").strip())
    print(f"  Hires: {len(hires)} | With justification: {justified}")
    for h in hires[:3]:
        j = h.get("justification", "(none)")[:100]
        print(f"    - {h.get('role', {}).get('name', '?')}: {j}")

    # Scenario c: Check formation assessment traces
    print("\n  b3: Formation traces present in output")
    fa2 = r2["result"].get("formation_assessment", {})
    traces = {
        "scope_size": bool(fa2.get("scope_size", "")),
        "bar_depth": bool(fa2.get("bar_depth", "")),
        "capacity_estimate": bool(fa2.get("capacity_estimate", "")),
    }
    all_traces = all(traces.values())
    print(f"  Traces: {traces} | All present: {all_traces}")

    return {
        "concrete_reasoning": has_concrete,
        "hires_justified": justified == len(hires) if hires else False,
        "traces_present": all_traces,
    }


async def test_c_dual_scoring():
    """Dual-scoring reader test on existing findings."""
    print("\n" + "="*60)
    print("C. DUAL-SCORING READER TEST")
    print("="*60)

    from mycelium.reader_test import score_findings

    charter = """\
# ORGANIZATIONAL CHARTER
**What Is Already Known:**
- Single-entity concentration in critical resources
- Power-law distribution in resource usage
- Permissive licensing dominance

**What Impresses Us:** Hidden structural dependencies, unexpected failure modes.
"""

    findings = [
        {
            "summary": "Resource-M (851M interactions) is a transitive dependency of 89% "
                       "of top-100 resources but never appears as a direct dependency.",
            "evidence": "resource-M: 851M interactions, 14200 dependents, never listed directly.",
            "validation_status": "confirmed",
        },
        {
            "summary": "Resource-X has 580M interactions and is maintained by a single controller.",
            "evidence": "resource-X: 580M interactions, controller_count=1, controller=entity-A.",
            "validation_status": "confirmed",
        },
        {
            "summary": "Resource-T shows 0 interactions despite 4631 versions and active maintenance.",
            "evidence": "resource-T: 0 interactions, 4631 versions, last_modified yesterday, "
                        "sibling at 457M interactions.",
            "validation_status": "needs_verification",
        },
    ]

    scores = await score_findings(charter, findings)

    for f, s in zip(findings, scores):
        fn = s.get("factual_novelty", "?")
        ic = s.get("interpretive_certainty", "?")
        cs = s.get("score", "?")
        print(f"\n  Finding: {f['summary'][:60]}...")
        print(f"  Factual novelty: {fn} | Interpretive certainty: {ic} | Combined: {cs}")
        print(f"  Reasoning: {s.get('reasoning', '')[:150]}")

    return scores


async def main():
    print("RECALIBRATION — Offline Validation")
    print("="*60)
    print("First pass. No iteration.")

    results_a = await test_a_ceiling()
    results_b = await test_b_reasoning_evidence()
    results_c = await test_c_dual_scoring()

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    a_correct = sum(1 for r in results_a if r["correct"])
    a_traces = sum(1 for r in results_a if r["has_traces"])
    print(f"\n  A. Ceiling reasoning: {a_correct}/{len(results_a)} correct, {a_traces}/{len(results_a)} with traces")
    for r in results_a:
        s = "✓" if r["correct"] else "✗"
        print(f"    {s} {r['name']:30s} | expected={r['expected']:12s} | got={r['actual']}")

    print(f"\n  B. Reasoning-evidence:")
    print(f"    Concrete reasoning: {results_b['concrete_reasoning']}")
    print(f"    Hires justified: {results_b['hires_justified']}")
    print(f"    Traces present: {results_b['traces_present']}")

    print(f"\n  C. Dual-scoring reader test:")
    for s in results_c:
        cs = s.get("score", "?")
        fn = s.get("factual_novelty", "?")
        print(f"    [{cs:12s}] fn={fn:8s} | {s['finding_summary'][:60]}")

    total_cost = sum(r["cost"] for r in results_a) + sum(r["cost"] for r in results_c)
    print(f"\n  Total cost: ${total_cost:.4f}")

    # Save
    with open(OUT_DIR / "results.md", "w") as f:
        f.write("# Recalibration Offline Validation\n\n")
        f.write(f"Ceiling: {a_correct}/{len(results_a)} correct, {a_traces}/{len(results_a)} with traces\n")
        f.write(f"Reasoning: concrete={results_b['concrete_reasoning']}, "
                f"justified={results_b['hires_justified']}, traces={results_b['traces_present']}\n")
        f.write(f"Reader test: {json.dumps([s['score'] for s in results_c])}\n")


if __name__ == "__main__":
    asyncio.run(main())
