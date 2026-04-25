"""Offline validation for role-authoring path (Build A).

Three test categories:
A. Formation-time assessment — does the node correctly decide investigate vs hire
   based on budget while holding role and scope constant?
B. Role-anchored emission — the 7 leakage scenarios with hand-authored role
   definitions. Does the role's bar catch what surface-and-commit missed?
C. First-node organizational design — does the engagement lead produce a
   sensible team design from a charter?

First pass — no iteration against scenarios.
"""

import asyncio
import json
import datetime
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/role_authoring_traces")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def call_with_v2_prompt(role_name, role_bar, role_heuristic, scope, purpose,
                               parent_context, workspace_context, records,
                               budget, force_resolve=""):
    """Call the V2 prompt with given parameters and return traces."""
    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import NODE_REASONING_PROMPT_V2

    prompt = NODE_REASONING_PROMPT_V2.format(
        current_date=datetime.date.today().isoformat(),
        role_name=role_name,
        role_bar=role_bar,
        role_heuristic=role_heuristic,
        scope_description=scope,
        purpose=purpose,
        parent_context=parent_context,
        workspace_context=workspace_context,
        filter_schema="(not applicable for this test)",
        budget_remaining=budget,
        parent_pool_remaining=budget * 2,
        phase_remaining=budget * 3,
        segment_context="",
        current_depth=0,
        max_depth=6,
        leaf_viable_envelope=0.12,
        depth_guidance=f"Each hire must receive at least $0.12.",
        budget_stage="EARLY — explore broadly." if budget > 0.30 else "LATE — resolve what you have.",
        doc_count=len(json.loads(records)) if records != "(no data)" else 0,
        fetched_data=records,
        force_resolve=force_resolve,
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

    return {
        "thinking": thinking,
        "result": result,
        "cost": cost,
    }


# =============================================================
# CATEGORY A: Formation-time assessment
# =============================================================

async def test_a_formation_assessment():
    """Same role and scope, vary budget. Low budget → investigate. High budget → hire."""
    print("\n" + "="*60)
    print("CATEGORY A: Formation-Time Assessment")
    print("="*60)

    role_name = "field analyst"
    role_bar = (
        "Produce specific, named findings backed by evidence from the records. "
        "Each finding must cite exact values from the data. Vague patterns without "
        "specific entities do not meet the bar."
    )
    role_heuristic = "When uncertain, favor depth on fewer items over shallow coverage of many."
    scope = "Analyze records in this scope to identify anomalies and structural patterns."
    purpose = "Find specific findings that meet the engagement's quality standards."

    records = json.dumps([
        {"id": f"record-{i}", "value_a": i * 100, "value_b": 1000 - i * 50,
         "entity": f"entity-{chr(65+i)}", "status": "active" if i < 5 else "inactive",
         "created": f"202{i}-01-01"}
        for i in range(10)
    ], indent=2)

    results_a = []

    for budget, expected_decision in [(0.20, "investigate"), (2.00, "hire")]:
        print(f"\n  Budget: ${budget:.2f} — expected: {expected_decision}")

        r = await call_with_v2_prompt(
            role_name=role_name, role_bar=role_bar, role_heuristic=role_heuristic,
            scope=scope, purpose=purpose,
            parent_context="Program office assigned this scope.",
            workspace_context="",
            records=records, budget=budget,
        )

        formation = r["result"].get("formation_assessment", {})
        decision = formation.get("decision", "?")
        reasoning = formation.get("reasoning", "")[:200]
        obs = r["result"].get("observations", [])
        hires = r["result"].get("hire_directives", [])

        correct = decision == expected_decision
        print(f"  Decision: {decision} {'✓' if correct else '✗'}")
        print(f"  Reasoning: {reasoning}")
        if decision == "hire":
            print(f"  Hires proposed: {len(hires)}")
            for h in hires:
                hr = h.get("role", {})
                print(f"    - {hr.get('name', '?')}: {hr.get('success_bar', '?')[:80]}")
        else:
            print(f"  Observations: {len(obs)}")

        results_a.append({
            "budget": budget, "expected": expected_decision,
            "actual": decision, "correct": correct, "cost": r["cost"],
        })

    return results_a


# =============================================================
# CATEGORY B: Role-anchored emission (7 leakage scenarios)
# =============================================================

async def test_b_role_emission():
    """The 7 leakage scenarios with role definitions. Does the bar catch what DDJ missed?"""
    print("\n" + "="*60)
    print("CATEGORY B: Role-Anchored Emission (7 scenarios)")
    print("="*60)

    scenarios = [
        {
            "name": "1_shape_leakage",
            "description": "Record matches charter's known category with different specifics",
            "role_bar": (
                "Find structural dependencies or failure modes that are NOT already "
                "documented as known patterns. Single-entity concentration in high-traffic "
                "resources is a known pattern — a new instance of it with a different "
                "entity name does not meet this bar."
            ),
            "charter": (
                "## ORGANIZATIONAL CHARTER\n\n"
                "**What Is Already Known:**\n"
                "- Single-entity concentration in high-traffic resources "
                "(entity A controls resource X with 500M monthly interactions)\n"
                "- Power-law distribution in resource usage\n"
                "- Permissive licensing dominance\n\n"
                "**What Doesn't Impress Us:** Another instance of single-entity "
                "concentration. We know the pattern exists."
            ),
            "records": json.dumps([
                {"id": "resource-Q", "monthly_interactions": 650000000,
                 "controller_count": 1, "controller": "entity-B",
                 "version_count": 89, "license": "permissive",
                 "created": "2011-01-05", "last_modified": "2025-08-22"},
            ], indent=2),
            "expected": "SUPPRESS (0 obs)",
        },
        {
            "name": "2_reframeable",
            "description": "Known category but genuinely novel angle exists",
            "role_bar": (
                "Find hidden structural dependencies — resources that create unmapped "
                "risks through indirect relationships not visible in surface metrics. "
                "Single-entity concentration alone does not meet this bar. Hidden "
                "transitive relationships that nobody has mapped DO meet it."
            ),
            "charter": (
                "## ORGANIZATIONAL CHARTER\n\n"
                "**What Is Already Known:**\n"
                "- Single-entity concentration in high-traffic resources\n"
                "- Power-law distribution in resource usage\n\n"
                "**What Impresses Us:** Hidden structural dependencies, "
                "unexpected cross-resource relationships."
            ),
            "records": json.dumps([
                {"id": "resource-M", "monthly_interactions": 851000000,
                 "controller_count": 1, "controller": "entity-C",
                 "version_count": 228, "license": "permissive",
                 "dependent_resources": 14200,
                 "note": "Transitive dependency of 89% of top-100 resources, "
                         "never listed as a direct dependency"},
            ], indent=2),
            "expected": "REFRAME (obs about hidden transitive dependency, not concentration)",
        },
        {
            "name": "3_artifact_contradicted",
            "description": "Extreme value contradicted by other fields",
            "role_bar": (
                "Produce findings backed by trustworthy evidence. A value is "
                "trustworthy when other fields in the same record corroborate it. "
                "A value contradicted by other fields in the same record is not "
                "trustworthy and does not meet this bar as evidence for a finding."
            ),
            "charter": (
                "## ORGANIZATIONAL CHARTER\n\n"
                "**What Impresses Us:** Genuine anomalies with evidence."
            ),
            "records": json.dumps([
                {"id": "resource-T", "monthly_interactions": 0,
                 "controller_count": 2, "controllers": "entity-D, entity-E",
                 "version_count": 4631, "license": "permissive",
                 "created": "2019-01-22", "last_modified": "2026-04-25",
                 "description": "Core parsing utilities used by sibling resources",
                 "sibling_resource_interactions": 457000000},
            ], indent=2),
            "expected": "SUPPRESS or NOTE AS DATA ISSUE (0 substantive obs)",
        },
        {
            "name": "4_genuine_extreme",
            "description": "Extreme value corroborated by other fields",
            "role_bar": (
                "Produce findings backed by trustworthy evidence. A value is "
                "trustworthy when other fields in the same record corroborate it."
            ),
            "charter": (
                "## ORGANIZATIONAL CHARTER\n\n"
                "**What Impresses Us:** Genuine anomalies with evidence."
            ),
            "records": json.dumps([
                {"id": "abandoned-resource", "monthly_interactions": 0,
                 "controller_count": 0, "controllers": "",
                 "version_count": 1, "license": "UNKNOWN",
                 "created": "2015-03-01", "last_modified": "2015-03-01",
                 "description": "test upload"},
            ], indent=2),
            "expected": "KEEP (genuine finding)",
        },
        {
            "name": "5_mixed_issues",
            "description": "Known-category match AND suspect value",
            "role_bar": (
                "Find structural dependencies NOT already documented as known. "
                "Single-entity concentration and automated version inflation are "
                "known patterns. Evidence must be trustworthy — values contradicted "
                "by other fields in the same record do not count."
            ),
            "charter": (
                "## ORGANIZATIONAL CHARTER\n\n"
                "**What Is Already Known:**\n"
                "- Single-entity concentration in high-traffic resources\n"
                "- Automated publishing creating version inflation\n\n"
                "**What Impresses Us:** Hidden structural dependencies, "
                "unexpected failure modes."
            ),
            "records": json.dumps([
                {"id": "resource-F", "monthly_interactions": 0,
                 "controller_count": 1, "controller": "entity-F",
                 "version_count": 3700, "license": "permissive",
                 "created": "2017-05-01", "last_modified": "2026-04-09",
                 "automated_publishing": True,
                 "description": "Core infrastructure component"},
            ], indent=2),
            "expected": "SUPPRESS (both issues: known category + suspect value)",
        },
        {
            "name": "6_clean_novel",
            "description": "Charter-novel, trustworthy values",
            "role_bar": (
                "Find dynamics that contradict surface metrics — cases where "
                "the relationship between resources reveals something unexpected "
                "about how the system actually works."
            ),
            "charter": (
                "## ORGANIZATIONAL CHARTER\n\n"
                "**What Is Already Known:**\n"
                "- Single-entity concentration\n- Power-law distribution\n\n"
                "**What Impresses Us:** Dynamics contradicting surface metrics."
            ),
            "records": json.dumps([
                {"id": "resource-G", "monthly_interactions": 1095000000,
                 "controller_count": 3, "controllers": "org-team",
                 "version_count": 45, "license": "permissive",
                 "parent_resource_interactions": 502000000,
                 "note": "2.18x more interactions than parent resource"},
            ], indent=2),
            "expected": "KEEP (novel shape, trustworthy values)",
        },
        {
            "name": "7_recontextualization_trap",
            "description": "Known pattern plus thin analytical wrapper",
            "role_bar": (
                "Find structural dependencies or failure modes NOT already documented. "
                "Single-entity concentration is a known pattern — adding analytical "
                "framing ('represents systemic risk', 'creates fragility') to a known "
                "pattern does not make it a new finding. It must reveal a genuinely "
                "new structural relationship."
            ),
            "charter": (
                "## ORGANIZATIONAL CHARTER\n\n"
                "**What Is Already Known:**\n"
                "- Single-entity concentration in high-traffic resources "
                "(entity A controls resource X with 500M interactions)\n\n"
                "**What Impresses Us:** How the system actually works vs perception."
            ),
            "records": json.dumps([
                {"id": "resource-H", "monthly_interactions": 426000000,
                 "controller_count": 1, "controller": "entity-G",
                 "version_count": 132, "license": "permissive",
                 "created": "2014-08-29", "last_modified": "2026-04-12",
                 "description": "HTTP client library"},
            ], indent=2),
            "expected": "SUPPRESS (same shape, analytical wrapper is not novelty)",
        },
    ]

    results_b = []

    for scenario in scenarios:
        name = scenario["name"]
        print(f"\n  {'─'*50}")
        print(f"  {name}: {scenario['description']}")
        print(f"  Expected: {scenario['expected']}")

        r = await call_with_v2_prompt(
            role_name="field analyst",
            role_bar=scenario["role_bar"],
            role_heuristic="When uncertain whether a finding meets the bar, err toward not emitting.",
            scope="Analyze the records in scope.",
            purpose="Produce findings that meet your role's bar.",
            parent_context="Manager assigned this scope.",
            workspace_context=scenario["charter"],
            records=scenario["records"],
            budget=0.25,
            force_resolve="You MUST investigate directly. Do not hire.",
        )

        obs = r["result"].get("observations", [])
        formation = r["result"].get("formation_assessment", {})

        # Check thinking for bar-anchored reasoning
        thinking = r["thinking"].lower()
        bar_aware = any(p in thinking for p in ["bar", "role", "success_bar", "meet the bar", "clear the bar"])

        # Assess
        expected = scenario["expected"].lower()
        if "suppress" in expected:
            correct = len(obs) == 0
        elif "reframe" in expected:
            correct = len(obs) > 0  # Has output but reframed
        elif "keep" in expected:
            correct = len(obs) > 0
        elif "note as data" in expected:
            correct = len(obs) == 0 or all(
                "data" in str(o.get("local_hypothesis", "")).lower() or
                "artifact" in str(o.get("local_hypothesis", "")).lower() or
                "reporting" in str(o.get("local_hypothesis", "")).lower()
                for o in obs
            )
        else:
            correct = True

        status = "✓" if correct else "✗"
        print(f"  {status} Observations: {len(obs)} | Bar-aware: {bar_aware} | Cost: ${r['cost']:.4f}")

        if obs:
            for o in obs[:2]:
                print(f"    - [{o.get('signal_strength', '?')}] {str(o.get('raw_evidence', ''))[:100]}")

        results_b.append({
            "name": name, "expected": scenario["expected"],
            "obs_count": len(obs), "correct": correct,
            "bar_aware": bar_aware, "cost": r["cost"],
        })

    return results_b


# =============================================================
# CATEGORY C: First-node organizational design
# =============================================================

async def test_c_org_design():
    """First node receives a charter, produces an organization."""
    print("\n" + "="*60)
    print("CATEGORY C: First-Node Organizational Design")
    print("="*60)

    charter = (
        "# ORGANIZATIONAL CHARTER\n\n"
        "We have access to 100,000 records spanning an entire ecosystem. "
        "Our mission is to find what experienced practitioners don't know.\n\n"
        "**What Is Already Known:**\n"
        "- Resource usage follows a power-law distribution\n"
        "- Most resources have single controllers\n"
        "- Permissive licensing dominates\n"
        "- Automated systems inflate version counts\n\n"
        "**What Impresses Us:** Hidden structural dependencies, unexpected "
        "failure modes, dynamics that contradict surface metrics. Named "
        "entities, exact figures, traceable evidence.\n\n"
        "**What Doesn't Impress Us:** Restating known patterns with different "
        "entity names. Vague observations without specific evidence.\n\n"
        "**Stakes:** This ecosystem underpins critical systems globally. "
        "Missing a structural fragility means missing a systemic risk."
    )

    records = json.dumps([
        {"id": f"record-{i}", "monthly_interactions": [500000000, 300000000, 100000, 0, 851000000][i % 5],
         "controller_count": [1, 3, 1, 0, 1][i % 5],
         "version_count": [114, 45, 4631, 1, 228][i % 5]}
        for i in range(20)
    ], indent=2)

    results_c = []

    # Run twice to check stability
    for run_idx in range(2):
        print(f"\n  Run {run_idx + 1}/2:")

        r = await call_with_v2_prompt(
            role_name="engagement lead",
            role_bar=(
                "Design and staff an organization that produces findings meeting "
                "the charter's standards. Success means: the team covers the "
                "engagement's territory without gaps or overlaps, each hire has "
                "a concrete bar, and findings would satisfy the charter author."
            ),
            role_heuristic=(
                "When uncertain whether to investigate directly or hire, ask: "
                "does this scope require distinct kinds of work?"
            ),
            scope=charter,
            purpose="Design and execute the investigation the charter demands.",
            parent_context="You are the first node. The charter is your engagement directive.",
            workspace_context=f"## ORGANIZATIONAL CHARTER\n\n{charter}",
            records=records,
            budget=2.00,
        )

        formation = r["result"].get("formation_assessment", {})
        decision = formation.get("decision", "?")
        hires = r["result"].get("hire_directives", [])

        print(f"  Decision: {decision}")
        print(f"  Hires: {len(hires)}")

        hire_names = []
        for h in hires:
            hr = h.get("role", {})
            name = hr.get("name", "?")
            bar = hr.get("success_bar", "?")[:100]
            budget_h = h.get("budget", 0)
            hire_names.append(name)
            print(f"    - {name} (${budget_h:.2f}): {bar}")

        results_c.append({
            "run": run_idx + 1,
            "decision": decision,
            "hire_count": len(hires),
            "hire_names": hire_names,
            "cost": r["cost"],
        })

    # Check stability: are the two runs similar in shape?
    if len(results_c) == 2:
        r1, r2 = results_c
        same_decision = r1["decision"] == r2["decision"]
        similar_count = abs(r1["hire_count"] - r2["hire_count"]) <= 1
        print(f"\n  Stability: decision={'same' if same_decision else 'DIFFERENT'}, "
              f"hire count={'similar' if similar_count else 'DIFFERENT'} "
              f"({r1['hire_count']} vs {r2['hire_count']})")

    return results_c


async def main():
    print("ROLE-AUTHORING PATH — Offline Validation")
    print("="*60)
    print("First pass. No iteration.")

    # Category A
    results_a = await test_a_formation_assessment()

    # Category B
    results_b = await test_b_role_emission()

    # Category C
    results_c = await test_c_org_design()

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    a_correct = sum(1 for r in results_a if r["correct"])
    print(f"\n  A. Formation assessment: {a_correct}/{len(results_a)}")
    for r in results_a:
        s = "✓" if r["correct"] else "✗"
        print(f"    {s} ${r['budget']:.2f} → {r['actual']} (expected {r['expected']})")

    b_correct = sum(1 for r in results_b if r["correct"])
    ddj_baseline = {
        "1_shape_leakage": True, "2_reframeable": True,
        "3_artifact_contradicted": False, "4_genuine_extreme": True,
        "5_mixed_issues": False, "6_clean_novel": True,
        "7_recontextualization_trap": False,
    }
    print(f"\n  B. Role-anchored emission: {b_correct}/{len(results_b)} (DDJ baseline: 4/7, S+C: 4/7)")
    for r in results_b:
        s = "✓" if r["correct"] else "✗"
        ddj = "✓" if ddj_baseline.get(r["name"], False) else "✗"
        change = ""
        if not ddj_baseline.get(r["name"], False) and r["correct"]:
            change = " ← FIXED"
        elif ddj_baseline.get(r["name"], False) and not r["correct"]:
            change = " ← REGRESSED"
        print(f"    {s} {r['name']:30s} | {r['obs_count']} obs | bar-aware: {r['bar_aware']} | DDJ: {ddj}{change}")

    print(f"\n  C. Org design: {len(results_c)} runs")
    for r in results_c:
        print(f"    Run {r['run']}: {r['decision']}, {r['hire_count']} hires ({', '.join(r['hire_names'][:5])})")

    total_cost = (sum(r["cost"] for r in results_a) +
                  sum(r["cost"] for r in results_b) +
                  sum(r["cost"] for r in results_c))
    print(f"\n  Total cost: ${total_cost:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
