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
