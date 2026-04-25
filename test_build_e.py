"""Build E offline validation — Synthesis as Authored Role.

Scenario: a set of observations including novel findings and known-pattern
restatements. Run synthesis WITH and WITHOUT an authored role. Verify the
role-authored synthesis produces better output.
"""

import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/build_e_traces")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Synthetic observations — mix of genuinely novel and known-pattern
INVESTIGATOR_REPORTS = """\
INVESTIGATOR 1 (Hidden Infrastructure Analyst):
- Obs 1 [data_originated_novel]: resource-M (851M interactions) is a transitive dependency of 89% of top-100 resources but never listed as direct dependency. Controller: entity-C.
- Obs 2 [data_originated_confirmatory]: resource-X (580M interactions) has single controller entity-A. Concentration risk.
- Obs 3 [data_originated_novel]: resource-P (99M interactions) has 3x more interactions than parent resource-O (33M), suggesting hidden consumption patterns.

INVESTIGATOR 2 (Maintainer Control Analyst):
- Obs 1 [data_originated_confirmatory]: entity-A controls resource-X with 580M interactions. Single point of failure.
- Obs 2 [data_originated_novel]: entity-D appears across 7 seemingly unrelated resources totaling 2.1B interactions, but is only listed as controller on 2 of them. Hidden cross-resource control.
- Obs 3 [data_originated_novel]: 47 resources show 0 interactions despite 100+ versions and recent updates. Other fields contradict the zero value — suspected measurement artifact.

INVESTIGATOR 3 (Ecosystem Dynamics Analyst):
- Obs 1 [data_originated_confirmatory]: Power-law distribution in interactions — top 10 resources account for 70% of total. Known pattern.
- Obs 2 [data_originated_novel]: Resources created in 2016-2017 show 3x higher abandonment rates than those from any other period, correlating with a specific framework migration wave.
- Obs 3 [data_originated_novel]: resource-G (utility) has 2.18x more interactions than its parent resource-F, suggesting transitive consumption invisible to surface metrics.
"""

CHARTER = """\
## ORGANIZATIONAL CHARTER
**What Is Already Known:**
- Single-entity concentration in critical resources (entity A controls resource X)
- Power-law distribution in resource usage
- Most resources have single controllers

**What Impresses Us:** Hidden structural dependencies, unexpected failure modes,
dynamics that contradict surface metrics. Named entities, exact figures, traceable evidence.

**What Doesn't Impress Us:** Another instance of single-entity concentration,
power-law confirmation, generic observations.
"""


