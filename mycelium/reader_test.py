"""Reader Test — scores each finding against the charter's standards.

Per-finding score (yes / marginal / no) with reasoning.
The question: would a knowledgeable reader say "I didn't know that"?

Corpus-agnostic — reads the charter from the run and scores against
whatever standards that charter sets.
"""

import json
import anthropic


READER_TEST_PROMPT = """\
You are a knowledgeable practitioner in the domain this investigation covers. \
You have read the organizational charter below. You are now reviewing a finding \
produced by the investigation team.

ORGANIZATIONAL CHARTER:
{charter}

FINDING:
{finding}

EVIDENCE:
{evidence}

VALIDATION STATUS: {validation_status}

---

Score this finding on one question: would a knowledgeable practitioner in this \
domain say "I didn't know that" about this finding?

YES — The finding reveals something a practitioner would not already know. \
It is specific, evidence-backed, and changes understanding of the domain.

MARGINAL — The finding has specific evidence but the conclusion is either \
partially known, weakly supported, or the practitioner would say "I suspected \
that but hadn't seen it quantified."

NO — The finding restates something a knowledgeable practitioner already knows. \
It may have specific data but the insight itself is not novel. The charter's \
"what is already known" section covers this category of finding.

Be honest. Most investigation runs produce mostly NO findings with a few \
MARGINAL and occasionally a YES. That's normal. The bar for YES is high — \
the practitioner is experienced and well-informed.

Return JSON:
{{
    "score": "yes | marginal | no",
    "reasoning": "why this score — reference the charter's standards and what a practitioner would already know",
    "what_practitioner_knows": "the closest thing a practitioner already knows to this finding",
    "what_is_new": "what, if anything, this finding adds beyond existing knowledge"
}}

Respond ONLY with valid JSON, no other text.
"""


async def score_findings(charter: str, findings: list[dict]) -> list[dict]:
    """Score each finding against the charter's standards.

    Args:
        charter: The organizational charter text
        findings: List of finding dicts with at minimum 'summary' and 'evidence'

    Returns:
        List of score dicts with 'finding_id', 'score', 'reasoning'
    """
    client = anthropic.Anthropic()
    scores = []

    for i, finding in enumerate(findings):
        summary = finding.get("summary", finding.get("what_conflicts",
                  finding.get("pattern", str(finding)[:200])))
        evidence = finding.get("evidence", finding.get("evidence_chain", ""))
        if isinstance(evidence, list):
            evidence = "\n".join(str(e) for e in evidence)
        validation = finding.get("validation_status",
                    finding.get("verdict", "not validated"))

        prompt = READER_TEST_PROMPT.format(
            charter=charter,
            finding=summary,
            evidence=str(evidence)[:2000],
            validation_status=validation,
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text
        cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            result = json.loads(raw[start:end]) if start >= 0 and end > start else {
                "score": "no", "reasoning": "parse error"
            }

        scores.append({
            "finding_index": i,
            "finding_summary": str(summary)[:100],
            "score": result.get("score", "no"),
            "reasoning": result.get("reasoning", ""),
            "what_practitioner_knows": result.get("what_practitioner_knows", ""),
            "what_is_new": result.get("what_is_new", ""),
            "cost": cost,
        })

    return scores


def score_run(run_dir: str) -> dict:
    """Score all findings from a completed run. Sync wrapper for pipeline integration."""
    import asyncio
    from pathlib import Path

    run_path = Path(run_dir)

    # Load charter
    charter_path = run_path / "workspace" / "charter.md"
    if not charter_path.exists():
        return {"error": "no charter found", "scores": []}
    charter = charter_path.read_text()

    # Load metrics to find findings
    metrics_path = run_path / "metrics.json"
    if not metrics_path.exists():
        return {"error": "no metrics found", "scores": []}

    with open(metrics_path) as f:
        metrics = json.load(f)

    # Extract findings from the run's synthesis results
    findings = []

    # Try loading from the exploration data
    for node_file in sorted((run_path / "nodes").glob("*.json")):
        with open(node_file) as f:
            node = json.load(f)
        for obs in node.get("observations", []):
            if isinstance(obs, dict) and obs.get("raw_evidence"):
                findings.append({
                    "summary": obs.get("raw_evidence", "")[:200],
                    "evidence": obs.get("raw_evidence", ""),
                    "validation_status": obs.get("signal_strength", "not validated"),
                })

    if not findings:
        return {"error": "no findings to score", "scores": []}

    scores = asyncio.run(score_findings(charter, findings[:10]))  # Cap at 10 for cost

    return {
        "charter_length": len(charter),
        "findings_scored": len(scores),
        "scores": scores,
        "summary": {
            "yes": sum(1 for s in scores if s["score"] == "yes"),
            "marginal": sum(1 for s in scores if s["score"] == "marginal"),
            "no": sum(1 for s in scores if s["score"] == "no"),
        },
        "total_cost": sum(s["cost"] for s in scores),
    }
