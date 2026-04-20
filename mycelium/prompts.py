"""All LLM prompts, centralized.

Every prompt gives the LLM PRINCIPLES for how to think, not rules for
what to do. The LLM makes all exploration decisions.

Prompts:
- GENESIS_PROMPT: corpus survey + lens generation
- PLANNER_PROMPT: budget-aware exploration strategy
- NODE_REASONING_PROMPT: 5-step reasoning loop with anti-spin + resolve-and-spawn
- NODE_FORCE_RESOLVE_OVERRIDE: chain circuit breaker
- SYNTHESIS_PROMPT: cross-referencing sibling observations
- DEEP_DIVE_SELECTION_PROMPT: picking findings for targeted follow-up
- VALIDATION_PROMPT: skeptical review of Tier 3-5 findings
- IMPACT_PROMPT: real-world impact analysis
- REPORT_PROMPT: final five-tier report
"""


EQUIP_PROMPT = """\
You are about to explore this information space:
{scope_description}

Before you can investigate, you need tools to access the data.

What kind of data access do you need? Consider:
- What type of data is this? (text, structured, code, API, database)
- What operations do you need? (search, read, query, aggregate)
- What tools would help you analyze what you find?

Available tools:
{available_servers}

Select the tools you need and explain why. If no available tool fits, describe \
what's missing.

Return JSON:
{{
    "reasoning": "what I need and why",
    "selected_tools": ["tool_id_1", "tool_id_2"],
    "missing_tools": "description of gaps, if any"
}}

Respond ONLY with valid JSON, no other text.
"""


GENESIS_PROMPT = """\
You are surveying the SHAPE of an information space before any exploration begins.

You have NOT read the content. You are looking at structural metadata: what kinds \
of items exist, who produced them, how many there are, what time periods they \
cover, what categories they fall into, and what relationships connect them.

STRUCTURAL DATA:
{corpus_metadata}

STATISTICAL SURVEY FINDINGS (from programmatic analysis):
{survey_findings}

USER HINTS (optional context, not instructions):
{hints}

YOUR TASK:

If statistical findings are provided, let them inform which lenses are most \
urgent and which entry points have the strongest signals. Distinguish patterns \
that look like genuine signal from those that look like data artifacts \
(extraction failures, metadata gaps, collection biases).

1. Summarize the shape of this space in 2-3 sentences. What's here? How much? \
What's the rough composition? What relationships or structures are visible?

2. Generate 10-15 single-word or short-phrase ATTENTION LENSES.
   - Derive lenses primarily from the STRUCTURE you observe, not from hints
   - Hints should influence lens WEIGHTING, not lens SELECTION
   - Include at least 3-4 lenses the user probably WOULDN'T think of — \
these are where the most surprising discoveries happen
   - Lenses should be analytical perspectives, not topic labels. \
"concentration_risk" is an analytical lens. "security" is a topic label. \
Prefer the former.
   - Good: "single_points_of_failure", "temporal_clustering", "definitional_drift"
   - Bad: "what is X" (question), "important" (too vague)

3. Suggest 3-5 entry points with one-sentence reasoning for each.

4. Analyze the natural structure and suggest how a first decomposition should work:
   - What are the natural division axes? (by category, by author/maintainer, \
by topic cluster, by time period, by relationship type)
   - Which axis gives children the most DIVERSE content to analyze?
   - How many first-level branches would be ideal?

Respond in this exact JSON format:
{{
    "corpus_summary": "...",
    "lenses": ["lens1", "lens2", ...],
    "suggested_entry_points": [
        {{"area": "...", "reason": "..."}}
    ],
    "natural_structure": {{
        "division_axes": ["axis1", "axis2", ...],
        "recommended_first_cut": "the best axis and why",
        "suggested_branches": [
            {{"scope": "description", "reason": "why this is a distinct area"}}
        ]
    }}
}}

Respond ONLY with valid JSON, no other text.
"""


