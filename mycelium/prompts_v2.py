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
NUMBER OF GENESIS LENSES: {num_lenses}

DOWNSTREAM PHASE COSTS (reserved, not your decision):
  - Review (parent Turn 2): 15% of total = ${review_budget:.2f}
  - Synthesis: ~$0.06-0.08
  - Deep-dive reserve: ~$0.02-0.05
  - Validation + Impact: ~$0.10-0.20
  - Overhead (genesis, planner, report): ~$0.30-0.40

ESTIMATED COST PER EXPLORATION NODE: $0.10-0.18 (includes Turn 1 reasoning + data fetch)
MINIMUM VIABLE LEAF ENVELOPE: ${leaf_viable_envelope:.2f} (a child must receive at least \
this much to do productive work)

YOUR FIRST TASK: Decide the exploration envelope — what percentage of the total \
budget should be allocated to exploration (initial probes + continuation follow-ups).

Reason explicitly about:
- How many of the {num_lenses} genesis lenses look worth pursuing as segments?
- At ~$0.15 per probe, how much would initial breadth cost?
- How much continuation budget would fund 2-3 deep follow-ups per segment?
- After subtracting downstream phase costs, what remains for exploration?

The exploration envelope must be between 40% and 75% of total budget. Below 40% \
starves exploration. Above 75% starves downstream phases.

YOUR SECOND TASK: Compute the maximum decomposition depth the budget can support.

Work BACKWARDS from leaf viability. Don't pick a depth and hope it's affordable — \
compute the depth that falls out of the budget math.

1. A leaf node needs at least ${leaf_viable_envelope:.2f} envelope to do productive work.
2. A parent typically keeps ~30% of its envelope for its own Turn 1 work and reserves, \
allocating ~70% across children.
3. Starting from your exploration envelope, work through the tree levels:
   - Root segments get exploration_envelope / num_segments each
   - Depth 1 children get parent_envelope * 0.70 / expected_branching each
   - Continue until the per-node envelope drops below ${leaf_viable_envelope:.2f}
4. The last depth where nodes still have at least ${leaf_viable_envelope:.2f} is your \
max_decomposition_depth.

Show your math explicitly. Use your assumed leaf_viable_envelope, parent_retention_ratio, \
and expected_branching factor so the calculation is auditable.

YOUR THIRD TASK: Create an exploration plan that uses the exploration envelope \
FULLY and STRATEGICALLY.

Consider:
1. How many distinct segments exist in this space?
2. How complex is each? (volume, interconnections)
3. How should budget be divided across segments? (proportional to complexity)
4. Are there areas flagged as especially interesting that deserve extra allocation?

IMPORTANT: Plan for the FULL exploration envelope. If you have budget for many \
nodes and only plan for a few, you're wasting the research grant. Plan ambitiously.

Return JSON:
{{
    "exploration_envelope": {{
        "reasoning": "your reasoning about how much of the budget should go to exploration",
        "percentage": 0.60,
        "absolute_dollars": 0.00
    }},
    "max_decomposition_depth": {{
        "leaf_viable_envelope": {leaf_viable_envelope:.2f},
        "parent_retention_ratio": 0.30,
        "expected_branching": 3,
        "reasoning": "show your math: starting envelope, per-level allocation, where it drops below leaf viable",
        "depth": 2
    }},
    "exploration_budget": 0.00,
    "estimated_total_nodes": 0,
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
    "deep_dive_reserve": 0.05,
    "deep_dive_strategy": "description of how to use the deep-dive reserve"
}}

Set exploration_budget equal to the absolute_dollars from your exploration_envelope.
Set segment sub_budgets to sum to the exploration_budget.

Respond ONLY with valid JSON, no other text.
"""


ANOMALY_AGGREGATION_PROMPT = """\
You are summarizing statistical anomalies into distinct patterns.

ANOMALIES (numbered):
{anomaly_list}

Group these anomalies into distinct PATTERNS. Each pattern represents a \
category of anomalies that share a common cause or mechanism. For each \
pattern, provide a description and list the representative anomaly indices.

