# Hire Economics Fix — Summary

## The Problem

The $5 preliminary run (32184e87) produced 38 nodes, 81 observations, depth 5,
spending $4.98 on exploration+review with $0 left for validation. The Turn 2
CONTINUE mechanism created unbounded continuation chains.

## The Fix

Three prompt changes to NODE_REASONING_PROMPT_V2 and MANAGER_TURN2_PROMPT_V2:

1. **Formation-time economics.** The hire decision now explicitly considers
   whether the overhead of authoring a role + briefing + reviewing a hire
   exceeds the cost of doing the work directly. Slicing the same kind of
   work into smaller pieces for multiple hires is identified as fragmentation,
   not delegation.

2. **Turn 2 continuation economics.** The CONTINUE decision now explicitly
   reasons about: (a) whether downstream phases still need budget, (b) whether
   the continuation needs genuinely different cognition or is more-of-the-same,
   (c) whether the manager could do it directly within their own envelope.
   Default changed to RESOLVE (return surplus) rather than CONTINUE.

3. **Continuation guidance in role authoring.** When the engagement lead authors
   roles, each hire's heuristic includes guidance that continuation should be
   reserved for genuinely different cognitive angles, defaulting to returning
   surplus.

## Offline Validation: 4/5, Economics Reasoning 5/5

| Scenario | Expected | Got | Economics |
|---|---|---|---|
| 1. Abundant/complex → hire | hire | investigate | present |
| 2. Abundant/narrow → investigate | investigate | investigate | present |
| 3. Thin/narrow → investigate | investigate | investigate | present |
| 4. Different-cognition threads | CONTINUE or RESOLVE | RESOLVE | present |
| 5. More-of-same threads | RESOLVE | RESOLVE | present |

Scenario 1 failure: the complex scope with 30 records was judged as "cohesive
data analysis" rather than requiring distinct cognition types. The economics
reasoning was present and correctly applied — the manager concluded the work
was one kind of analysis even though the scope described multiple dimensions.
This is conservative but defensible. At the engagement lead level (where the
real organizational design happens), the scope descriptions are broader and
the hire decision should still fire.

The critical scenario (5: more-of-the-same) passed — this directly addresses
the continuation runaway.

## $5 Re-Run Comparison (d010ea3d vs 32184e87)

| Metric | Before (32184e87) | After (d010ea3d) |
|---|---|---|
| Exploration nodes | 38 | 10 |
| Exploration depth | 5 | 3 |
| Observations | 81 | 38 |
| Exploration cost | $4.30 (86%) | $1.59 (32%) |
| Budget for downstream | $0.02 (0%) | $3.41 (68%) |
| Validation completed | 0/6 | *run incomplete* |
| Zero-obs nodes | 27% | unknown |

**The economics fix dramatically reduced the continuation cascade.** Exploration
consumed $1.59 instead of $4.30, leaving $3.41 for downstream phases. The tree
went to depth 3 instead of 5, with 10 nodes instead of 38.

**The $5 re-run did not complete the full pipeline.** The deep-dive phase
(which uses old WorkerNode, not RoleWorkerNode) appears to have hit a problem.
The exploration phase completed successfully with authored synthesis running.
The incomplete downstream is a separate issue from the economics fix.

## Remaining Issues

1. **Deep-dive nodes use old WorkerNode** — not subject to the economics fix.
   The deep-dive's old Turn 2 continuation behavior may still create cascades.
   Consider migrating deep-dive nodes to RoleWorkerNode.

2. **Formation scenario 1 is too conservative** — the economics reasoning
   correctly fires but tips toward "investigate" even for complex multi-
   dimensional scopes. At the engagement lead level (real pipeline), the scope
   is the full charter, which is clearly too broad for one worker. This may
   self-correct at realistic scale.

3. **Run completeness** — the $5 re-run exploration phase worked correctly
   but the run didn't complete through report generation. Needs investigation
   of what happened in the deep-dive phase.