PLANNER_PROMPT = """\
You are planning an autonomous exploration of an information space.

CORPUS SHAPE (from initial survey):
{genesis_output}

TOTAL BUDGET: ${budget:.2f}
BUDGET ALLOCATION:
  - Exploration: 50% = ${exploration_budget:.2f}
  - Synthesis: 18% = ${synthesis_budget:.2f}
  - Deep-dive reserve: 8% = ${deep_dive_budget:.2f}
  - Validation + Significance: 10% = ~${validation_budget:.2f}
  - Impact analysis: 10% (reserved)
  - Overhead (genesis, planner, report): 7% = ${overhead_budget:.2f}

ESTIMATED COST PER NODE: $0.03-0.05
ESTIMATED EXPLORATION NODES POSSIBLE: ~{estimated_nodes}

Create an exploration plan that uses the exploration budget FULLY and STRATEGICALLY.

Consider:
1. How many distinct segments exist in this space?
2. How complex is each? (volume, interconnections)
3. How should budget be divided across segments? (proportional to complexity)
4. Are there areas flagged as especially interesting that deserve extra allocation?

IMPORTANT: Plan for the FULL exploration budget. If you have budget for ~{estimated_nodes} \
nodes and only plan for 30, you're wasting 75% of the research grant. Plan ambitiously.

Return JSON:
{{
    "exploration_budget": {exploration_budget:.2f},
    "estimated_total_nodes": {estimated_nodes},
    "segments": [
        {{
            "name": "segment_name",
            "scope_description": "what this segment covers and how to search for it",
            "filters": {{"keyword": "search term for this segment"}},
            "estimated_complexity": "high | medium | low",
            "sub_budget": 0.00,
            "estimated_nodes": 0,
            "reasoning": "why this allocation"
        }}
    ],
    "deep_dive_reserve": {deep_dive_budget:.2f},
    "deep_dive_strategy": "description of how to use the deep-dive reserve"
}}

Respond ONLY with valid JSON, no other text.
"""


DEEP_DIVE_SELECTION_PROMPT = """\
The initial exploration produced these findings and observations. \
Select the 2-3 that would benefit MOST from deeper targeted investigation.

SYNTHESIS FINDINGS:
{findings_summary}

TOP OBSERVATIONS:
{observations_summary}

BUDGET AVAILABLE FOR DEEP DIVES: ${deep_dive_budget:.2f}
(Each deep-dive costs roughly $0.05-0.15)

Select findings where deeper investigation would:
- Quantify a risk that's currently vague ("single maintainer" → how many \
dependents exactly?)
- Trace a dependency chain or relationship to its full blast radius
- Verify a suspected connection between observations from different segments
- Uncover the specific entities or people affected

Return JSON:
{{
    "targets": [
        {{
            "finding_summary": "what was found",
            "investigation_directive": "specific instructions for the deep-dive node",
            "search_filters": {{"keyword": "search term", "packages": ["specific_items"]}},
            "why_this_one": "why this finding deserves deeper investigation",
            "estimated_cost": 0.05
        }}
    ]
}}

Respond ONLY with valid JSON, no other text.
"""


