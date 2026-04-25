"""Offline validation of SURFACE AND COMMIT observation review step.

Same seven scenarios as DDJ validation. Exercises the updated
NODE_REASONING_PROMPT against fixed synthetic inputs. Prints reasoning
traces and final observations for each scenario.

First pass — no iteration against scenarios. Results are what they are.
"""

import asyncio
import json
import datetime
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/surface_commit_traces")
OUT_DIR.mkdir(parents=True, exist_ok=True)


SCENARIOS = [
    {
        "name": "1_shape_leakage",
        "description": "Record matches charter's known category with different specifics",
        "charter": (
            "## ORGANIZATIONAL CHARTER\n\n"
            "**What Is Already Known:**\n"
            "- Single-entity concentration in high-traffic resources "
            "(entity A controls resource X with 500M monthly interactions)\n"
            "- Power-law distribution in resource usage\n"
            "- Permissive licensing dominance\n\n"
            "**What Impresses Us:** Hidden structural dependencies, "
            "unexpected failure modes, dynamics that contradict surface metrics. "
            "Named entities, exact figures, traceable evidence.\n\n"
            "**What Doesn't Impress Us:** Another instance of single-entity "
            "concentration. We know the pattern exists — don't bring us a "
            "new example of the same shape."
        ),
        "records": json.dumps([
            {"id": "resource-Q", "monthly_interactions": 650000000,
             "controller_count": 1, "controller": "entity-B",
             "version_count": 89, "license": "permissive",
             "created": "2011-01-05", "last_modified": "2025-08-22"},
        ], indent=2),
        "purpose": "Investigate concentration patterns in high-traffic resources",
        "expected": "SUPPRESS — same shape as charter's known category, different specifics",
    },
    {
        "name": "2_reframeable",
        "description": "Superficially matches known category but has a genuinely novel angle",
        "charter": (
            "## ORGANIZATIONAL CHARTER\n\n"
            "**What Is Already Known:**\n"
            "- Single-entity concentration in high-traffic resources "
            "(entity A controls resource X)\n"
            "- Power-law distribution in resource usage\n\n"
            "**What Impresses Us:** Hidden structural dependencies, "
            "unexpected cross-resource relationships that create unmapped risks."
        ),
        "records": json.dumps([
            {"id": "resource-M", "monthly_interactions": 851000000,
             "controller_count": 1, "controller": "entity-C",
             "version_count": 228, "license": "permissive",
             "created": "2014-01-20", "last_modified": "2025-05-14",
             "dependent_resources": 14200,
             "note": "This resource is a transitive dependency of 89% of all "
                     "resources in the top-100, but is never listed as a direct dependency"},
        ], indent=2),
        "purpose": "Investigate hidden dependency chains in critical resources",
        "expected": "REFRAME — single-controller is known, but hidden transitive dependency affecting 89% of top-100 is novel",
    },
    {
        "name": "3_artifact_contradicted",
        "description": "Extreme value contradicted by other fields in same record",
        "charter": (
            "## ORGANIZATIONAL CHARTER\n\n"
            "**What Is Already Known:**\n"
            "- Resource usage follows power-law patterns\n\n"
            "**What Impresses Us:** Genuine anomalies with evidence."
        ),
        "records": json.dumps([
            {"id": "resource-T", "monthly_interactions": 0,
             "controller_count": 2, "controllers": "entity-D, entity-E",
             "version_count": 4631, "license": "permissive",
             "created": "2019-01-22", "last_modified": "2026-04-23",
             "description": "Core parsing utilities used by sibling resources",
             "sibling_resource_interactions": 457000000},
        ], indent=2),
        "purpose": "Investigate resources showing zero interactions",
        "expected": "SUPPRESS or CAVEAT — zero interactions contradicted by 4631 versions, recent modification, active controllers, sibling at 457M",
    },
    {
        "name": "4_genuine_extreme",
        "description": "Extreme value corroborated by other fields",
        "charter": (
            "## ORGANIZATIONAL CHARTER\n\n"
            "**What Is Already Known:**\n"
            "- Resource usage follows power-law patterns\n\n"
            "**What Impresses Us:** Genuine anomalies with evidence."
        ),
        "records": json.dumps([
            {"id": "abandoned-resource", "monthly_interactions": 0,
             "controller_count": 0, "controllers": "",
             "version_count": 1, "license": "UNKNOWN",
             "created": "2015-03-01", "last_modified": "2015-03-01",
             "description": "test upload"},
        ], indent=2),
        "purpose": "Investigate resources showing zero interactions",
        "expected": "KEEP — zero interactions corroborated by 0 controllers, 1 version, no updates since creation",
    },
    {
        "name": "5_mixed_issues",
        "description": "Known-category match AND suspect value in same record",
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
        "purpose": "Investigate anomalous resources",
        "expected": "SUPPRESS — both issues: single-controller is known category, zero interactions contradicted by 3700 versions + recent activity",
    },
    {
        "name": "6_clean_novel",
        "description": "Charter-novel finding, trustworthy values — should pass through",
        "charter": (
            "## ORGANIZATIONAL CHARTER\n\n"
            "**What Is Already Known:**\n"
            "- Single-entity concentration in high-traffic resources\n"
            "- Power-law distribution in resource usage\n\n"
            "**What Impresses Us:** Hidden structural dependencies, "
            "unexpected cross-resource relationships, dynamics that "
            "contradict surface metrics."
        ),
        "records": json.dumps([
            {"id": "resource-G", "monthly_interactions": 1095000000,
             "controller_count": 3, "controllers": "org-team",
             "version_count": 45, "license": "permissive",
             "created": "2016-08-01", "last_modified": "2026-04-17",
             "parent_resource_interactions": 502000000,
             "note": "This utility resource has 2.18x more interactions than "
                     "the parent resource it supports, suggesting hidden "
                     "transitive consumption patterns"},
        ], indent=2),
        "purpose": "Investigate interaction pattern anomalies",
        "expected": "KEEP — novel shape (utility exceeds parent 2.18x), trustworthy values, not covered by charter categories",
    },
    {
        "name": "7_recontextualization_trap",
        "description": "Known pattern plus thin analytical wrapper — tempting to keep",
        "charter": (
            "## ORGANIZATIONAL CHARTER\n\n"
            "**What Is Already Known:**\n"
            "- Single-entity concentration in high-traffic resources "
            "(entity A controls resource X with 500M interactions)\n\n"
            "**What Impresses Us:** Findings that reveal how the system "
            "actually works versus how we think it works."
        ),
        "records": json.dumps([
            {"id": "resource-H", "monthly_interactions": 426000000,
             "controller_count": 1, "controller": "entity-G",
             "version_count": 132, "license": "permissive",
             "created": "2014-08-29", "last_modified": "2026-04-12",
             "description": "HTTP client library"},
        ], indent=2),
        "purpose": "Investigate concentration risks",
        "expected": "SUPPRESS — same shape as known category. Temptation is to add 'represents supply chain risk' wrapper, but that's recontextualization, not novelty",
    },
]