Return ONLY a JSON object:
{{
    "patterns": [
        {{
            "name": "short pattern name",
            "description": "what this pattern represents and why it matters",
            "representative_indices": [0, 3, 7],
            "anomaly_count": 15
        }}
    ]
}}

Rules:
- Every anomaly should belong to exactly one pattern
- Patterns should be meaningfully distinct — not just technique labels
- Include the most informative anomalies as representatives (max 5 per pattern)
- If an anomaly looks like a data artifact rather than real signal, note that in the description

Respond ONLY with valid JSON, no other text.
"""


ANOMALY_ROUTING_PROMPT = """\
You are routing statistical anomaly patterns to an investigation segment.

SEGMENT:
Name: {segment_name}
Scope: {segment_scope}
Purpose: {segment_reasoning}

ANOMALY PATTERNS (from aggregation):
{pattern_summary}

Which of these patterns are RELEVANT to this segment's scope and purpose? \
A pattern is relevant if the entities, fields, or phenomena it describes \
fall within what this segment will investigate.

Be selective — only include patterns this segment can actually investigate. \
If none are relevant, return an empty list.

Return ONLY a JSON object:
{{
    "relevant_pattern_indices": [0, 2],
    "reasoning": "one sentence explaining your selection"
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

{briefing_context}

YOUR ASSIGNED AREA:
{scope_description}

ATTENTION LENSES (frequencies to tune into, not questions to answer):
{lenses}

DATA SOURCE FILTER SCHEMA (how to query this data source when creating children):
{filter_schema}

When creating child_directives, use ONLY the filter parameter names listed above. \
The data source does matching as described — use values that will actually match \
records. If you saw specific entity names in your data, use those exact names. \
Do NOT invent natural-language queries as filter values.

BUDGET CONTEXT:
You have been allocated ${budget_remaining:.2f} for this investigation.
Your parent has approximately ${parent_pool_remaining:.2f} remaining in its pool.
The overall exploration phase has approximately ${phase_remaining:.2f} remaining.
{segment_context}
Your current depth: {current_depth}
Max decomposition depth: {max_depth}
Minimum child envelope: ${leaf_viable_envelope:.2f}

{depth_guidance}

{budget_stage}
{capacity_context}
When allocating budget to children:
- Each child must receive at least ${leaf_viable_envelope:.2f}. The system will \
automatically reject spawns below this minimum.
- Prefer 2-3 well-funded children over 4-5 underfunded ones.

Quality investigation is more valuable than speed. If the data supports deeper \
investigation than your envelope allows, name that explicitly in your \
worthwhile_followup_threads — do not artificially wrap up work that deserves \
more attention. Do not produce shallow observations just to hit a minimum count.

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

When identifying interesting patterns, check against the Common Knowledge briefing \
(if provided above). Patterns the briefing already covers are not interesting \
unless you're finding a significant deviation or contradiction.

Develop your curiosity from what you actually observe, not from prior expectations.

STEP 3 — HYPOTHESIZE
Based on what you noticed, what might be true? What might be hiding here? Form \
hypotheses about patterns that are genuinely novel relative to the briefing. If a \
pattern would fit in the Common Knowledge briefing, do not hypothesize further \
about it — note it as confirmatory and move on.

Each hypothesis should:
- State what you suspect
- Point to the specific evidence that triggered the suspicion
- Describe what would confirm or deny it
- Note which attention lenses it relates to

STEP 4 — ASSESS YOUR COVERAGE AND SELF-ASSESS

You just analyzed the data in your scope. Now honestly assess: did you do it justice?

Ask yourself:
- Did I investigate every anomaly target I was given, or did I skip some because \
the scope was too large?
- Are there specific threads in my analysis that deserve dedicated follow-up — \
a specific entity, a specific pattern, a specific contradiction — that I could \
only scratch the surface of?
- If I had to present my analysis to an expert, would they say "you covered this \
thoroughly" or "you glossed over the interesting parts"?

SELF-ASSESSMENT: Before producing output, you MUST populate every field in \
the self_evaluation section of the output schema. This includes \
worthwhile_followup_threads, capability_gaps, and adjacent_findings — all \
three are REQUIRED fields. See the output schema for what each field needs. \
Empty arrays require an explicit justification string.

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
            "confidence_rationale": "why you are this confident — name any uncertainties (thin evidence, single data point, possible alternative explanations)",
            "signal_strength": "data_originated_novel | data_originated_confirmatory | confirmatory",
            "surprising_because": "what you would EXPECT to see and how this differs"
        }}
    ],
    "child_directives": [
        {{
            "scope_description": "what this child should investigate and the SPECIFIC evidence that triggered it",
            "purpose": "WHY this child is needed — what you need from this investigation and how it fits into your broader analysis",
            "data_filter": {{
                "use ONLY parameter names from the DATA SOURCE FILTER SCHEMA above": "with values that will match actual records you saw in your data"
            }},
            "parent_context": "the exact evidence packet that motivated this child",
            "hypothesis": "what you suspect the child will find"
        }}
    ],
    "self_evaluation": {{
        "purpose_addressed": true,
        "purpose_gap": "if you could not address your purpose, explain what was missing",
        "evidence_quality": "high | medium | low — did you cite specific data or describe general patterns?",
        "worthwhile_followup_threads": [
            {{
                "what_to_investigate": "REQUIRED — a specific investigation thread, not vague. What entity, pattern, or question?",
                "data_or_tools_needed": "what data source, filter, or tool is required",
                "question_it_answers": "what specific question this would resolve",
                "scope_estimate": "a focused follow-up | a full decomposition"
            }}
        ],
        "capability_gaps": ["REQUIRED — what data or tools were needed but unavailable. If none, include the string: 'no capability gaps encountered'"],
        "adjacent_findings": ["REQUIRED — observations noticed OUTSIDE your assigned scope. If none, include the string: 'no adjacent findings outside scope'"]
    }},
    "unresolved": [
        "things you noticed but couldn't investigate from here"
    ]
}}

