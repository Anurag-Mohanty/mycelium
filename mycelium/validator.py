"""Validator — skeptical reviewer for Tier 3-5 findings.

After synthesis produces findings (contradictions, gaps, cross-cutting patterns),
each one goes through validation before inclusion in the final report. The validator
challenges the finding, checks evidence quality, and suggests verification actions.
"""

import json
import anthropic
from .schemas import ValidationResult
from .prompts import VALIDATION_PROMPT


async def validate_finding(finding_id: str, finding_type: str, finding: dict) -> ValidationResult:
    """Challenge a Tier 3-5 finding with skeptical review.

    Args:
        finding_id: Unique identifier for this finding
        finding_type: "contradiction", "gap", or "cross_cutting_pattern"
        finding: The finding dict from synthesis output

    Returns:
        ValidationResult with verdict, adjusted confidence, and verification suggestion
    """
    # Format the finding and evidence for the validator
    if finding_type == "contradiction":
        finding_desc = finding.get("what_conflicts", "")
        evidence = json.dumps({
            "side_a": finding.get("side_a", {}),
            "side_b": finding.get("side_b", {}),
            "significance": finding.get("significance", ""),
        }, indent=2)
    else:
        finding_desc = finding.get("pattern", "")
        evidence = json.dumps({
            "evidence_chain": finding.get("evidence_chain", []),
            "confidence": finding.get("confidence", 0),
            "inferred_links": finding.get("inferred_links", []),
        }, indent=2)

    prompt = VALIDATION_PROMPT.format(
        finding_type=finding_type,
        finding=finding_desc,
        evidence_chain=evidence,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    try:
        result = _parse_json(raw_text)
    except (json.JSONDecodeError, ValueError):
        return ValidationResult(
            finding_id=finding_id,
            original_finding=finding,
            verdict="needs_verification",
            reasoning="Failed to parse validator output",
            adjusted_confidence=0.3,
            adjusted_tier=3,
            verification_action="Manual review needed",
            revised_finding=None,
            raw_reasoning=raw_text,
            token_usage=usage,
            cost=cost,
        )

    return ValidationResult(
        finding_id=finding_id,
        original_finding=finding,
        verdict=result.get("verdict", "needs_verification"),
        reasoning=result.get("reasoning", ""),
        adjusted_confidence=float(result.get("adjusted_confidence", 0.5)),
        adjusted_tier=int(result.get("adjusted_tier", 3)),
        verification_action=result.get("verification_action", ""),
        revised_finding=result.get("revised_finding"),
        raw_reasoning=raw_text,
        factual_assessment=result.get("factual_assessment", {}),
        interpretive_assessment=result.get("interpretive_assessment", {}),
        token_usage=usage,
        cost=cost,
    )


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            return json.loads(text[start:end].strip())
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError("Could not extract JSON")
