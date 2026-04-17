"""Impact Analysis — assesses real-world consequences of validated findings.

Transforms discoveries from academic footnotes into actionable intelligence.
Answers: who's affected, how much money is at stake, who needs to know,
and what should be done about it.
"""

import json
import anthropic
from .schemas import ImpactResult
from .prompts import IMPACT_PROMPT


async def analyze_impact(finding_id: str, finding_description: str,
                         evidence: dict, confidence: float) -> ImpactResult:
    """Analyze the real-world impact of a validated finding.

    Args:
        finding_id: Unique identifier
        finding_description: Human-readable description of the finding
        evidence: The evidence dict supporting the finding
        confidence: Post-validation confidence score

    Returns:
        ImpactResult with affected parties, scale, financial exposure, etc.
    """
    evidence_str = json.dumps(evidence, indent=2, default=str)

    prompt = IMPACT_PROMPT.format(
        finding=finding_description,
        evidence_chain=evidence_str,
        confidence=f"{confidence:.2f}",
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
        return ImpactResult(
            finding_id=finding_id,
            affected_parties=["unknown"],
            estimated_scale="unknown",
            financial_exposure="Could not assess",
            risk_scenario="Impact analysis failed to parse",
            who_needs_to_know=["manual review needed"],
            urgency="medium",
            actionability="Review finding manually",
            reasoning="Parse error in impact analysis",
            raw_reasoning=raw_text,
            token_usage=usage,
            cost=cost,
        )

    return ImpactResult(
        finding_id=finding_id,
        affected_parties=result.get("affected_parties", []),
        estimated_scale=result.get("estimated_scale", "unknown"),
        financial_exposure=result.get("financial_exposure", ""),
        risk_scenario=result.get("risk_scenario", ""),
        who_needs_to_know=result.get("who_needs_to_know", []),
        urgency=result.get("urgency", "medium"),
        actionability=result.get("actionability", ""),
        reasoning=result.get("reasoning", ""),
        raw_reasoning=raw_text,
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
