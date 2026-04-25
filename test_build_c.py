"""Build C offline validation — Manager Turn 2 bar evaluation.

Three scenarios:
A. Clean met-the-bar — child output clearly meets authored bar
B. Child reasoned poorly — output doesn't meet bar, bar was reasonable
C. Wrong role authored — output fails bar, but bar itself was misaligned
"""

import asyncio
import json
import datetime
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/build_c_traces")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def call_turn2(role_name, role_bar, scope, hire_reports, workspace_context=""):
    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import MANAGER_TURN2_PROMPT_V2

    prompt = MANAGER_TURN2_PROMPT_V2.format(
        budget_remaining=0.30,
        role_name=role_name,
        role_bar=role_bar,
        scope_description=scope,
        workspace_context=workspace_context,
        hire_reports=hire_reports,
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
        "name": "A_met_the_bar",
        "description": "Child output clearly meets authored bar",
        "manager_role": "department lead",
        "manager_bar": "Produce findings that reveal hidden structural relationships with named entities and exact figures.",
        "scope": "Investigate structural relationships in the data.",
        "hire_reports": (
            "--- HIRE: structural dependency mapper ---\n"
            "AUTHORED BAR: Identify specific instances where resources create hidden "
            "dependencies through indirect relationships. Each finding must name the "
            "resources involved, quantify the relationship, and explain why it was hidden.\n"
            "AUTHORED HEURISTIC: Favor depth on fewer relationships over broad surveys.\n"
            "SCOPE: Map indirect dependency chains.\n"
            "OBSERVATIONS (3):\n"
            "  Observation 1 [data_originated_novel]: resource-M (851M interactions) is a "
            "transitive dependency of 89% of top-100 resources but never appears as a "
            "direct dependency. Controller: entity-C, 228 versions.\n"
            "  Observation 2 [data_originated_novel]: resource-K (325M interactions) serves "
            "as hidden infrastructure for 14,200 dependent resources through a chain: "
            "resource-K → resource-L → resource-N → consumer resources.\n"
            "  Observation 3 [data_originated_novel]: resource-P (99M interactions) has 3x "
            "more interactions than its parent resource-O (33M), indicating consumption "
            "patterns invisible to surface metrics.\n"
            "SELF-EVALUATION: bar_met=True, quality=high\n"
            "  Follow-up threads flagged:\n"
            "    - Map complete blast radius of resource-M compromise\n"
        ),
        "expected_classification": "MET",
    },
    {
        "name": "B_poor_reasoning",
        "description": "Output doesn't meet bar — bar was reasonable, child fell short",
        "manager_role": "department lead",
        "manager_bar": "Produce findings that reveal hidden structural relationships with named entities and exact figures.",
        "scope": "Investigate structural relationships in the data.",
        "hire_reports": (
            "--- HIRE: structural dependency mapper ---\n"
            "AUTHORED BAR: Identify specific instances where resources create hidden "
            "dependencies through indirect relationships. Each finding must name the "
            "resources involved, quantify the relationship, and explain why it was hidden.\n"
            "AUTHORED HEURISTIC: Favor depth on fewer relationships over broad surveys.\n"
            "SCOPE: Map indirect dependency chains.\n"
            "OBSERVATIONS (2):\n"
            "  Observation 1 [data_originated_confirmatory]: Many resources in this ecosystem "
            "have single controllers. This creates concentration risk.\n"
            "  Observation 2 [confirmatory]: Resources with high interaction counts tend to "
            "have more dependents, following a power-law distribution.\n"
            "SELF-EVALUATION: bar_met=True, quality=medium\n"
        ),
        "expected_classification": "POOR_REASONING",
    },
    {
        "name": "C_wrong_role",
        "description": "Output fails bar, but bar was misaligned with actual territory",
        "manager_role": "department lead",
        "manager_bar": "Produce findings that reveal hidden structural relationships with named entities and exact figures.",
        "scope": "Investigate structural relationships in the data.",
        "hire_reports": (
            "--- HIRE: temporal pattern analyst ---\n"
            "AUTHORED BAR: Identify specific temporal patterns where resource creation, "
            "adoption, or abandonment timing reveals coordinated behavior or hidden "
            "organizational relationships. Each finding must include exact dates, named "
            "resources, and quantified timing correlations.\n"
            "AUTHORED HEURISTIC: Focus on timing clusters that suggest coordination.\n"
            "SCOPE: Analyze temporal patterns in resource lifecycle.\n"
            "OBSERVATIONS (3):\n"
            "  Observation 1 [data_originated_novel]: 47 resources show zero interactions "
            "despite active maintenance (last modified within 30 days, 100+ versions). "
            "Zero values are contradicted by sibling resources showing millions of interactions.\n"
            "  Observation 2 [data_originated_novel]: Resource-T has 4,631 versions and "
            "0 reported interactions. Sibling resource-U has 457M interactions with the same "
            "controllers. This is a measurement artifact, not genuine abandonment.\n"
            "  Observation 3 [data_originated_novel]: 8 resources from the same organization "
            "all show identical last-modified timestamps (2026-04-22), suggesting automated "
            "coordinated releases.\n"
            "SELF-EVALUATION: bar_met=False, quality=high\n"
            "  Follow-up threads flagged:\n"
            "    - Investigate the zero-interaction measurement gap across the ecosystem\n"
        ),
        "expected_classification": "WRONG_ROLE (bar asked for timing correlations, child found measurement artifacts — valuable work but misaligned with authored bar)",
    },
]