BEFORE FINALIZING: Verify your self_evaluation contains ALL required fields \
populated with content: purpose_addressed, evidence_quality, \
worthwhile_followup_threads (at least one thread OR explicit "nothing warrants \
deeper investigation" statement), capability_gaps, and adjacent_findings. \
Empty arrays without justification strings will be flagged as incomplete.

SELF-REVIEW: Before producing your output, assess your own work:
- You were asked to investigate: [your PURPOSE above]. Did your observations \
actually address this? Would your manager read them and say "that answers what \
I needed" or "you missed the point"?
- If your output doesn't address the purpose, either revise your observations \
or flag the gap in self_evaluation.purpose_gap.
- Rate your evidence_quality: "high" if every observation cites specific data \
values from the records, "low" if your observations are generic descriptions \
that could be written without reading the data.
- For each observation, did you honestly set signal_strength? Three levels:
  "data_originated_novel" = required the data AND not covered by the briefing.
  "data_originated_confirmatory" = required the data BUT restates something in the briefing.
  "confirmatory" = would be expected without reading the data at all.
- For each observation, did you name your uncertainties in confidence_rationale?
- Are your worthwhile_followup_threads specific enough that a parent could \
spawn a targeted continuation from each one? If not, revise or remove them.
- Did you note anything outside your scope in adjacent_findings?

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

CITATION DISCIPLINE (read before proceeding):
Every claim in a contradiction or pattern MUST cite specific data points from \
the worker observations. Specifically:
- The observation field must include the SPECIFIC DATA the worker found: actual \
numbers, entity names, dates, exact text — not a paraphrased summary.
- The source field must include the original observation's source identifier \
(doc_id, URL, or equivalent), not just a topic name.
- For each piece of evidence in a cross-cutting pattern, cite the specific \
observation with its source identifier.