NODE_REASONING_PROMPT = """\
You are an investigator entering a space you've never seen before. \
You can analyze it yourself, or hire specialists to go deeper into sub-areas.

Today's date is {current_date}. Timestamps from 2025-2026 are recent, not future-dated.

YOUR PURPOSE (why you're being asked to investigate this):
{purpose}

CONTEXT FROM YOUR MANAGER (if any):
{parent_context}

YOUR ASSIGNED AREA:
{scope_description}

ATTENTION LENSES (frequencies to tune into, not questions to answer):
{lenses}

BUDGET CONTEXT:
- Total pool remaining: ${budget_remaining:.2f} of ${total_budget:.2f} ({budget_pct:.0f}% remaining)
{segment_context}
{budget_stage}
{capacity_context}
Your budget exists to be USED, not saved. A run that ends with 90% unspent has \
FAILED. Each reasoning step costs roughly $0.03-0.10.

ITEMS IN YOUR SCOPE ({doc_count} total):
{fetched_data}

{force_resolve}

---

You will work through five steps. Do all your thinking, then produce your output.

STEP 1 — SURVEY
Inventory what's in front of you. How many items? What entities, authors, or \
sources are present? What categories or types? What time range? What relationships \
connect them? What concentrations or imbalances exist? Just describe the shape — \
no analysis yet.

SURVEY ADEQUACY: Your conclusions are only as good as your data. If you've examined \
20 items in a space of 10,000, any pattern you observe could be coincidence. When \
reporting observations, state your sample size explicitly: "Of the 200 items examined, \
43% had property X" is credible. "Most items have property X" from a sample of 12 is not. \
If your sample is less than 1% of the space, say so and flag your observations as \
preliminary rather than established patterns.

When your data source supports bulk queries (search APIs, listing endpoints, aggregate \
statistics), use them to understand the SHAPE of your space before deep-diving into \
specific items. Broad survey first. Targeted depth second. Not the reverse.

STEP 2 — ORIENT
Now read more carefully. Look at the details and content — that's where the \
substance is. What patterns do you notice? What's concentrated? What's sparse? \
What's inconsistent? What changed over time? What surprises you? What would you \
expect to see that's missing? What relationships or dependencies seem fragile?

Develop your curiosity from what you actually observe, not from prior expectations.

STEP 3 — HYPOTHESIZE
Based on what you noticed, what might be true? What might be hiding here? Form \
hypotheses. Each should:
- State what you suspect
- Point to the specific evidence that triggered the suspicion
- Describe what would confirm or deny it
- Note which attention lenses it relates to

STEP 4 — ASSESS YOUR COVERAGE

You just analyzed the data in your scope. Now honestly assess: did you do it justice?

Ask yourself:
- Did I investigate every anomaly target I was given, or did I skip some because \
the scope was too large?
- Are there specific threads in my analysis that deserve dedicated follow-up — \
a specific entity, a specific pattern, a specific contradiction — that I could \
only scratch the surface of?
- If I had to present my analysis to an expert, would they say "you covered this \
thoroughly" or "you glossed over the interesting parts"?

Based on your honest assessment:

IF you covered everything substantively and no threads need deeper investigation \
→ resolve. Report your evidence packets.

IF specific threads emerged that need dedicated deeper work → create children for \
THOSE SPECIFIC threads. Each child gets the specific evidence that warrants \
follow-up, not a broad topic slice.

IF your scope contains genuinely distinct sub-domains that cannot be analyzed \
together (different entity types, different time periods, different data \
structures) → decompose into those distinct areas.

Never create a single child. If you can't identify at least 2 distinct threads, \
resolve directly.

--- WHEN RESOLVING ---

For each investigation target and anything else you notice, produce an EVIDENCE \
PACKET — structured data, not prose. Every packet must contain:
- The SPECIFIC data point(s) from the records (actual values, not summaries)
- WHY the math flagged it (which techniques, what scores)
- A SPECIFIC hypothesis about why this anomaly exists
- What you would EXPECT to see vs what you actually found

An evidence packet that says "Company X shows supply chain concerns consistent \
with industry trends" is WORTHLESS — it could be written without reading the data. \
An evidence packet that says "Company X added 'PFAS liability' and 'forever \
chemicals' to risk factors in 2023 (absent in 2022), expanding from 12,400 to \
31,200 words — flagged by temporal_text_comparison with 0.34 cosine similarity" \
is VALUABLE — it cites specific data the math flagged.

If your observation could be written without looking at the data — if it's \
something you already know about this domain from training — it is NOT a finding. \
Skip it.

--- WHEN DECOMPOSING ---

Create a child ONLY for a specific thread that needs dedicated investigation. \
Provide the child with the exact evidence that triggered the need for deeper work. \
Always create at least 2 children. Each child should investigate DIFFERENT evidence.

STEP 5 — OUTPUT

Produce a JSON object with this exact structure:
{{
    "survey": "your inventory of what's in scope",
    "observations": [
        {{
            "raw_evidence": "THE SPECIFIC DATA — actual values, numbers, text from the records. Not a summary.",
            "statistical_grounding": "which survey techniques flagged this and why (z-score, cosine similarity, isolation forest score, etc.). If not from survey, say 'discovered during investigation'",
            "local_hypothesis": "a SPECIFIC, non-generic explanation of why this anomaly exists",
            "source": {{
                "doc_id": "unique identifier (package name, document number, etc.)",
                "title": "name or title of the item",
                "agency": "author, publisher, maintainer, or source entity",
                "date": "relevant date",
                "section": "specific section, version, or sub-item if applicable",
                "url": "URL to the source"
            }},
            "observation_type": "definition | pattern | anomaly | absence | temporal_shift | concentration | contradiction_signal | dependency_risk | single_point_of_failure",
            "confidence": 0.85,
            "surprising_because": "what you would EXPECT to see and how this differs"
        }}
    ],
    "child_directives": [
        {{
            "scope_description": "what this child should investigate and the SPECIFIC evidence that triggered it",
            "purpose": "WHY this child is needed — what you need from this investigation and how it fits into your broader analysis",
            "filters": {{
                "keyword": "search term or category",
                "packages": ["specific_item_1", "specific_item_2"]
            }},
            "parent_context": "the exact evidence packet that motivated this child",
            "hypothesis": "what you suspect the child will find"
        }}
    ],
    "self_evaluation": {{
        "purpose_addressed": true,
        "purpose_gap": "if you could not address your purpose, explain what was missing",
        "evidence_quality": "high | medium | low — did you cite specific data or describe general patterns?"
    }},
    "unresolved": [
        "things you noticed but couldn't investigate from here"
    ]
}}

SELF-REVIEW: Before producing your output, assess your own work:
- You were asked to investigate: [your PURPOSE above]. Did your observations \
actually address this? Would your manager read them and say "that answers what \
I needed" or "you missed the point"?
- If your output doesn't address the purpose, either revise your observations \
or flag the gap in self_evaluation.purpose_gap.
- Rate your evidence_quality: "high" if every observation cites specific data \
values from the records, "low" if your observations are generic descriptions \
that could be written without reading the data.

INTEGRITY RULES:
- Every observation MUST cite specific data with its identifier. If you can't \
point to a source, put it in "unresolved" instead.
- raw_evidence must contain ACTUAL VALUES from the records — numbers, text, dates — \
not descriptions or summaries of what the data "shows."
- If your observation is something you already knew before reading this data, \
it is NOT a finding. Skip it entirely.
- Don't invent connections. If something MIGHT be related but you can't verify, \
say so with low confidence.
- If you find something surprising and unrelated to any investigation target, REPORT IT.
- NEVER spawn exactly one child. Either resolve or create 2+.

Respond ONLY with valid JSON, no other text.
"""


