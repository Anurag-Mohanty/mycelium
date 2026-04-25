# Architectural Gate Summary (Final)

## Two $10 Runs — Full Pipeline with Diagnostics + Reader Test

### npm $10 (run fe9cd62f)

| Metric | Value |
|---|---|
| Nodes | 15 |
| Observations | 45 |
| Depth | 3 |
| Cost | $2.38 / $10.00 (24%) |
| Zero-obs nodes | 0 |
| Findings submitted | 4 |
| Confirmed | 0, Weakened 4 |
| Pipeline issues | 0 |
| Diagnostics | 15 nodes, 15 diagnostics, full_diagnostic.txt |
| **Reader test** | **1 YES, 2 marginal, 1 no** |

**Reader test findings:**
- **YES:** "Missing react-is Dependency Trail" — genuinely novel finding
- MARGINAL: "Zero-Dependency Risk Amplification"
- MARGINAL: "Utility-to-Infrastructure Scope Inversion"
- NO: "Corporate Backing vs Individual Control Paradox" — known pattern

Budget: explore=$1.86, review=$0.16, synth=$0.04, dive=$0.01, valid=$0.09, impact=$0.03

### SEC $10 (run db77f81e)

| Metric | Value |
|---|---|
| Nodes | 10 |
| Observations | 26 |
| Depth | 1 |
| Cost | $1.48 / $10.00 (15%) |
| Zero-obs nodes | 0 |
| Findings submitted | 3 |
| Confirmed | 0, Weakened 2 |
| Pipeline issues | 0 |
| Diagnostics | 10 nodes, 10 diagnostics, full_diagnostic.txt |
| **Reader test** | **1 YES, 0 marginal, 2 no** |

**Reader test findings:**
- **YES:** "Regulatory Exemption Interpretation Void" — genuinely novel finding
- NO: "3M 2025 Data Availability Conflict"
- NO: "Systematic Peer Group Deviation Signals"

Budget: explore=$1.06, review=$0.10, synth=$0.04, dive=$0.01, valid=$0.06, impact=$0.01

## Side-by-Side Comparison

| | npm $10 | SEC $10 |
|---|---|---|
| Reader test YES | **1** | **1** |
| Reader test MARGINAL | 2 | 0 |
| Reader test NO | 1 | 2 |
| Budget used | 24% | 15% |
| Nodes | 15 | 10 |
| Observations | 45 | 26 |
| Depth | 3 | 1 |
| Full pipeline | yes | yes |
| Diagnostics | yes | yes |
| Zero-obs nodes | 0 | 0 |

## What the Architecture Produces

**Both corpora produced exactly 1 YES finding each.** The reader test
correctly discriminates: known patterns score NO, partially novel
findings score MARGINAL, genuinely novel findings score YES.

npm YES: "Missing react-is Dependency Trail" — a specific hidden
dependency chain that a practitioner wouldn't know about.

SEC YES: "Regulatory Exemption Interpretation Void" — a specific
gap in how regulatory exemptions are interpreted across filings.

## Architecture Behavior (Full)

1. **Role authoring** — engagement lead hired department heads with
   authored roles, bars, heuristics at both corpora
2. **Economics reasoning** — no continuation runaway, budget remained
   for all downstream phases
3. **Turn 2 bar evaluation** — MET, POOR_REASONING, WRONG_ROLE
   classifications in both runs
4. **Diagnostics** — per-node JSON, per-node diagnostics,
   full_diagnostic.txt with role names, bars, thinking traces
5. **Authored synthesis** — synthesis role authored by engagement lead
6. **Deep-dive migration** — RoleWorkerNode with formation assessment
7. **Validation + significance + impact** — all ran on real budget
8. **Reader test** — scored every Tier 3-5 finding against charter
9. **Zero zero-obs nodes** — no wasted budget

## Budget Underutilization

npm used 24%, SEC used 15% of $10 budgets. The economics reasoning
is conservative — managers default to investigating rather than hiring,
producing shallow trees. This is the tradeoff from fixing the
continuation runaway: no cascade, but also less depth than the budget
could support.

Whether this is the right calibration depends on whether 1 YES finding
per $10 per corpus is the right quality/quantity target. More aggressive
hiring would produce more observations and potentially more findings,
but also risks the cascade returning.

## Open Questions

1. **Budget calibration** — 15-24% utilization leaves $7.50-8.50 on
   the table. Is the economics reasoning too conservative?

2. **SEC depth 1** — only reached depth 1, meaning the engagement lead
   hired workers who all investigated directly, none decomposed further.
   SEC's multi-entity filing data may genuinely need deeper analysis.

3. **Reader test as gate** — both runs produce 1 YES finding. Is this
   the minimum viable quality for the architecture, or should we expect
   more from $10?