If the supporting observations from workers do not contain specific data points \
(only prose impressions), DO NOT synthesize them into a finding. Findings without \
verifiable specifics waste downstream validation effort and produce unfalsifiable \
claims. Fewer findings with specific evidence are better than many findings with \
vague evidence.

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
            "side_a": {{
                "observation": "THE SPECIFIC DATA from worker — actual numbers, names, dates. Not a summary.",
                "specific_data_points": ["entity X has value Y", "entity Z has value W"],
                "source": "doc_id or URL of the original source",
                "source_observation_node": "node_id of the worker that produced this"
            }},
            "side_b": {{
                "observation": "THE SPECIFIC DATA from worker — actual numbers, names, dates. Not a summary.",
                "specific_data_points": ["entity A has value B", "entity C has value D"],
                "source": "doc_id or URL of the original source",
                "source_observation_node": "node_id of the worker that produced this"
            }},
            "significance": "why this matters"
        }}
    ],
    "cross_cutting_patterns": [
        {{
            "pattern": "description",
            "evidence_chain": [
                {{
                    "claim": "specific factual claim with data",
                    "specific_data_points": ["entity X: value Y"],
                    "source": "doc_id",
                    "source_observation_node": "node_id"
                }}
            ],
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

Most findings contain TWO kinds of claims mixed together. Your job is to \
evaluate them SEPARATELY:

A. FACTUAL CLAIMS — observations about the data. Counts, entities, dates, \
word-level differences, relationships between records. These are verifiable: \
either the data says what the finding claims, or it doesn't.

B. INTERPRETIVE CLAIMS — what the facts imply. Causal explanations, risk \
assessments, motivations, predictions. These often can't be fully verified \
from the data alone.

EVALUATE FACTUAL CLAIMS:
1. Does each factual claim cite a specific, verifiable source?
2. Are the data points real and checkable?
3. Is the sample size sufficient for the factual claim being made?
4. Verdict: CONFIRMED (data supports), REFUTED (data contradicts), or \
MISSING_EVIDENCE (can't verify from available data).

EVALUATE INTERPRETIVE CLAIMS:
1. How many inferential leaps from the facts to the interpretation?
2. Are there alternative interpretations the facts would also support?
3. Confidence: WELL_SUPPORTED (facts strongly imply this, few alternatives), \
PLAUSIBLE (consistent with facts but other interpretations fit), or \
SPECULATIVE (large leap from facts to interpretation).

DERIVE OVERALL VERDICT:
- CONFIRMED: factual claims CONFIRMED AND interpretive claims WELL_SUPPORTED
- CONFIRMED_WITH_CAVEATS: factual claims CONFIRMED but interpretive claims \
only PLAUSIBLE or SPECULATIVE. The observations are real; the interpretation \
is uncertain. This is NOT a weak finding — it means the data is solid and \
the interpretation is reasonable but not the only possibility.
- WEAKENED: factual claims have MISSING_EVIDENCE in part, but partial support exists
- REFUTED: factual claims REFUTED or evidence directly contradicts the finding

IMPORTANT: A finding where the factual observations are verified but the \
interpretation is uncertain is CONFIRMED_WITH_CAVEATS, not NEEDS_VERIFICATION. \
Reserve NEEDS_VERIFICATION only for findings where the factual claims themselves \
cannot be assessed.

METHODOLOGY ARTIFACTS TO REJECT:
If a "contradiction" is between two measurements of the same thing taken at \
different times or from different API endpoints, this is a measurement artifact, \
not a discovery. Mark these as REFUTED.

Return JSON:
{{
    "factual_assessment": {{
        "verdict": "CONFIRMED | REFUTED | MISSING_EVIDENCE",
        "verifiable_claims": ["list each factual claim found in the finding"],
        "reasoning": "why you assessed the facts this way"
    }},
    "interpretive_assessment": {{
        "confidence": "WELL_SUPPORTED | PLAUSIBLE | SPECULATIVE",
        "interpretive_claims": ["list each interpretive claim found"],
        "reasoning": "why this confidence level"
    }},
    "verdict": "confirmed | confirmed_with_caveats | weakened | refuted | needs_verification",
    "reasoning": "overall assessment combining factual and interpretive",
    "adjusted_confidence": 0.0,
    "adjusted_tier": 3,
    "verification_action": "specific lookup that would resolve remaining uncertainty",
    "revised_finding": "reworded finding separating established facts from uncertain interpretation, or null if confirmed as-is"
}}

Respond ONLY with valid JSON, no other text.
"""


SIGNIFICANCE_PROMPT = """\
You are an editor deciding whether a research finding deserves prominent placement. \
Your audience is practitioners in this domain — people who work with this data daily.

FINDING: {finding}
EVIDENCE: {evidence}
VALIDATION STATUS: {validation_status}

COMMON KNOWLEDGE BRIEFING (what a practitioner already knows):
{briefing_context}

Assess this finding on these dimensions:

1. IS IT GENUINE? (yes/no)
   Can every claim be traced to specific observed data? If the finding names \
specific packages, maintainers, and download numbers that were read from the \
data source, it IS genuine. Do not mark genuine=false simply because "more \
investigation is needed."

2. IS IT COMMONLY KNOWN? Check the briefing above. If this finding merely restates, \
illustrates, or is a direct consequence of a briefing claim, it is COMMONLY KNOWN. \
Mark commonly_known=true and set novelty to 1-2.

3. IS IT NOVEL? (1-5) — relative to the briefing AND your audience
   1 = Directly covered by the briefing (commonly known)
   2 = Close variant of a briefing claim (known vaguely)
   3 = Known vaguely but NOW QUANTIFIED with specific names and numbers
   4 = Specific, verifiable, and surprising — a practitioner would stop to check this
   5 = Nobody has reported this publicly before

4. IS IT ACTIONABLE? (1-5) — can a practitioner DO something with this?
   1 = No realistic action ("ecosystems are complex")
   3 = A specific investigation or audit could be triggered
   4 = A practitioner could check their own systems against this TODAY
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

5. WHO CARES? Name specific roles or teams.

6. WHAT DOES THIS UNLOCK? What happens next if someone acts on this?

COMPOSITE SCORE: Average of novelty + actionability (genuine must be yes, else 0).

THRESHOLD:
- 4.0+: HEADLINE — top of report
- 3.0-3.9: SIGNIFICANT — prominent in report
- Below 3.0: NOTED — listed briefly

Return JSON:
{{
    "genuine": true,
    "commonly_known": false,
    "commonly_known_reasoning": "which briefing claim this matches, if any",
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


TURN2_REVIEW_PROMPT = """\
Your children have returned. You have ${budget_remaining:.2f} available in \
your pool (this includes budget returned from children who resolved under envelope).

YOUR ORIGINAL OBSERVATIONS:
{my_observations}

WORKER REPORTS (including self-assessments):
{children_reports}

Read each child's output carefully, especially their self-assessment sections:
- worthwhile_followup_threads: what specific investigations each child flagged as valuable
- signal_strength on observations: which observations are data_originated vs confirmatory
- capability_gaps and adjacent_findings: what each child noticed outside scope or couldn't do

────────────────────────────────────────────────────
STEP 1 — SUMMARIZE EACH CHILD'S OUTPUT

For each worker, state:
- What it was asked to do (its purpose)
- What it returned: observation count, key evidence, self-evaluation
- How many observations are data_originated vs confirmatory
- What follow-up threads it flagged as worthwhile
- What capability gaps or adjacent findings it reported
- Whether it returned zero records (data fetch failure)

────────────────────────────────────────────────────
STEP 2 — BUDGET DEPLOYMENT DECISION

Your primary question: what is the highest-value deployment of your \
${budget_remaining:.2f} available budget?

Choose EXACTLY ONE option. Options in order of priority:

OPTION A: FUND CONTINUATION ON A FLAGGED THREAD
One or more children named specific worthwhile follow-up threads in their \
self-assessment. Spawn a continuation child on the highest-value thread, \
funded from your available pool. The continuation child receives the original \
child's relevant observations as parent context, plus the specific thread scope.

OPTION B: FUND ADJACENT-FINDING INVESTIGATION
A child's adjacent findings (observations noticed outside its assigned scope) \
point to something worth investigating. Spawn a new child with the adjacent \
finding as its scope. Include relevant context from sibling observations.

OPTION C: SPAWN MORE CHILDREN ON THE CURRENT LINE
The children's failure was scope or specificity — a different child formulation \
might succeed. You MUST name what was wrong with the previous children's framing \
and how a new formulation would avoid the same failure.

OPTION D: PIVOT
The children consistently flagged the SAME capability limit. More children on \
the current line will hit the same limit. Reason about what the children DID \
learn and whether an adjacent investigation is answerable with available data. \
If yes, spawn children on the pivoted line. If no, resolve and escalate the gap.

OPTION E: RESOLVE
No further deployment of available budget would produce valuable work. \
Resolution is correct ONLY when you can justify why:
(1) No flagged follow-up threads are worth pursuing, AND
(2) No adjacent findings warrant investigation, AND
(3) The current line cannot be usefully reformulated.

"My children addressed their briefs" is NOT sufficient justification for \
resolution when valuable deployment options remain.

KEY PRINCIPLES:
- A consistent pattern of children flagging the SAME gap is evidence for D, not C.
- Children that named specific follow-up threads are giving you deployment targets \
for option A — use them.
- Resolution (E) is the LAST option, not the default. Justify it actively.

────────────────────────────────────────────────────
STEP 3 — IDENTIFY ADJACENT FINDINGS

Examine ALL children's adjacent_findings and capability_gaps for findings \
outside their assigned scope.

For EACH adjacent finding, choose ONE action:

ACTION 1 — SPAWN A NEW CHILD: Create a child directive to investigate it. \
Budget comes from your remaining pool.

ACTION 2 — ESCALATE TO YOUR PARENT: Emit as an observation with \
escalated_adjacency=true. Use when the finding falls outside your entire \
subtree's scope.

ACTION 3 — RECORD AS UNADDRESSED: Emit as an observation with \
unaddressed_adjacency=true. Use when interesting but not worth budget.

────────────────────────────────────────────────────
STEP 4 — EMIT OUTPUT

Return JSON:
{{
    "option_chosen": "A | B | C | D | E",
    "option_reasoning": "why you chose this option — reference specific child outputs, flagged threads, and budget deployment rationale",
    "children_summary": [
        {{
            "worker_scope": "what this worker was asked",
            "observations_count": 3,
            "data_originated_count": 2,
            "confirmatory_count": 1,
            "key_evidence": "strongest data-originated observation in one sentence",
            "followup_threads_flagged": ["specific threads from child's self-assessment"],
            "gaps_flagged": "capability limits or self-eval gaps, if any",
            "zero_records": false,
            "purpose_aligned": true
        }}
    ],
    "synthesis": {{
        "patterns": ["cross-cutting pattern descriptions"],
        "contradictions": ["things that conflict"],
        "strongest_findings": ["most solid evidence"],
        "weakest_findings": ["most speculative"]
    }},
    "findings": [
        {{
            "type": "contradiction|gap|cross_cutting",
            "summary": "description",
            "evidence": ["from which workers"],
            "confidence": 0.8
        }}
    ],
    "adjacent_findings": [
        {{
            "description": "what was noticed outside scope",
            "source_child": "which child noticed it",
            "action": "spawn_child | escalate | record_unaddressed",
            "reasoning": "why this action"
        }}
    ],
    "followup_children": [
        {{
            "scope_description": "what to investigate — reference the specific thread or finding",
            "purpose": "why this child is needed and what question it answers",
            "data_filter": {{}},
            "parent_context": "the original child's observations and the specific thread that motivated this",
            "budget": 0.10
        }}
    ],
    "escalated_observations": [
        {{
            "raw_evidence": "the adjacent finding for grandparent consideration",
            "local_hypothesis": "why this matters",
            "observation_type": "escalated_adjacency"
        }}
    ],
    "surplus_to_return": 0.00,
    "surplus_reason": "why returning this amount — must justify why no deployment option is valuable"
}}

Respond ONLY with valid JSON, no other text.
"""
