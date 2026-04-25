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

Score this finding on TWO dimensions:

1. FACTUAL NOVELTY — would a practitioner say "I didn't know that fact"?

YES — The underlying factual observation is something a practitioner would \
not already know. Specific data that has not been publicly documented.

MARGINAL — The factual observation is partially known or suspected but now \
quantified with specific evidence the practitioner hadn't seen.

NO — The factual observation restates something practitioners already know. \
The charter's "what is already known" section covers this category.

2. INTERPRETIVE CERTAINTY — how strongly does the interpretation follow \
from the facts?

HIGH — The interpretation is well-supported by the cited evidence. Few \
alternative explanations fit the facts.

MEDIUM — The interpretation is plausible given the evidence but other \
explanations could also fit.

LOW — The interpretation makes a large leap from the cited evidence. \
Speculative.

COMBINED SCORE: derived from both dimensions.
- YES: factual_novelty=YES and interpretive_certainty=HIGH or MEDIUM
- YES_FACTUAL: factual_novelty=YES but interpretive_certainty=LOW \
  (the fact is novel even if the interpretation is uncertain)
- MARGINAL: factual_novelty=MARGINAL, or YES with LOW certainty
- NO: factual_novelty=NO regardless of interpretation

Return JSON:
{{
    "factual_novelty": "yes | marginal | no",
    "factual_novelty_reasoning": "what fact is claimed, is it known or novel",
    "interpretive_certainty": "high | medium | low",
    "interpretive_certainty_reasoning": "how well the interpretation follows from the facts",
    "combined_score": "yes | yes_factual | marginal | no",
    "reasoning": "overall assessment",
    "what_practitioner_knows": "closest known fact to this finding",
    "what_is_new": "what this finding adds beyond existing knowledge"
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
                "combined_score": "no", "reasoning": "parse error"
            }

        combined = result.get("combined_score", result.get("score", "no"))
        scores.append({
            "finding_index": i,
            "finding_summary": str(summary)[:100],
            "score": combined,
            "factual_novelty": result.get("factual_novelty", ""),
            "interpretive_certainty": result.get("interpretive_certainty", ""),
            "reasoning": result.get("reasoning", ""),
            "what_practitioner_knows": result.get("what_practitioner_knows", ""),
            "what_is_new": result.get("what_is_new", ""),
            "cost": cost,
        })

    return scores


async def score_run(run_dir: str) -> dict:
    """Score all findings from a completed run."""
    import re
    from pathlib import Path

    run_path = Path(run_dir)

    # Load charter
    charter_path = run_path / "workspace" / "charter.md"
    if not charter_path.exists():
        return {"error": "no charter found", "scores": []}
    charter = charter_path.read_text()

    # Extract findings from the report (Tier 3-5 sections)
    report_path = run_path / "report.md"
    if not report_path.exists():
        return {"error": "no report found", "scores": []}

    report = report_path.read_text()
    findings = []

    # Parse Tier 3, 4, 5 findings from report markdown
    # Each finding starts with ### Finding N.N: or ### Pattern N.N:
    finding_pattern = re.compile(
        r'###\s+(?:Finding|Pattern)\s+\d+\.\d+[:\s]*(.*?)(?=\n###|\n## |\Z)',
        re.DOTALL
    )
    for match in finding_pattern.finditer(report):
        block = match.group(0)
        # Extract title and evidence from the block
        lines = block.strip().split('\n')
        title = lines[0] if lines else ""
        # Collect everything as evidence
        evidence_lines = [l for l in lines[1:] if l.strip() and not l.startswith('**Impact')]
        findings.append({
            "summary": title[:300],
            "evidence": "\n".join(evidence_lines[:15]),
            "validation_status": "validated",
        })

    # Fallback: if no structured findings found, try broader section parsing
    if not findings:
        for section in ["## Tier 3", "## Tier 4", "## Tier 5"]:
            idx = report.find(section)
            if idx >= 0:
                # Get content until next ## section
                next_section = report.find("\n## ", idx + len(section))
                section_text = report[idx:next_section] if next_section > 0 else report[idx:]
                if len(section_text) > 50:  # has real content
                    findings.append({
                        "summary": section_text[:300],
                        "evidence": section_text[:1000],
                        "validation_status": "validated",
                    })

    if not findings:
        return {"error": "no findings to score in report", "scores": []}

    scores = await score_findings(charter, findings[:10])  # Cap at 10 for cost

    return {
        "charter_length": len(charter),
        "findings_scored": len(scores),
        "scores": scores,
        "summary": {
            "yes": sum(1 for s in scores if s["score"] in ("yes", "yes_factual")),
            "yes_factual": sum(1 for s in scores if s["score"] == "yes_factual"),
            "marginal": sum(1 for s in scores if s["score"] == "marginal"),
            "no": sum(1 for s in scores if s["score"] == "no"),
        },
        "total_cost": sum(s["cost"] for s in scores),
    }
