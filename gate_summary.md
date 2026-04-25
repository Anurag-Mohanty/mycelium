# Architectural Gate Summary

## Two $10 Runs — Full Pipeline

### npm $10 (run 44239639)

| Metric | Value |
|---|---|
| Nodes | 31 |
| Observations | 126 |
| Depth | 3 |
| Cost | $4.60 / $10.00 (46%) |
| Zero-obs nodes | 0 |
| Findings submitted | 4 |
| Confirmed | 1 |
| Weakened | 3 |
| Pipeline issues | 2 |
| Reader test | **1 YES, 0 marginal, 0 no** |

**Confirmed finding:** "Scoped Package Download Metric Failure" — scored YES
by reader test. A practitioner would say "I didn't know that."

Budget allocation: exploration $3.79 (82%), review $0.43, synthesis $0.06,
deep-dive $0.01, validation $0.09, impact $0.03, overhead $0.20. Full
downstream pipeline ran with budget remaining.

### SEC $10 (run 73095441)

| Metric | Value |
|---|---|
| Nodes | 19 |
| Observations | 45 |
| Depth | 2 |
| Cost | $2.00 / $10.00 (20%) |
| Zero-obs nodes | 0 |
| Findings submitted | 3 |
| Confirmed | 0 |
| Weakened | 3 |
| Pipeline issues | 0 |
| Reader test | **0 YES, 3 marginal, 0 no** |

**Three marginal findings:**
1. Strategic Disclosure Manipulation vs. Compliance Framework
2. Anticipatory Legal Positioning Through Disclosure Timing
3. Parallel Disclosure Universes Within Identical Business Categories

SEC run used only 20% of budget — the economics reasoning was very
conservative on SEC data, producing a shallow tree. The findings are
interpretively interesting (strategic disclosure manipulation, anticipatory
legal positioning) but scored marginal, not yes.

## Comparison: What the Architecture Produces

| | npm $10 | SEC $10 |
|---|---|---|
| Reader test YES | 1 | 0 |
| Reader test MARGINAL | 0 | 3 |
| Reader test NO | 0 | 0 |
| Budget used | 46% | 20% |
| Observations | 126 | 45 |
| Depth | 3 | 2 |
| Zero-obs nodes | 0 | 0 |
| Full pipeline | yes | yes |

## Architecture Behavior

Both runs demonstrate the full architecture working end-to-end:

1. **Role authoring:** Engagement lead hired department heads with authored
   roles, bars, and heuristics. Each department head assessed whether to
   investigate or hire further.

2. **Economics reasoning:** Managers stopped hiring when the work didn't
   warrant delegation overhead. No continuation runaway. Budget remained
   for downstream phases.

3. **Turn 2 bar evaluation:** Managers evaluated hires against authored bars.
   MET, POOR_REASONING, and WRONG_ROLE classifications in both runs.

4. **Deep-dive migration:** Deep-dive nodes used RoleWorkerNode with
   formation assessment, economics, and Turn 2. No cascade.

5. **Authored synthesis:** Synthesis role authored by engagement lead.
   Cross-referencing anchored on the synthesis bar.

6. **Validation, significance, impact:** All ran on real budget.

7. **Reader test:** Scored findings against charter standards. npm produced
   1 YES. SEC produced 3 MARGINAL.

8. **Zero zero-obs nodes** in both runs. No wasted budget.

## Assessment

**The architecture is whole and self-regulating.** One architectural
primitive (RoleWorkerNode) applied recursively at every layer — exploration,
continuations, deep-dives. Economics reasoning prevents cascade. Turn 2
evaluates against authored bars. Reader test provides the quality gate.

**npm finding quality:** 1 YES out of 1 confirmed finding. The architecture
produced fewer findings than the old pipeline but the one it confirmed
and scored passes the reader test. Quality over quantity.

**SEC finding quality:** 3 MARGINAL, 0 YES. The SEC run was conservative
(20% budget used, depth 2). The findings are interpretively plausible but
not specific enough to clear the "I didn't know that" bar. The economics
reasoning may have been too conservative for SEC data — the shallow tree
didn't produce enough depth for the kind of cross-filing analysis that
would produce YES findings.

**Budget discipline:** npm used 46%, SEC used 20%. Both left substantial
budget for downstream. The economics fix overcorrected from the runaway
— managers are now defaulting to investigate/resolve rather than hiring,
which produces shallower trees. This may need calibration for different
corpus types — SEC's complex multi-entity filing data may need deeper
trees than npm's simpler package metadata.

## Open Questions for Review

1. **SEC budget underutilization** (20% of $10) — is the economics
   reasoning too conservative for corpora that genuinely need deeper trees?
   The formation assessment may be treating multi-entity SEC analysis as
   "cohesive" when it actually requires distinct analytical approaches.

2. **npm 1 finding vs baseline** — the pre-Phase-F baseline produced more
   findings (with leakage). Phase G produces fewer but the one that passes
   is genuinely novel. Is 1 YES finding from $10 the right quality/quantity
   tradeoff?

3. **Reader test calibration** — MARGINAL may be appropriate for SEC findings
   that have specific evidence but interpretive uncertainty. Is the bar for
   YES correctly set, or should these SEC findings score higher?