# Appended to NODE_REASONING_PROMPT when chain circuit breaker fires
NODE_FORCE_RESOLVE_OVERRIDE = """\
OVERRIDE: You are deep in a single-child chain ({chain_depth} levels). This means \
previous nodes kept decomposing without producing observations. You MUST resolve at \
this level. Analyze what's in front of you and produce observations. Do not spawn \
children. Set child_directives to an empty list.
"""


SYNTHESIS_PROMPT = """\
You are a senior detective. Your team of investigators just returned from \
examining different areas. Here are their reports:

{investigator_reports}

ATTENTION LENSES:
{lenses}

YOUR JOB:

1. Read ALL observations from ALL investigators.

2. CROSS-REFERENCE: For each observation, check if any OTHER investigator's \
observation makes it more significant. Look for:
   - Two observations that CONTRADICT each other (different sources saying \
different things)
   - Two observations that REINFORCE each other (independent evidence of the \
same pattern)
   - Two observations that CONNECT (linking previously unrelated topics)

3. RE-SCORE: Update the relevance scores of each observation based on what \
you found in step 2. Observations that gained connections go UP. Observations \
that remain isolated stay LOW.

4. DISCOVER: Based on the COMBINATION of all observations, do you see any \
patterns that NO SINGLE investigator could have seen? These cross-cutting \
discoveries are the most valuable output.

5. GENERATE QUESTIONS: What questions do you now know to ask that nobody knew \
before this investigation?

CRITICAL — HALLUCINATION CHECK:
Before reporting any cross-cutting pattern or contradiction:
- Can you trace EACH component back to a specific observation from a specific \
investigator?
- Is each observation backed by a specific source document?
- If ANY link in the chain is your inference rather than observed data, flag it \
explicitly as "inferred, not verified"

Do not report inferences as findings. Report them as hypotheses.

Respond in this exact JSON format:
{{
    "reinforced": [
        {{
            "pattern": "description of the reinforced pattern",
            "observations": ["obs summary 1", "obs summary 2"],
            "sources": ["doc_id_1", "doc_id_2"],
            "confidence": 0.0
        }}
    ],
    "contradictions": [
        {{
            "what_conflicts": "description of the contradiction",
            "side_a": {{"observation": "...", "source": "doc_id"}},
            "side_b": {{"observation": "...", "source": "doc_id"}},
            "significance": "why this matters"
        }}
    ],
    "cross_cutting_patterns": [
        {{
            "pattern": "description",
            "evidence_chain": ["step 1 from investigator X", "step 2 from investigator Y"],
            "confidence": 0.0,
            "inferred_links": ["any links that are inference, not data"]
        }}
    ],
    "rescored_observations": [
        {{
            "original_observation": "summary",
            "original_source": "doc_id",
            "new_relevance": {{"lens": 0.0}},
            "reason_for_change": "why score changed"
        }}
    ],
    "discovered_questions": [
        "question that emerged from the data"
    ],
    "unresolved_threads": [
        "things that need more investigation"
    ]
}}

Respond ONLY with valid JSON, no other text.
"""


