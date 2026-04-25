"""Build D offline validation — Reader Test calibration.

Score known findings to verify the scorer works:
1. Known-good: genuinely novel finding (should score YES)
2. Known-bad: leakage finding restating known pattern (should score NO)
3. Mixed: partially novel finding (should score MARGINAL or YES)
4. Data artifact: finding built on suspect data (should score NO)
"""

import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/build_d_calibration")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# A realistic charter for calibration
CALIBRATION_CHARTER = """\
# ORGANIZATIONAL CHARTER

We have access to 100,000+ records in a software ecosystem.

**What Is Already Known:**
- Single-entity concentration in critical resources (entity A controls resource X with 500M+ interactions)
- Power-law distribution in resource usage
- Permissive licensing dominance (MIT/ISC/Apache)
- Automated publishing creating version inflation in corporate resources
- Most resources have single controllers
- Framework-specific clustering patterns

**What Impresses Us:** Hidden structural dependencies, unexpected failure modes,
dynamics that contradict surface metrics. Named entities, exact figures, traceable evidence.

**What Doesn't Impress Us:** Another instance of single-entity concentration,
power-law confirmation, generic category observations without specific evidence.
"""


CALIBRATION_FINDINGS = [
    {
        "name": "known_good_novel",
        "finding": {
            "summary": (
                "Resource-M (851M monthly interactions) is a transitive dependency of "
                "89% of top-100 resources but never appears as a direct dependency in any "
                "of them. Controller entity-C is the sole controller. This hidden dependency "
                "creates an unmapped chokepoint — if resource-M fails, 89 of the top 100 "
                "resources break, but none of their dependency manifests list it."
            ),
            "evidence": (
                "resource-M: monthly_interactions=851000000, controller_count=1, "
                "dependent_resources=14200, note='transitive dependency of 89% of "
                "top-100 resources, never listed as direct dependency'. Verified by "
                "checking dependency manifests of top-100 resources — none list resource-M."
            ),
            "validation_status": "confirmed_with_caveats",
        },
        "expected_score": "yes",
        "why": "Hidden transitive dependency affecting 89% of top resources — genuinely novel structural finding",
    },
    {
        "name": "known_bad_leakage",
        "finding": {
            "summary": (
                "Resource-X has 580M monthly interactions and is maintained by a single "
                "controller (entity-A). This represents extreme concentration risk in "
                "critical ecosystem infrastructure."
            ),
            "evidence": (
                "resource-X: monthly_interactions=580261537, controller_count=1, "
                "controller='entity-A', version_count=114, license='MIT'"
            ),
            "validation_status": "confirmed",
        },
        "expected_score": "no",
        "why": "Exact match for charter's known pattern — single-entity concentration in critical resources",
    },
    {
        "name": "mixed_partially_novel",
        "finding": {
            "summary": (
                "Resource-G (a utility) has 2.18x more interactions than its parent "
                "resource-F (1.095B vs 502M). This inversion suggests hidden transitive "
                "consumption patterns where the utility is consumed through paths not "
                "visible in direct dependency graphs."
            ),
            "evidence": (
                "resource-G: monthly_interactions=1095000000, parent resource-F "
                "monthly_interactions=502000000, ratio=2.18x. resource-G is described "
                "as a utility for resource-F."
            ),
            "validation_status": "confirmed_with_caveats",
        },
        "expected_score": "marginal",
        "why": "The utility-exceeds-parent pattern is partially known but the specific 2.18x ratio and hidden transitive consumption angle adds new quantification",
    },
    {
        "name": "data_artifact",
        "finding": {
            "summary": (
                "Resource-T shows 0 monthly interactions despite having 4,631 versions "
                "and being actively maintained (last modified yesterday). This suggests "
                "the resource is critical infrastructure that is invisible to standard metrics."
            ),
            "evidence": (
                "resource-T: monthly_interactions=0, version_count=4631, "
                "last_modified=2026-04-25, controller_count=2, "
                "sibling_resource_interactions=457000000"
            ),
            "validation_status": "needs_verification",
        },
        "expected_score": "no",
        "why": "Zero interactions contradicted by 4631 versions and active maintenance — this is a data collection artifact, not a genuine finding",
    },
]


async def main():
    print("BUILD D — Reader Test Calibration")
    print("="*60)
    print("First pass. No iteration.")

    from mycelium.reader_test import score_findings

    findings_to_score = [f["finding"] for f in CALIBRATION_FINDINGS]
    scores = await score_findings(CALIBRATION_CHARTER, findings_to_score)

    results = []
    for cal, score in zip(CALIBRATION_FINDINGS, scores):
        name = cal["name"]
        expected = cal["expected_score"]
        actual = score["score"]
        correct = actual == expected

        print(f"\n{'─'*60}")
        print(f"{name}: {cal['why'][:80]}")
        print(f"  Expected: {expected} | Got: {actual} {'✓' if correct else '✗'}")
        print(f"  Reasoning: {score['reasoning'][:200]}")
        print(f"  Practitioner knows: {score['what_practitioner_knows'][:100]}")
        print(f"  What's new: {score['what_is_new'][:100]}")

        results.append({
            "name": name, "expected": expected, "actual": actual,
            "correct": correct, "cost": score["cost"],
        })

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    correct_count = sum(1 for r in results if r["correct"])
    for r in results:
        s = "✓" if r["correct"] else "✗"
        print(f"  {s} {r['name']:25s} | expected={r['expected']:8s} | got={r['actual']:8s}")

    total_cost = sum(r["cost"] for r in results)
    print(f"\n  Score: {correct_count}/{len(results)}")
    print(f"  Cost: ${total_cost:.4f}")

    # Pass criteria
    novel_correct = results[0]["correct"]  # known_good must be YES
    leakage_correct = results[1]["correct"]  # known_bad must be NO
    print(f"\n  Pass criteria:")
    print(f"    Novel finding scored YES: {novel_correct}")
    print(f"    Leakage finding scored NO: {leakage_correct}")
    print(f"    OVERALL: {'PASS' if novel_correct and leakage_correct else 'FAIL'}")

    # Save
    with open(OUT_DIR / "calibration_results.md", "w") as f:
        f.write("# Build D — Reader Test Calibration\n\n")
        f.write(f"Score: {correct_count}/{len(results)}\n")
        f.write(f"Cost: ${total_cost:.4f}\n\n")
        for r, cal, score in zip(results, CALIBRATION_FINDINGS, scores):
            f.write(f"## {r['name']}\n")
            f.write(f"Expected: {r['expected']} | Got: {r['actual']} | {'PASS' if r['correct'] else 'FAIL'}\n")
            f.write(f"Reasoning: {score['reasoning']}\n\n")


if __name__ == "__main__":
    asyncio.run(main())