async def run_scenario(scenario: dict, prompt_template: str) -> dict:
    """Run one scenario and return traces."""
    briefing_context = (
        scenario["charter"] + "\n\n"
        "## RULES OF ENGAGEMENT\n\n"
        "Follow the directive above. Report only what advances the mission.\n\n"
    )

    prompt = prompt_template.format(
        current_date=datetime.date.today().isoformat(),
        purpose=scenario["purpose"],
        parent_context="Investigation scope assigned by program office",
        briefing_context=briefing_context,
        scope_description=scenario["purpose"],
        lenses="concentration_risk, hidden_dependencies, data_quality",
        filter_schema="(not applicable)",
        budget_remaining=0.25,
        total_budget=2.0,
        budget_pct=12.5,
        budget_stage="Early investigation.",
        capacity_context="",
        segment_context="",
        parent_pool_remaining=1.50,
        phase_remaining=1.50,
        current_depth=0,
        max_depth=6,
        leaf_viable_envelope=0.12,
        depth_guidance="You may spawn children if threads warrant it.",
        doc_count=len(json.loads(scenario["records"])),
        fetched_data=scenario["records"],
        force_resolve="You MUST resolve. Do not spawn children. Produce observations directly.",
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
        result = json.loads(output_text[start:end]) if start >= 0 and end > start else {"observations": []}

    return {
        "thinking": thinking,
        "observations": result.get("observations", []),
        "cost": cost,
    }


def find_surface_commit_traces(thinking: str) -> str:
    """Extract the surface-and-commit reasoning section from thinking."""
    text_lower = thinking.lower()

    # Look for the section where surface/commit reasoning happens
    markers = ["surface and commit", "surface:", "before you emit",
               "tension", "commit:"]
    best_start = -1
    for m in markers:
        idx = text_lower.find(m)
        if idx >= 0 and (best_start < 0 or idx < best_start):
            best_start = idx

    if best_start < 0:
        # Fallback: look for any tension-related reasoning
        for m in ["tension", "charter", "directive"]:
            idx = text_lower.find(m)
            if idx >= 0:
                best_start = max(0, idx - 50)
                break

    if best_start < 0:
        return "(no surface/commit reasoning found)"

    # Extract a generous chunk from that point
    return thinking[best_start:best_start + 1500]


async def main():
    print("SURFACE AND COMMIT — Offline Validation")
    print("="*60)
    print("First pass. No iteration. Same 7 scenarios as DDJ validation.")
    print()

    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import NODE_REASONING_PROMPT

    results = []
    total_cost = 0

    for scenario in SCENARIOS:
        name = scenario["name"]
        print(f"\n{'─'*60}")
        print(f"SCENARIO: {name}")
        print(f"  {scenario['description']}")
        print(f"  Expected: {scenario['expected']}")
        print(f"{'─'*60}")

        result = await run_scenario(scenario, NODE_REASONING_PROMPT)
        total_cost += result["cost"]
        obs = result["observations"]

        # Extract surface/commit trace
        sc_trace = find_surface_commit_traces(result["thinking"])

        # Check for structural markers
        thinking_lower = result["thinking"].lower()
        has_surface = "surface" in thinking_lower and "tension" in thinking_lower
        has_commit = "commit" in thinking_lower
        has_both = has_surface and has_commit

        print(f"\n  Cost: ${result['cost']:.4f}")
        print(f"  Observations emitted: {len(obs)}")
        print(f"  Surface/commit structure: {'YES' if has_both else 'PARTIAL' if has_surface or has_commit else 'NO'}")

        # Print the trace
        print(f"\n  --- Surface/Commit Trace ---")
        for line in sc_trace[:1200].split("\n"):
            if line.strip():
                print(f"    {line.strip()[:120]}")

        # Print observations
        if obs:
            for k, o in enumerate(obs):
                raw = str(o.get("raw_evidence", ""))[:140]
                sig = o.get("signal_strength", "?")
                print(f"\n  Obs {k+1} [{sig}]: {raw}")
        else:
            print(f"\n  (no observations emitted)")

        # Assess outcome
        expected_lower = scenario["expected"].lower()
        if "suppress" in expected_lower:
            outcome = "CORRECT" if len(obs) == 0 else f"INCORRECT — emitted {len(obs)} obs, should have suppressed"
        elif "reframe" in expected_lower:
            outcome = "CORRECT" if len(obs) > 0 else "INCORRECT — should have reframed (some output)"
        elif "keep" in expected_lower:
            outcome = "CORRECT" if len(obs) > 0 else "INCORRECT — should have kept"
        elif "caveat" in expected_lower:
            # Suppress or caveat both acceptable
            if len(obs) == 0:
                outcome = "CORRECT — suppressed suspect data"
            else:
                # Check if caveated
                any_caveated = any("artifact" in str(o.get("local_hypothesis", "")).lower() or
                                   "suspect" in str(o.get("local_hypothesis", "")).lower() or
                                   "reporting" in str(o.get("local_hypothesis", "")).lower() or
                                   "data quality" in str(o.get("local_hypothesis", "")).lower()
                                   for o in obs)
                outcome = "CORRECT — caveated" if any_caveated else f"INCORRECT — emitted {len(obs)} obs without caveat"
        else:
            outcome = "UNCLEAR"

        print(f"\n  ASSESSMENT: {outcome}")

        results.append({
            "name": name,
            "expected": scenario["expected"],
            "observations_emitted": len(obs),
            "has_surface_commit": has_both,
            "outcome": outcome,
        })

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY — compared to DDJ baseline")
    print(f"{'='*60}")
    print(f"Total cost: ${total_cost:.4f}")
    print()

    ddj_results = {
        "1_shape_leakage": "CORRECT",
        "2_reframeable": "CORRECT",
        "3_artifact_contradicted": "INCORRECT",
        "4_genuine_extreme": "CORRECT",
        "5_mixed_issues": "INCORRECT",
        "6_clean_novel": "CORRECT",
        "7_recontextualization_trap": "INCORRECT",
    }

    correct = 0
    for r in results:
        is_correct = "CORRECT" in r["outcome"]
        if is_correct:
            correct += 1
        status = "✓" if is_correct else "✗"
        ddj = ddj_results.get(r["name"], "?")
        change = ""
        if "INCORRECT" in ddj and is_correct:
            change = " ← FIXED"
        elif "CORRECT" in ddj and not is_correct:
            change = " ← REGRESSED"
        sc = "S+C" if r["has_surface_commit"] else "no"
        print(f"  {status} {r['name']:30s} | {r['observations_emitted']} obs | S+C: {sc:3s} | DDJ: {ddj:10s} → {r['outcome']}{change}")

    print(f"\n  Score: {correct}/7 (DDJ was 4/7)")

    # Save full output
    out_path = OUT_DIR / "summary.md"
    with open(out_path, "w") as f:
        f.write(f"# Surface and Commit — Offline Validation\n\n")
        f.write(f"Score: {correct}/7 (DDJ baseline: 4/7)\n")
        f.write(f"Total cost: ${total_cost:.4f}\n\n")
        f.write("| Scenario | Obs | S+C | DDJ | S+C Result |\n|---|---|---|---|---|\n")
        for r in results:
            ddj = ddj_results.get(r["name"], "?")
            f.write(f"| {r['name']} | {r['observations_emitted']} | "
                    f"{'yes' if r['has_surface_commit'] else 'no'} | {ddj} | {r['outcome']} |\n")

    print(f"\n  Saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