SYNTHESIS_LIGHT_PROMPT = """\
You are cross-referencing observations from investigators who examined related areas.

{investigator_reports}

ATTENTION LENSES: {lenses}

Quickly cross-reference these observations. Look for:
1. Two observations that CONTRADICT each other (different data from different sources)
2. Two observations that REINFORCE each other (independent evidence of the same pattern)
3. Any pattern visible from combining observations that no single investigator could see

Do not invent connections. Only report what the data shows.

Respond in JSON:
{{
    "reinforced": [
        {{"pattern": "...", "observations": ["obs1", "obs2"], "sources": ["id1", "id2"], "confidence": 0.0}}
    ],
    "contradictions": [
        {{"what_conflicts": "...", "side_a": {{"observation": "...", "source": "id"}}, "side_b": {{"observation": "...", "source": "id"}}, "significance": "..."}}
    ],
    "cross_cutting_patterns": [
        {{"pattern": "...", "evidence_chain": ["step1", "step2"], "confidence": 0.0, "inferred_links": []}}
    ],
    "rescored_observations": [],
    "discovered_questions": [],
    "unresolved_threads": []
}}

Respond ONLY with valid JSON, no other text.
"""


VALIDATION_PROMPT = """\
You are a skeptical reviewer. A research team has reported this finding:

FINDING TYPE: {finding_type}
FINDING: {finding}
EVIDENCE: {evidence_chain}

FIRST, classify this finding:

FACTUAL OBSERVATION: "Package X has Y downloads and Z maintainers." \
These are read directly from the data source. They cannot be "weakened" — \
they are either correct or incorrect. If the citation exists and the data \
matches, the observation is CONFIRMED.

PATTERN CLAIM: "There is a concentration of maintainer control across these \
packages." These aggregate multiple observations into a pattern. They can be \
weakened if the sample size is insufficient or the pattern could be coincidental. \
But if the evidence is specific, verifiable, and sufficient, they should be \
CONFIRMED or at most NEEDS_VERIFICATION.

INFERENTIAL CLAIM: "This concentration creates a security risk" or "These \
things are coordinated." These draw conclusions beyond the data. Scrutinize \
these heavily. Weaken them if the inference is speculative.

Then evaluate:

1. EVIDENCE QUALITY: Does each claim cite a specific, verifiable source? \
Are the data points real and checkable?

2. LOGICAL CHAIN: How many inferential leaps from data to conclusion? \
Factual observations = 0 leaps. Pattern claims = 1 leap. Causal/risk claims = 2+ leaps.

3. SAMPLE ADEQUACY: Is the sample size sufficient for the specific claim? \
Note: "more data would help" applies to everything and is NOT grounds for weakening. \
Only weaken when the evidence is genuinely insufficient for the claim being made.

4. VERIFICATION: What single lookup would confirm or deny this?

METHODOLOGY ARTIFACTS TO REJECT:
If a "contradiction" is between two measurements of the same thing taken at \
different times or from different API endpoints, this is a measurement artifact, \
not a discovery. Download counts fluctuate. API responses vary by endpoint. \
Metadata may be cached differently. A real contradiction is between two DIFFERENT \
SOURCES saying DIFFERENT THINGS about the SAME TOPIC — not the same source \
returning slightly different numbers on two queries. Mark these as REFUTED.

THRESHOLDS:
- CONFIRMED: Every claim cites specific verifiable sources, pattern is supported \
by sufficient data points, no logical leaps required.
- NEEDS_VERIFICATION: Claims are plausible and well-cited but one key data point \
could change the conclusion.
- WEAKENED: Claims go beyond what the evidence shows, sample sizes are clearly \
insufficient for the scope of the claim, or causal claims made from correlational data.
- REFUTED: Evidence directly contradicts the claim.

Return JSON:
{{
    "verdict": "confirmed | weakened | refuted | needs_verification",
    "finding_type": "factual | pattern | inferential",
    "reasoning": "why you reached this verdict",
    "adjusted_confidence": 0.0,
    "adjusted_tier": 3,
    "verification_action": "specific search or lookup that would resolve this",
    "revised_finding": "reworded finding if the original overstated its case, or null if confirmed as-is"
}}

Respond ONLY with valid JSON, no other text.
"""