async def run_synthesis_generic():
    """Synthesis WITHOUT authored role — uses existing generic prompt."""
    from mycelium import prompts
    prompts.set_version("v2")

    import anthropic
    prompt = prompts.SYNTHESIS_PROMPT.format(
        investigator_reports=INVESTIGATOR_REPORTS,
        lenses="concentration_risk, hidden_dependencies, data_quality",
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
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

    return result, cost


async def run_synthesis_authored():
    """Synthesis WITH authored role."""
    from mycelium import prompts
    prompts.set_version("v2")

    import anthropic
    prompt = prompts.SYNTHESIS_PROMPT_V2.format(
        role_name="cross-cutting pattern analyst",
        role_bar=(
            "Surface only patterns that emerge from combining multiple investigators' "
            "findings and that would not be visible from any single investigator's output. "
            "Specifically: contradictions between investigators, hidden connections linking "
            "seemingly unrelated findings, and structural vulnerabilities that span multiple "
            "areas. Do NOT include single-entity concentration patterns — these are known. "
            "Do NOT include power-law observations — these are known. Do NOT build findings "
            "on values that other fields in the same record contradict."
        ),
        role_heuristic=(
            "When uncertain whether a cross-cutting pattern is novel or a restatement "
            "of known categories, check: could a practitioner have predicted this pattern "
            "without seeing the investigation data? If yes, it's not novel enough."
        ),
        workspace_context=CHARTER,
        investigator_reports=INVESTIGATOR_REPORTS,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
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

    return result, cost


def analyze_synthesis(result: dict, label: str):
    """Analyze synthesis output for known-pattern leakage."""
    reinforced = result.get("reinforced", [])
    contradictions = result.get("contradictions", [])
    cross_cutting = result.get("cross_cutting_patterns", [])
    questions = result.get("discovered_questions", [])

    total_findings = len(reinforced) + len(contradictions) + len(cross_cutting)

    # Check for known-pattern leakage
    known_patterns = 0
    novel_patterns = 0
    for item in reinforced + cross_cutting:
        pattern = str(item.get("pattern", "")).lower()
        if any(kp in pattern for kp in [
            "single controller", "single entity", "single maintainer",
            "concentration risk", "power law", "power-law",
        ]):
            known_patterns += 1
        else:
            novel_patterns += 1

    print(f"\n  [{label}]")
    print(f"    Reinforced: {len(reinforced)}, Contradictions: {len(contradictions)}, Cross-cutting: {len(cross_cutting)}")
    print(f"    Total findings: {total_findings}")
    print(f"    Known-pattern leakage: {known_patterns}")
    print(f"    Novel patterns: {novel_patterns}")
    print(f"    Questions discovered: {len(questions)}")

    # Print findings
    for i, item in enumerate(reinforced[:3]):
        print(f"    Reinforced {i+1}: {str(item.get('pattern', ''))[:100]}")
    for i, item in enumerate(contradictions[:3]):
        print(f"    Contradiction {i+1}: {str(item.get('what_conflicts', ''))[:100]}")
    for i, item in enumerate(cross_cutting[:3]):
        print(f"    Cross-cutting {i+1}: {str(item.get('pattern', ''))[:100]}")

    return {
        "total": total_findings,
        "known_patterns": known_patterns,
        "novel_patterns": novel_patterns,
        "questions": len(questions),
    }


async def main():
    print("BUILD E — Synthesis as Authored Role")
    print("="*60)
    print("Comparing generic vs role-authored synthesis. First pass.")

    # Run both
    generic_result, generic_cost = await run_synthesis_generic()
    authored_result, authored_cost = await run_synthesis_authored()

    print(f"\n  Generic synthesis cost: ${generic_cost:.4f}")
    generic_analysis = analyze_synthesis(generic_result, "GENERIC (no role)")

    print(f"\n  Authored synthesis cost: ${authored_cost:.4f}")
    authored_analysis = analyze_synthesis(authored_result, "AUTHORED (with role)")

    # Comparison
    print(f"\n{'='*60}")
    print("COMPARISON")
    print(f"{'='*60}")
    print(f"  Known-pattern leakage:  generic={generic_analysis['known_patterns']}  authored={authored_analysis['known_patterns']}")
    print(f"  Novel patterns:         generic={generic_analysis['novel_patterns']}  authored={authored_analysis['novel_patterns']}")
    print(f"  Total findings:         generic={generic_analysis['total']}  authored={authored_analysis['total']}")

    # Pass criteria
    less_leakage = authored_analysis["known_patterns"] <= generic_analysis["known_patterns"]
    has_output = authored_analysis["total"] > 0
    print(f"\n  Pass criteria:")
    print(f"    Less or equal known-pattern leakage: {less_leakage}")
    print(f"    Authored synthesis has output: {has_output}")
    print(f"    OVERALL: {'PASS' if less_leakage and has_output else 'FAIL'}")

    # Save
    with open(OUT_DIR / "comparison.md", "w") as f:
        f.write("# Build E — Synthesis Comparison\n\n")
        f.write(f"Generic: {generic_analysis}\n")
        f.write(f"Authored: {authored_analysis}\n")
        f.write(f"Pass: {less_leakage and has_output}\n")

    total_cost = generic_cost + authored_cost
    print(f"\n  Total cost: ${total_cost:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
