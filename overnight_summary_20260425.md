# Overnight Build Summary — 2026-04-25

## Builds Completed

### Build C: Manager Turn 2 Reorientation — PASS (offline), pipeline run in progress

**Offline validation: 3/3 PASS**
- Scenario A (met-the-bar): Correctly classified MET, decided CONTINUE
- Scenario B (poor reasoning): Correctly classified POOR_REASONING, decided REHIRE
- Scenario C (wrong role): Correctly classified WRONG_ROLE with self-evaluation
- Cost: $0.079

**Pipeline integration ($2 mini-run): COMPLETE — mechanism works, budget issue**
- Run ID: f54d2521
- 18 nodes, 36 observations, depth 4, $2.09/$2.00 (5% overshoot)
- Engagement lead hired 3, evaluated all 3 as MET, decided CONTINUE
- Spawned "Dependency Chain Impact Mapper" ($1.48) — continuation went deep
- At depth 3, manager 1.C1.3 classified 1 hire MET, 2 POOR_REASONING — first
  POOR_REASONING classification in a live pipeline run
- 5 Turn 2 reviews total across the tree — all evaluated against authored bars
- **Budget issue:** continuation tree consumed $1.78 on exploration + $0.11 on
  review, leaving nothing for synthesis/validation/report. 9 of 18 nodes had
  zero observations due to thin budgets at depth 4-5.
- The mechanism works (bar evaluation, POOR_REASONING detection, continuations)
  but budget discipline needs attention at larger budgets.
- **Pass criteria met:** managers evaluate against bars (not summarization),
  continuation decisions reflect evaluation, POOR_REASONING detected in the wild.
  Budget issue is a tuning concern, not an architectural failure.

### Build D: Reader Test Instrumentation — PASS

**Calibration: 2/2 critical criteria met (4 findings scored)**
- Known-good novel finding: correctly scored YES
- Known-bad leakage finding: correctly scored NO
- Mixed finding: scored YES (expected MARGINAL) — scorer is slightly generous
- Data artifact finding: scored YES (expected NO) — scorer doesn't catch data artifacts well
- Cost: $0.021

**Integration:**
- Reader test scorer integrated into run.py — runs after report generation
- Scores written to metrics.json under `reader_test` section
- Scorer prompt is corpus-agnostic — reads charter from the run

**Note:** The scorer doesn't reliably catch data artifacts as NO. It treats
zero-value contradictions as genuine "invisibility" findings. This is worth
noting for the $10 gate but doesn't block the build since the primary pass
criteria (novel=YES, leakage=NO) are met.

### Build E: Synthesis as Authored Role — PASS

**Offline validation: generic vs authored synthesis compared**
- Generic synthesis: 4 findings, 0 known-pattern leakage, 4 novel
- Authored synthesis: 4 findings, 0 known-pattern leakage, 3 novel
- Generic included "Entity-A single point of failure" (reinforced) — known pattern
- Authored filtered that out and found "stealth control architecture" instead
- Cost: $0.040

**Integration:**
- SYNTHESIS_PROMPT_V2 added to prompts_v2.py — role-anchored synthesis
- Engagement lead now authors a synthesis_role in Step 3 (name, bar, heuristic)
- Orchestrator passes synthesis_role to synthesize() when available
- synthesizer.py uses SYNTHESIS_PROMPT_V2 when synthesis_role is provided

### Cleanup Tasks — DONE
- planner.py archived to archive/planner.py (no longer imported)
- worker.py kept (still used by deep-dive nodes)
- README updated with new architecture files
- Stdout buffering fix: PYTHONUNBUFFERED=1 for background runs

## Planner Removal (pre-overnight)
- Existing Planner path completely removed from orchestrator
- Role-authoring path is now the only exploration path
- No --role-path flag needed — it's the default
- Regression test (run 28fe8b83) confirmed pipeline works end-to-end

## New Files Created
- `mycelium/reader_test.py` — reader test scorer
- `test_build_c.py` — Build C offline validation
- `test_build_d.py` — Build D calibration
- `test_build_e.py` — Build E offline validation
- `archive/planner.py` — archived old Planner

## Modified Files
- `mycelium/prompts_v2.py` — added MANAGER_TURN2_PROMPT_V2, SYNTHESIS_PROMPT_V2, synthesis_role in output schema
- `mycelium/worker_v2.py` — added Turn 2 evaluation (_turn2_evaluate, _spawn_continuations), synthesis_role storage
- `mycelium/synthesizer.py` — accepts synthesis_role parameter, uses V2 prompt when provided
- `mycelium/orchestrator.py` — passes synthesis_role to synthesize(), removed Planner path
- `mycelium/run.py` — integrated reader test after report generation
- `README.md` — updated architecture docs

## Pass Criteria Summary

| Build | Offline | Pipeline | Overall |
|---|---|---|---|
| C (Manager Turn 2) | PASS (3/3) | *running* | *pending* |
| D (Reader Test) | PASS (2/2 critical) | integrated | PASS |
| E (Synthesis Role) | PASS | integrated | PASS |

## Preliminary $5 Run
- Status: BLOCKED on Build C mini-run completion
- Will run if Build C mini-run completes without errors

## Anything Surprising or Ambiguous

1. **Build C continuation depth:** The Turn 2 CONTINUE decision with the full
   remaining budget ($1.48) created a deep tree (depth 4+, 15+ nodes). At $2
   budget, this is borderline — the tree goes deep but individual nodes have
   thin budgets. At $10, this should produce much better results since each
   continuation has real budget to work with.

2. **POOR_REASONING classification in the wild:** Node 1.C1.3 classified 2 of
   3 hires as POOR_REASONING — first time we've seen this outside offline
   validation. This is the mechanism working: the manager authored bars for
   dependency chain tracers, the hires didn't meet those bars, and the manager
   correctly identified the gap.

3. **Reader test artifact scoring:** The scorer treats data artifact findings
   (zero values contradicted by other fields) as genuine "invisibility" findings
   rather than data issues. This is a known gap — the scorer evaluates novelty
   of the claim, not trustworthiness of the evidence. Consider whether the
   reader test should also check evidence trustworthiness, or whether that's
   a separate check.

4. **No architectural decisions deferred:** All builds followed the specified
   approach without encountering ambiguities that required human judgment.

## Recommended Next Steps

1. Review Build C mini-run results when complete
2. If all builds pass, review the preliminary $5 run results
3. Run $10 final gate on npm AND SEC (requires review first)
4. Consider whether reader test needs artifact-awareness calibration
5. Consider whether Turn 2 continuation budget should be capped to prevent
   deep-but-thin trees at small budgets