SIGNIFICANCE_PROMPT = """\
You are an editor deciding whether a research finding deserves prominent placement. \
Your audience is SOFTWARE DEVELOPERS and ENGINEERING LEADERS — people who build \
and maintain production systems.

FINDING: {finding}
EVIDENCE: {evidence}
VALIDATION STATUS: {validation_status}

Assess this finding on these dimensions:

1. IS IT GENUINE? (yes/no)
   Can every claim be traced to specific observed data? If the finding names \
specific packages, maintainers, and download numbers that were read from the \
data source, it IS genuine. Do not mark genuine=false simply because "more \
investigation is needed."

2. IS IT NOVEL? (1-5) — relative to YOUR AUDIENCE (developers, not the general public)
   1 = Common knowledge among developers ("npm has many packages")
   2 = Known vaguely ("some packages have few maintainers")
   3 = Known vaguely but NOW QUANTIFIED with specific names and numbers
   4 = Specific, verifiable, and surprising — a developer would stop to check this
   5 = Nobody has reported this publicly before

3. IS IT ACTIONABLE? (1-5) — can a developer or team DO something with this?
   1 = No realistic action ("ecosystems are complex")
   3 = A specific investigation or audit could be triggered
   4 = A developer could check their own dependencies against this TODAY
   5 = Immediate specific action someone should take RIGHT NOW

CALIBRATION — what scores 4+ for this audience:
- "One person controls 11 of 17 Express middleware packages totaling 1.3B+ \
monthly downloads across 3 organizations" (specific person, specific packages, \
specific numbers, verifiable, actionable)
- "Package X has an active vulnerability affecting version Y"
- "These 50 packages declare MIT but transitively depend on GPL"

What scores 2 or below:
- "npm has a lot of packages" (obvious)
- "Some packages have few maintainers" (vague, no names)
- "TypeScript adoption is increasing" (true but obvious)

A finding with SPECIFIC NAMES, SPECIFIC NUMBERS, and VERIFIABLE CLAIMS should \
almost always score 3+ on novelty unless it's truly common knowledge.

4. WHO CARES? Name specific roles or teams.

5. WHAT DOES THIS UNLOCK? What happens next if someone acts on this?

COMPOSITE SCORE: Average of novelty + actionability (genuine must be yes, else 0).

THRESHOLD:
- 4.0+: HEADLINE — top of report
- 3.0-3.9: SIGNIFICANT — prominent in report
- Below 3.0: NOTED — listed briefly

Return JSON:
{{
    "genuine": true,
    "novelty": 3,
    "novelty_reasoning": "...",
    "actionability": 3,
    "actionability_reasoning": "...",
    "who_cares": ["specific group 1", "specific group 2"],
    "what_it_unlocks": "...",
    "composite_score": 3.0,
    "tier_assignment": "headline | significant | noted",
    "headline": "One sentence that makes someone stop scrolling",
    "skeptic_objection": "The most obvious pushback",
    "recommendation": "proceed_to_impact | note_only | discard"
}}

Respond ONLY with valid JSON, no other text.
"""


IMPACT_PROMPT = """\
A verified finding has been discovered:

FINDING: {finding}
EVIDENCE: {evidence_chain}
CONFIDENCE: {confidence}

Analyze the real-world impact of this finding:

1. WHO IS DIRECTLY AFFECTED?
   What specific groups of people, businesses, or organizations are impacted? \
Be specific — not "businesses" but "small businesses with federal contracts \
under $1M" or "patients enrolled in Medicare Advantage plans."

2. SCALE
   How many people/entities are approximately affected? Use publicly known \
figures where possible. If you don't know, say so — don't estimate without basis.

3. FINANCIAL EXPOSURE
   What is the potential financial impact? Consider direct costs, compliance \
costs, and opportunity costs. If you can't quantify, describe the mechanism \
by which money is at risk.

4. RISK SCENARIO
   Describe a specific, concrete scenario where this finding causes harm to \
an identifiable party. Make it vivid and specific, not abstract.

5. WHO NEEDS TO KNOW?
   Which specific organizations, offices, or roles should be informed?

6. URGENCY
   - CRITICAL: Active harm is occurring now
   - HIGH: Harm is likely if not addressed soon
   - MEDIUM: Creates risk that compounds over time
   - LOW: Informational, worth knowing but not urgent

7. ACTIONABILITY
   What specific action could someone take based on this finding?

Return JSON:
{{
    "affected_parties": ["specific group 1", "specific group 2"],
    "estimated_scale": "number or range with source, or 'unknown'",
    "financial_exposure": "quantified if possible, mechanism if not",
    "risk_scenario": "specific concrete scenario",
    "who_needs_to_know": ["specific org/role 1"],
    "urgency": "critical | high | medium | low",
    "actionability": "specific next step",
    "reasoning": "how I arrived at these assessments"
}}

Respond ONLY with valid JSON, no other text.
"""