async def main():
    print("BUILD C — Manager Turn 2 Offline Validation")
    print("="*60)
    print("First pass. No iteration.")

    results = []

    for scenario in SCENARIOS:
        name = scenario["name"]
        print(f"\n{'─'*60}")
        print(f"SCENARIO {name}: {scenario['description']}")
        print(f"Expected: {scenario['expected_classification']}")
        print(f"{'─'*60}")

        r = await call_turn2(
            role_name=scenario["manager_role"],
            role_bar=scenario["manager_bar"],
            scope=scenario["scope"],
            hire_reports=scenario["hire_reports"],
        )

        evaluations = r["result"].get("hire_evaluations", [])
        continuation = r["result"].get("continuation_decision", {})

        print(f"\n  Cost: ${r['cost']:.4f}")

        for ev in evaluations:
            cls = ev.get("classification", "?")
            reasoning = ev.get("reasoning", "")[:200]
            print(f"  Classification: {cls}")
            print(f"  Reasoning: {reasoning}")

        action = continuation.get("action", "?")
        print(f"  Continuation: {action}")

        # Check for self-evaluation in scenario C
        thinking = r["thinking"].lower()
        self_eval = any(p in thinking for p in [
            "wrong role", "wrong bar", "misaligned", "my bar", "i authored",
            "bar was wrong", "bar didn't match", "authored bar was",
            "bar i set", "role i created",
        ])

        expected = scenario["expected_classification"].split(" ")[0]
        actual = evaluations[0].get("classification", "?") if evaluations else "?"

        correct = actual == expected
        print(f"\n  {'✓' if correct else '✗'} Expected: {expected}, Got: {actual}")
        if name == "C_wrong_role":
            print(f"  Self-evaluation present: {self_eval}")

        results.append({
            "name": name, "expected": expected, "actual": actual,
            "correct": correct, "self_eval": self_eval, "cost": r["cost"],
            "action": action,
        })

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        s = "✓" if r["correct"] else "✗"
        extra = f" | self-eval={r['self_eval']}" if r["name"] == "C_wrong_role" else ""
        print(f"  {s} {r['name']:25s} | expected={r['expected']:15s} | got={r['actual']:15s} | action={r['action']}{extra}")

    total_cost = sum(r["cost"] for r in results)
    print(f"\n  Total cost: ${total_cost:.4f}")

    all_pass = all(r["correct"] for r in results[:2])  # A and B must pass
    c_self_eval = results[2]["self_eval"] if len(results) > 2 else False
    print(f"\n  Pass criteria:")
    print(f"    A+B correct: {all_pass}")
    print(f"    C self-evaluation attempted: {c_self_eval}")
    print(f"    OVERALL: {'PASS' if all_pass and c_self_eval else 'FAIL'}")

    # Save
    with open(OUT_DIR / "results.md", "w") as f:
        f.write("# Build C — Manager Turn 2 Offline Validation\n\n")
        f.write(f"Pass: {all_pass and c_self_eval}\n")
        f.write(f"Cost: ${total_cost:.4f}\n\n")
        for r in results:
            f.write(f"## {r['name']}\n")
            f.write(f"Expected: {r['expected']} | Got: {r['actual']} | Action: {r['action']}\n\n")


if __name__ == "__main__":
    asyncio.run(main())