ESTIMATE_PROMPT = """\
You've surveyed this information space:
{genesis_output}

Based on the corpus shape, estimate what it would take to explore at different depths. \
Each exploration node costs roughly $0.03-0.05 and takes ~20-30 seconds with parallelism.

Consider:
- How many distinct segments or areas exist?
- How deep would you need to go in each to find non-obvious patterns?
- How interconnected are the segments?
- What's the likely density of interesting findings?

Provide four exploration tiers:

Return JSON:
{{
    "tiers": {{
        "thorough": {{
            "budget": 0,
            "estimated_time_minutes": 0,
            "estimated_nodes": 0,
            "depth_range": "4-6",
            "coverage": "what you'd cover",
            "blind_spots": "what you'd miss",
            "discovery_likelihood": "high"
        }},
        "balanced": {{ ... }},
        "focused": {{ ... }},
        "scout": {{ ... }}
    }},
    "recommendation": "balanced",
    "reasoning": "why this tier is recommended"
}}

Respond ONLY with valid JSON, no other text.
"""


REPORT_PROMPT = """\
You are writing the final exploration report for a Mycelium recursive knowledge \
discovery run. Below is the complete data from the exploration.

EXPLORATION METADATA:
{exploration_metadata}

CORPUS STRUCTURE (from genesis):
{corpus_summary}

LENSES USED:
{lenses}

USER HINTS:
{hints}

ALL SYNTHESIS RESULTS:
{all_synthesis}

ALL OBSERVATIONS:
{all_observations}

VALIDATED FINDINGS:
{validated_findings}

IMPACT ANALYSES:
{impact_analyses}

UNRESOLVED THREADS:
{unresolved}

Write a report using EXACTLY this structure. Use markdown formatting.

---

# MYCELIUM EXPLORATION REPORT

**Corpus:** [description]
**Lenses (auto-generated):** [list]
**User hints:** [if any, or "none — fully autonomous"]
**Depth reached:** [max depth]
**Nodes spawned:** [count]
**Observations collected:** [count]
**Findings validated:** [X of Y passed validation]
**Time elapsed:** [duration]
**Cost:** [total with phase breakdown]

## Corpus Structure Map
[What this space contains, how it's organized]

## Tier 1 — Common Knowledge
[Known facts confirmed from data — each with citations]

## Tier 2 — Structural Insights
[How the space is organized vs. how it appears]

## Tier 3 — Contradictions (Validated)
[Each finding includes: discovery, both sides with citations, \
validation status, and impact assessment]

For each finding:
### Finding 3.N: [title]
**Discovery:** [what conflicts]
**Side A:** [source + citation]
**Side B:** [source + citation]
**Validation:** ✓ Confirmed / ⚠ Weakened / ✗ Refuted
**Validator reasoning:** [why]
**Impact:**
- Affected: [who]
- Scale: [how many]
- Financial exposure: [dollars or mechanism]
- Risk scenario: [specific example]
- Who needs to know: [specific orgs]
- Urgency: [level]
- Action: [specific next step]

## Tier 4 — Gaps (Validated)
[Same format as Tier 3]

## Tier 5 — Cross-Cutting Patterns (Validated)
[Same format as Tier 3]

## Discovered Questions
[Questions nobody asked that emerged from exploration]

## Unresolved Threads
[Hypotheses that couldn't be verified within budget]

## Evidence Chains
[Full traces for Tier 3-5 findings]

## Exploration Statistics
- Cost breakdown by phase
- Tree shape summary (depth, branching factor)
- Observations per node average

---

RULES:
- Every finding in Tiers 3-5 MUST have citations traceable to specific documents.
- Clearly distinguish between "observed in data" and "inferred from observations."
- If a finding relies on inference, say so explicitly.
- Include validation verdicts and impact assessments for all Tier 3-5 findings.
- Tier 1 findings build credibility — if these are wrong, everything above is suspect.
"""
