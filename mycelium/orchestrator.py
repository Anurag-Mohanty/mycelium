"""Recursive Orchestrator — manages the exploration lifecycle.

Pipeline: Genesis → Planner → Explore → Deep-dive → Validate → Significance → Impact → Report

The orchestrator is pure plumbing:
- Runs genesis to survey corpus shape
- Runs planner to create budget-aware strategy
- Executes the plan with parallel siblings and incremental synthesis
- Runs deep-dives on the most interesting findings
- Validates Tier 3-5 findings
- Runs impact analysis on validated findings
- Tracks spending across a shared budget pool

ONE hardcoded safety check: chain circuit breaker (MAX_CHAIN_DEPTH=8).
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

from .schemas import (
    Directive, Scope, ExplorationStats, NodeResult, SynthesisResult,
    BudgetPool, ValidationResult, ImpactResult, Observation, Source,
)
from .genesis import run_genesis
from .briefer import generate_briefing
from .survey import ProgrammaticSurvey
from .node import run_node

# Minimum envelope for a productive leaf node. Roughly one Turn 1 with reduced
# thinking budget at current Sonnet pricing ($3/M in, $15/M out). Update if
# model pricing changes significantly.
LEAF_VIABLE_ENVELOPE = 0.12
from .worker import WorkerNode
from .synthesizer import synthesize
from .validator import validate_finding, check_charter_shape
from .significance import assess_significance
from .impact import analyze_impact
from .prompts import DEEP_DIVE_SELECTION_PROMPT, ANOMALY_ROUTING_PROMPT, ANOMALY_AGGREGATION_PROMPT
from . import events
from .knowledge_graph import KnowledgeGraph
from .bulletin_board import BulletinBoard
from .equip import run_equip

import anthropic

MAX_CHAIN_DEPTH = 8


class Orchestrator:
    def __init__(self, data_source, budget: float = None, output_dir: str = "output",
                 hints: list[str] = None, visualize: bool = False,
                 deliverable: str = None, obsidian: bool = True,
                 partition_gate: str = "off"):
        self.data_source = data_source
        self._initial_budget = budget  # None = interactive (user picks in browser)
        self.budget = BudgetPool(total_budget=budget or 10.0)  # placeholder until user picks
        self.output_dir = output_dir
        self.hints = hints or []
        self.visualize = visualize
        self._deliverable_connector = deliverable
        self._obsidian = obsidian
        self._partition_gate = partition_gate

        self.stats = ExplorationStats()
        self._token_log: list[dict] = []  # per-call token usage records
        self.all_node_results: list[NodeResult] = []
        self.all_syntheses: list[SynthesisResult] = []
        self.all_validations: list[ValidationResult] = []
        self.all_significance_scores: list[dict] = []
        self.all_impacts: list[ImpactResult] = []
        self.genesis_result: dict = {}
        self.plan: dict = {}
        self.start_time: float = 0.0

        self._branch_counts: list[int] = []
        self._semaphore = asyncio.Semaphore(3)
        self._node_times: list[float] = []  # seconds per node for ETA
        self._planned_nodes: int = 0
        self._last_progress_time: float = 0.0

        self.run_id = str(uuid.uuid4())[:8]
        self.run_dir = Path(output_dir) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "nodes").mkdir(exist_ok=True)

        # Knowledge graph — shared across runs, lives at project root
        self.kg = KnowledgeGraph("knowledge.db")

        # Bulletin board — lateral communication between nodes within this run
        self.bulletin_board = BulletinBoard()

    # --- Logging ---

    def _log(self, position: str, message: str):
        depth = position.count(".") if position != "ROOT" else 0
        indent = "│ " * depth
        cost_str = f"${self.budget.spent:.2f}/{self.budget.total:.2f}"
        print(f"  {indent}[{position}] {message} ({cost_str})")

    def _log_progress(self):
        """Print live progress with ETA. Throttled to every 30s."""
        now = time.time()
        if now - self._last_progress_time < 30:
            return
        self._last_progress_time = now

        elapsed = now - self.start_time
        elapsed_min = int(elapsed / 60)
        elapsed_sec = int(elapsed % 60)

        avg_bf = (sum(self._branch_counts) / len(self._branch_counts)
                  if self._branch_counts else 0)
        self.stats.avg_branching_factor = avg_bf

        pct_used = 100 - self.budget.remaining_pct()
        bar_filled = int(pct_used / 6.67)  # 15 chars total
        bar = '█' * bar_filled + '░' * (15 - bar_filled)

        # ETA calculation
        eta_str = ""
        if len(self._node_times) >= 3 and self._planned_nodes > 0:
            avg_time = sum(self._node_times[-20:]) / len(self._node_times[-20:])
            remaining_nodes = max(0, self._planned_nodes - self.stats.nodes_spawned)
            est_remaining = remaining_nodes * avg_time + 120  # +2min for synthesis/validation
            eta_min = int(est_remaining / 60)
            throughput = len(self._node_times) / elapsed if elapsed > 0 else 0
            eta_str = f" | ETA: ~{eta_min}min | {throughput:.1f} nodes/min"

        print(f"\n  Nodes: {self.stats.nodes_spawned}"
              f"{'/' + str(self._planned_nodes) + ' planned' if self._planned_nodes else ''}"
              f" | Budget: {bar} ${self.budget.spent:.2f}/${self.budget.total:.2f} ({pct_used:.0f}%)"
              f" | {elapsed_min}m{elapsed_sec:02d}s{eta_str}"
              f" | depth {self.stats.max_depth_reached}, branch {avg_bf:.1f}\n")

    # --- Main pipeline ---

    async def explore(self) -> dict:
        self.start_time = time.time()

        # Start event recording and visualizer (if not already started by interactive mode)
        if self.visualize and events._server is None:
            await events.start_server(run_dir=str(self.run_dir))
            await asyncio.sleep(1)
        elif not self.visualize:
            events.start_recording(str(self.run_dir))

        source_name = getattr(self.data_source, 'source_name', self.data_source.__class__.__name__)
        print(f"\n╔{'═'*54}╗")
        print(f"║  MYCELIUM — Engineered Curiosity{' '*21}║")
        print(f"║  Source: {source_name[:44]}{' '*(44-len(source_name[:44]))}║")
        print(f"║  Budget: ${self.budget.total:.2f}{' '*(44-len(f'{self.budget.total:.2f}'))}║")
        print(f"╚{'═'*54}╝\n")
        events.emit("source_info", {"source_name": source_name})

        # --- Phase -1: Catalog (programmatic, free) ---
        print("  [CATALOG] Scanning all accessible records...")
        events.emit("phase_change", {"phase": "catalog"})

        catalog_stats = None
        bulk_records = None
        if hasattr(self.data_source, 'fetch_bulk_metadata'):
            def _catalog_progress(p):
                fetched = p.get("fetched", 0)
                total = p.get("total_estimated", "?")
                print(f"\r  [CATALOG] {fetched}/{total} records scanned", end="", flush=True)
                events.emit("catalog_progress", {
                    "scanned": fetched,
                    "total": total,
                    "percent": round(fetched / total * 100) if isinstance(total, int) and total > 0 else 0,
                })

            bulk_records = await self.data_source.fetch_bulk_metadata(
                max_records=2000, progress_callback=_catalog_progress)
            print()  # newline after \r progress

            if bulk_records:
                # Survey caching: hash the record count + first/last record IDs
                # to detect when catalog data changes.
                import hashlib
                cache_key_parts = [
                    str(len(bulk_records)),
                    str(bulk_records[0].get("id", bulk_records[0].get("title", "")))[:50],
                    str(bulk_records[-1].get("id", bulk_records[-1].get("title", "")))[:50],
                    self.data_source.__class__.__name__,
                ]
                cache_hash = hashlib.md5("|".join(cache_key_parts).encode()).hexdigest()[:12]
                cache_path = Path("catalog") / f"survey_cache_{cache_hash}.json"

                if cache_path.exists():
                    import json as _json
                    with open(cache_path) as _f:
                        catalog_stats = _json.load(_f)
                    print(f"  [CATALOG] Survey loaded from cache ({cache_path.name})")
                else:
                    survey_engine = ProgrammaticSurvey()
                    catalog_stats = survey_engine.analyze(bulk_records)
                    # Save cache — convert numpy types to native Python for JSON
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    import json as _json
                    import numpy as _np

                    def _numpy_safe(obj):
                        if isinstance(obj, (_np.integer,)):
                            return int(obj)
                        if isinstance(obj, (_np.floating,)):
                            return float(obj) if not _np.isnan(obj) else None
                        if isinstance(obj, (_np.bool_,)):
                            return bool(obj)
                        if isinstance(obj, _np.ndarray):
                            return obj.tolist()
                        if isinstance(obj, float) and (obj != obj):  # NaN check
                            return None
                        raise TypeError(f"Not JSON serializable: {type(obj)}")

                    with open(cache_path, "w") as _f:
                        _json.dump(catalog_stats, _f, default=_numpy_safe)
                    print(f"  [CATALOG] Survey cached to {cache_path.name}")

                n_records = catalog_stats["record_count"]
                n_clusters = len(catalog_stats.get("anomaly_clusters", []))
                n_outliers = len(catalog_stats.get("outliers", []))
                n_concentrations = len(catalog_stats.get("concentrations", []))

                print(f"  [CATALOG] {n_records} records cataloged — "
                      f"{n_clusters} anomaly clusters, "
                      f"{n_outliers} outliers, "
                      f"{n_concentrations} concentrations")

                events.emit("catalog_complete", {
                    "total_records": n_records,
                    "anomaly_clusters": n_clusters,
                    "outliers": n_outliers,
                    "concentrations": n_concentrations,
                    "clusters": [
                        {"name": c["name"], "severity": c["severity"],
                         "evidence_count": c.get("evidence_count", 0),
                         "description": c.get("description", "")}
                        for c in catalog_stats.get("anomaly_clusters", [])
                    ],
                    "top_distributions": {
                        k: v for k, v in list(catalog_stats.get("distributions", {}).items())[:6]
                    },
                })

                # Store anomaly data for passing to exploration nodes
                self._survey_anomalies = {
                    "outliers": catalog_stats.get("outliers", []),
                    "unusual_combinations": catalog_stats.get("unusual_combinations", []),
                    "concentrations": catalog_stats.get("concentrations", []),
                    "content_anomalies": catalog_stats.get("content_anomalies", []),
                    "entity_anomalies": catalog_stats.get("entity_anomalies", []),
                    "graph_anomalies": catalog_stats.get("graph_anomalies", []),
                    "similarity_anomalies": catalog_stats.get("similarity_anomalies", []),
                    "velocity_anomalies": catalog_stats.get("velocity_anomalies", []),
                    "anomalies_by_technique": catalog_stats.get("anomalies_by_technique", {}),
                }

                # Translate raw stats into human-readable descriptions (one cheap LLM call)
                print("  [CATALOG] Translating findings into plain language...")
                catalog_stats = await self._translate_catalog(catalog_stats)

                for c in catalog_stats.get("anomaly_clusters", [])[:5]:
                    desc = c.get("plain_description", c["name"])
                    print(f"    [{c['severity'].upper()}] {desc}")
        else:
            print("  [CATALOG] Data source does not support bulk metadata — skipping")

        # --- Budget authorization (interactive if --budget not provided) ---
        if self._initial_budget is None and self.visualize:
            # Interactive mode: present tiers and wait for user choice
            tiers = self._compute_tiers(catalog_stats)
            events.emit("present_tiers", {
                "tiers": tiers,
                "catalog_records": catalog_stats.get("record_count", 0) if catalog_stats else 0,
            })
            print(f"\n  [WAITING] Budget tiers sent to visualizer — waiting for user selection...")

            choice = await events.wait_for_client_message(timeout=600)
            if not choice:
                print("  [ERROR] No budget selection received. Aborting.")
                return self._build_exploration_data()

            selected_budget = float(choice.get("budget", 10))
            print(f"  [AUTHORIZED] User selected ${selected_budget:.2f}")
            self.budget = BudgetPool(total_budget=selected_budget)
            events.emit("budget_authorized", {"budget": selected_budget})

        # --- Briefing (load cached, or generate if needed) ---
        from . import prompts as _prompts
        briefing_text = ""
        # Try loading cached briefing first
        source_name = self.data_source.__class__.__name__
        briefing_dir = Path("catalog/briefings")
        if briefing_dir.exists():
            for bp in sorted(briefing_dir.glob("briefing_*.md"), reverse=True):
                if source_name.lower().replace("datasource", "").replace("_", "") in bp.stem.lower().replace("_", ""):
                    briefing_text = bp.read_text()
                    print(f"\n  [BRIEFING] Loaded cached briefing ({len(briefing_text)} chars)")
                    break
        if not briefing_text and _prompts.get_version() == "v2" and bulk_records:
            print(f"\n  [BRIEFING] Generating common knowledge baseline...")
            self._briefing = await generate_briefing(
                genesis_result={"corpus_summary": ""},
                catalog_records=bulk_records,
                survey_results=catalog_stats,
                source_name=source_name,
            )
            self.budget.record("overhead", self._briefing.cost)
            self._update_tokens(self._briefing.token_usage)
            briefing_text = self._briefing.common_knowledge
            print(f"  [BRIEFING] Generated ({len(briefing_text)} chars, "
                  f"${self._briefing.cost:.3f})")
        else:
            self._briefing = None

        # --- Phase 0: Genesis (produces organizational charter) ---
        print(f"\n  [GENESIS] Generating organizational charter...")
        events.emit("phase_change", {"phase": "genesis"})
        self.genesis_result = await run_genesis(
            self.data_source, self.hints,
            catalog_records=bulk_records,
            survey_results=catalog_stats,
            briefing_text=briefing_text,
        )
        self.budget.record("overhead", self.genesis_result.get("cost", 0))
        self._update_tokens(self.genesis_result.get("token_usage", {}))

        charter = self.genesis_result.get("charter", "")
        summary = self.genesis_result.get("corpus_summary", "")
        lenses = self.genesis_result.get("lenses", [])

        print(f"  [GENESIS] Charter generated ({len(charter.split())} words)")
        print(f"  [GENESIS] First 200 chars: {charter[:200]}...")

        # Emit genesis reasoning for visualizer
        events.emit("genesis_reasoning", {
            "corpus_summary": summary,
            "lenses": lenses,
            "entry_points": [],
            "recommended_cut": "",
            "division_axes": [],
            "charter_preview": charter[:500],
        })

        if not charter:
            print("  [GENESIS] ERROR: No charter generated. Aborting.")
            return self._build_exploration_data()

        # --- Create org-level workspace (charter only — first node produces org structure) ---
        from .workspace import OrgWorkspace
        workspace_dir = self.run_dir / "workspace"
        self._workspace = OrgWorkspace(workspace_dir)
        self._workspace.write_charter(charter)
        print(f"  [WORKSPACE] Org workspace created at {workspace_dir}")
        self._workspace_path = str(workspace_dir)

        # --- EQUIP: workspace prep ---
        events.emit("phase_change", {"phase": "equip"})
        equip_result = await run_equip(
            data_source=self.data_source,
            charter=charter,
            catalog_stats=catalog_stats,
            bulletin_board=self.bulletin_board,
            budget=0.50,
        )
        equip_cost = equip_result.get("cost", 0)
        if equip_cost > 0:
            self.budget.record("overhead", equip_cost)

        if equip_result["status"] == "CANNOT_PREP":
            print(f"\n  [EQUIP] FAILED: {equip_result['reason']}")
            print(f"  Engagement cannot proceed without workspace preparation.")
            return self._build_exploration_data()

        # Exploration limit — budget is the real constraint
        self.budget.phase_limits["exploration"] = 0.80
        self.budget.phase_limits["validation"] = 0.10
        self.budget.phase_limits["synthesis"] = 0.05
        self.budget.phase_limits["impact"] = 0.05
        self._planner_envelope = {
            "exploration_envelope_pct": 0.80,
            "exploration_envelope_dollars": round(self.budget.total * 0.80, 2),
            "reasoning": "Role-authoring path: 80% exploration, 10% validation, 5% synthesis, 5% impact",
        }
        self._max_depth = 6  # permissive upper bound (safety circuit)
        self._planner_envelope["max_decomposition_depth"] = self._max_depth
        self._planner_envelope["leaf_viable_envelope"] = LEAF_VIABLE_ENVELOPE

        # --- Phase 1: Exploration (role-authoring path) ---
        print(f"\n  {'─'*54}")
        print(f"  PHASE 1: EXPLORATION")
        events.emit("phase_change", {"phase": "exploration"})
        print(f"  {'─'*54}\n")

        await self._run_role_authoring_exploration(charter, [], lenses)

        # --- Phase 2: Deep-dives ---
        await self._run_deep_dives(lenses)

        # --- Author pipeline roles from charter ---
        print(f"\n  [PIPELINE] Authoring post-exploration roles from charter...")
        await self._author_pipeline_roles(charter)

        # --- Phase 3: Validation ---
        await self._run_validation()

        # --- Phase 4: Significance gate ---
        await self._run_significance_gate()

        # --- Phase 5: Impact ---
        await self._run_impact_analysis()

        # --- Phase 6: Reader test quality gate ---
        await self._run_reader_test_gate()

        # --- Done ---
        elapsed = time.time() - self.start_time
        minutes, seconds = int(elapsed // 60), int(elapsed % 60)

        print(f"\n  {'─'*54}")
        print(f"  COMPLETE — {self.stats.nodes_spawned} nodes, "
              f"{self.stats.observations_collected} obs, "
              f"depth {self.stats.max_depth_reached}")
        print(f"  Cost: ${self.budget.spent:.2f}/{self.budget.total:.2f} "
              f"({100-self.budget.remaining_pct():.0f}% used) — {minutes}m {seconds}s")
        print(f"  Validated: {self.stats.findings_confirmed}/{self.stats.findings_validated} confirmed"
              f"  Deep-dives: {self.stats.deep_dives_executed}")
        print(f"  Phases: explore=${self.budget.phase_spent.get('exploration',0):.2f} "
              f"review=${self.budget.phase_spent.get('review',0):.2f} "
              f"synth=${self.budget.phase_spent.get('synthesis',0):.2f} "
              f"dive=${self.budget.phase_spent.get('deep_dive',0):.2f} "
              f"valid=${self.budget.phase_spent.get('validation',0):.2f} "
              f"impact=${self.budget.phase_spent.get('impact',0):.2f} "
              f"overhead=${self.budget.phase_spent.get('overhead',0):.2f}")
        print(f"  {'─'*54}")

        exploration_data = self._build_exploration_data()
        self._save_tree(exploration_data)

        # Save KG export alongside tree
        kg_stats = self.kg.stats()
        kg_export_path = self.run_dir / "knowledge_graph.json"
        with open(kg_export_path, "w") as f:
            json.dump(self.kg.export_json(), f, indent=2, default=str)
        print(f"\n  Knowledge graph: {kg_stats['entities']} entities, "
              f"{kg_stats['relationships']} relationships, "
              f"{kg_stats['contradictions']} contradictions")

        # Save bulletin board state
        bb_stats = self.bulletin_board.stats()
        self.bulletin_board.save(self.run_dir / "workspace_state.json")
        if bb_stats["total_posts"] > 0:
            print(f"  Bulletin board: {bb_stats['total_posts']} posts, "
                  f"{bb_stats['total_pulls']} pulls, "
                  f"{bb_stats['influenced_pulls']} influenced")

        events.emit("exploration_complete", {
            "total_nodes": self.stats.nodes_spawned,
            "total_observations": self.stats.observations_collected,
            "max_depth": self.stats.max_depth_reached,
            "total_cost": self.budget.spent,
            "elapsed_time": time.time() - self.start_time,
        })

        # Compute and save run metrics
        self._write_run_metrics()
        self._write_token_usage()

        # NOTE: Deliverable generation moved to run.py (after report.md exists)
        # so the findings table can be populated from the report.

        # Generate Obsidian vault
        if self._obsidian:
            try:
                from .obsidian_export import generate_vault, update_persistent_vault
                vault_path = generate_vault(str(self.run_dir), self.run_id)
                corpus = self.data_source.__class__.__name__
                persistent_path = update_persistent_vault(str(self.run_dir), self.run_id, corpus)
                import glob
                entity_count = len(glob.glob(f"{vault_path}/*.md")) - 1  # exclude _index.md
                print(f"  Obsidian vault: {entity_count} entities at {vault_path}")
                events.emit("obsidian_vault_created", {"file_count": entity_count, "path": str(vault_path)})
            except Exception as e:
                print(f"  Obsidian vault skipped: {e}")

        # Generate transcripts and dashboard
        try:
            import subprocess
            subprocess.run(
                ["python3", "build_transcripts.py", self.run_id],
                capture_output=True, timeout=30,
            )
            print(f"  Transcripts: output/{self.run_id}/transcripts/")
        except Exception:
            pass  # transcript generation is optional

        if self.visualize:
            await asyncio.sleep(2)  # let final events reach the browser
            await events.stop_server()

        return exploration_data

    # --- Recursive exploration ---

    async def _explore_node(self, directive: Directive) -> NodeResult:
        """Recursively explore. Siblings in parallel. Synthesis on completion.

        Uses atomic budget reservation: reserve before LLM call, commit after.
        Exploration phase has a hard limit — when exhausted, nodes return without work.
        """
        pos = directive.tree_position
        seg_id = directive.segment_id
        depth = pos.count(".") + 1 if pos != "ROOT" else 0

        # Atomic budget reservation — prevents race condition
        estimated_cost = self._avg_node_cost()
        reserved = await self.budget.reserve(estimated_cost, phase="exploration")
        if not reserved:
            self._log(pos, "BUDGET EXHAUSTED — skipping")
            return NodeResult(
                node_id=directive.node_id, parent_id=directive.parent_id,
                scope_description=directive.scope.description,
                survey="Exploration budget exhausted.",
                observations=[], child_directives=[],
                unresolved=[f"Scope '{directive.scope.description[:80]}' — budget exhausted"],
                raw_reasoning="", token_usage={}, cost=0.0,
            )

        # Build context strings for the node prompt
        seg_ctx = ""
        if seg_id:
            status = self.budget.segment_status(seg_id)
            seg_ctx = (
                f"- Segment '{seg_id}' target: ${status['target']:.2f}, "
                f"spent: ${status['spent']:.2f}, "
                f"remaining: ${status['remaining_target']:.2f}"
                f"{' (OVER TARGET — be selective)' if status['over_target'] else ''}\n"
            )

        capacity_ctx = self._capacity_context()

        self.stats.nodes_spawned += 1
        self.stats.max_depth_reached = max(self.stats.max_depth_reached, depth)

        self._log(pos, f"Exploring: {directive.scope.description[:60]}...")
        events.emit("node_spawned", {
            "node_id": directive.node_id, "parent_id": directive.parent_id,
            "tree_position": pos, "scope_summary": directive.scope.description[:100],
            "segment_id": seg_id,
        })

        # Semaphore only wraps the LLM call, NOT recursive children.
        node_start = time.time()
        try:
            async with self._semaphore:
                node_result = await run_node(
                    directive, self.data_source,
                    self.budget.remaining(), self.budget.total,
                    segment_context=seg_ctx,
                    capacity_context=capacity_ctx,
                )
            self._node_times.append(time.time() - node_start)
            await self.budget.commit(estimated_cost, node_result.cost, "exploration", seg_id)
        except Exception:
            await self.budget.release(estimated_cost)
            raise

        self.all_node_results.append(node_result)
        self.stats.observations_collected += len(node_result.observations)
        self._update_tokens(node_result.token_usage)
        self._save_node(node_result)
        self._populate_kg(node_result)
        self._emit_reasoning(directive.node_id, node_result.thinking)

        n_obs = len(node_result.observations)
        n_children = len(node_result.child_directives)

        events.emit("budget_update", {
            "total_spent": self.budget.spent, "total_remaining": self.budget.remaining(),
            "percent_used": 100 - self.budget.remaining_pct(),
        })

        if n_children > 0:
            self._branch_counts.append(n_children)
            self._log(pos, f"{n_obs} observations + decomposing → {n_children} areas")
            events.emit("node_decomposing", {
                "node_id": directive.node_id, "tree_position": pos,
                "children_count": n_children, "observations_count": n_obs,
            })
        else:
            self.stats.nodes_resolved += 1
            self._log(pos, f"RESOLVED: {n_obs} observations")
            top_obs = node_result.observations[0].what_i_saw[:80] if node_result.observations else ""
            events.emit("node_resolved", {
                "node_id": directive.node_id, "tree_position": pos,
                "observations_count": n_obs, "cost_spent": node_result.cost,
                "top_observation": top_obs,
            })

        if not node_result.child_directives:
            return node_result

        # Check exploration hard gate before spawning children
        if self.budget.exploration_exhausted:
            self._log(pos, "Exploration phase limit reached — not spawning children")
            return node_result

        # Propagate segment_id to children
        for child in node_result.child_directives:
            if not child.segment_id:
                child.segment_id = seg_id

        # Run children in parallel — each child reserves its own budget atomically
        tasks = [self._explore_node(child) for child in node_result.child_directives]
        children_results = list(await asyncio.gather(*tasks)) if tasks else []

        # Incremental synthesis
        if children_results and self.budget.can_spend():
            self._log(pos, f"SYNTHESIZING {len(children_results)} branches...")
            events.emit("node_status", {"node_id": directive.node_id, "tree_position": pos, "status": "synthesizing"})
            # Light synthesis for leaf parents (all children resolved directly),
            # full synthesis for branch parents (children had their own children)
            all_leaves = all(len(c.child_directives) == 0 for c in children_results)
            syn = await synthesize(node_result, children_results, directive.lenses, light=all_leaves)
            self.all_syntheses.append(syn)
            self.budget.record("synthesis", syn.cost, seg_id)
            self._update_tokens(syn.token_usage)
            for c in syn.contradictions:
                events.emit("finding_discovered", {"node_id": directive.node_id, "summary": str(c.get("what_conflicts", ""))[:80], "type": "contradiction"})
            for p in syn.cross_cutting:
                events.emit("finding_discovered", {"node_id": directive.node_id, "summary": str(p.get("pattern", ""))[:80], "type": "cross_cutting"})

            for child in children_results:
                node_result.observations.extend(child.observations)
        elif children_results:
            for child in children_results:
                node_result.observations.extend(child.observations)

        self._log_progress()

        return node_result

    # --- Deep-dive phase ---

    async def _run_deep_dives(self, lenses: list[str]):
        available = self.budget.deep_dive_available()
        if available < 0.05:
            print("\n  ⚠ DEEP-DIVE SKIPPED — insufficient budget remaining")
            return

        # Collect findings for selection
        findings_summary = self._summarize_findings()
        observations_summary = self._summarize_top_observations()

        if not findings_summary and not observations_summary:
            print("\n  ⚠ DEEP-DIVE SKIPPED — no findings to investigate")
            return

        print(f"\n  {'─'*54}")
        print(f"  PHASE 2: DEEP DIVES (${available:.2f} available)")
        events.emit("phase_change", {"phase": "deep_dive"})
        print(f"  {'─'*54}\n")

        # Ask LLM which findings to investigate
        prompt = DEEP_DIVE_SELECTION_PROMPT.format(
            findings_summary=findings_summary or "(no synthesis findings yet)",
            observations_summary=observations_summary,
            deep_dive_budget=available,
        )

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000
        self.budget.record("deep_dive", cost)
        self._update_tokens(usage)

        try:
            selection = _parse_json(raw)
        except (json.JSONDecodeError, ValueError):
            print("  Deep-dive selection failed to parse. Skipping.")
            return

        targets = selection.get("targets", [])
        if not targets:
            print("  No deep-dive targets selected.")
            return

        from .worker_v2 import RoleWorkerNode
        from .schemas import RoleDefinition

        for i, target in enumerate(targets, 1):
            if not self.budget.can_spend():
                break

            desc = target.get("investigation_directive", target.get("finding_summary", ""))
            filters = target.get("search_filters", {})
            print(f"  [DEEP-DIVE {i}] {desc[:70]}...")

            # Author a role for this deep-dive from the selection context
            dd_role = RoleDefinition(
                name="deep-dive investigator",
                mission=(
                    f"Produce the definitive account of this finding — trace every "
                    f"connection, quantify every impact, name every entity involved: "
                    f"{desc[:150]}"
                ),
                success_bar=(
                    f"Trace this specific finding to its full extent with named entities "
                    f"and exact figures: {desc[:200]}"
                ),
                heuristic=(
                    "This is targeted follow-up, not broad exploration. Favor depth on "
                    "the specific thread over breadth. Do not hire unless the thread "
                    "genuinely splits into distinct sub-investigations."
                ),
            )

            directive = Directive(
                scope=Scope(
                    source=self.data_source.__class__.__name__,
                    filters=filters,
                    description=desc,
                ),
                lenses=lenses,
                parent_context=f"DEEP DIVE: {target.get('why_this_one', '')}. "
                               f"Trace this finding to its full extent.",
                purpose=target.get("investigation_directive", target.get("why_this_one", "")),
                tree_position=f"DD.{i}",
                segment_id="deep_dive",
                workspace_path=getattr(self, '_workspace_path', None),
                role=dd_role,
            )

            worker = RoleWorkerNode(
                directive=directive,
                data_source=self.data_source,
                budget=min(available / len(targets), self.budget.remaining()),
                total_budget=self.budget.total,
                semaphore=self._semaphore,
                budget_pool=self.budget,
                parent_pool_available=self.budget.deep_dive_available(),
                depth=0,
                max_depth=self._max_depth,
                leaf_viable_envelope=LEAF_VIABLE_ENVELOPE,
                bulletin_board=self.bulletin_board,
                partition_gate=self._partition_gate,
            )
            worker_result = await worker.run()

            # Collect results from deep-dive worker tree
            self._collect_worker_stats_v2(worker)
            self._collect_worker_node_results_v2(worker)
            self._write_node_files_v2(worker)
            self._write_diagnostics_v2(worker)
            self.stats.deep_dives_executed += 1

            n_obs = len(worker.observations)
            print(f"  [DEEP-DIVE {i}] RESOLVED: {n_obs} observations")

    # --- Validation ---

    async def _run_validation(self):
        findings = []
        for syn in self.all_syntheses:
            for c in syn.contradictions:
                findings.append(("contradiction", c))
            for p in syn.cross_cutting:
                findings.append(("cross_cutting_pattern", p))

        if not findings:
            print("\n  ⚠ VALIDATION SKIPPED — no Tier 3-5 findings to validate")
            return

        print(f"\n  {'─'*54}")
        print(f"  PHASE 3: VALIDATION ({len(findings)} findings)")
        events.emit("phase_change", {"phase": "validation"})
        print(f"  {'─'*54}\n")

        for i, (ftype, finding) in enumerate(findings, 1):
            if not self.budget.can_spend():
                print(f"  ⚠ VALIDATION TRUNCATED — budget exhausted after {i-1}/{len(findings)} validations")
                break

            desc = str(finding.get("what_conflicts", finding.get("pattern", "")))[:60]
            print(f"  [VALIDATE {i}/{len(findings)}] {ftype}: {desc}...")

            # Node-based validator with corpus access and four parallel operations
            briefing_text = self._briefing.common_knowledge if self._briefing else ""
            result = await validate_finding(
                f"{ftype}_{i}", ftype, finding,
                data_source=self.data_source,
                run_dir=str(self.run_dir),
                briefing_text=briefing_text,
            )
            self.budget.record("validation", result.cost)
            self._update_tokens(result.token_usage)

            # Charter-shape check: does this finding match an excluded shape?
            charter_exclusions = ""
            if hasattr(self, '_workspace') and self._workspace:
                charter_text = self._workspace.read_charter()
                if charter_text:
                    from .worker_v2 import _extract_charter_section
                    charter_exclusions = _extract_charter_section(charter_text, "EXCLUSIONS")

            finding_claim = desc
            if result.revised_finding:
                finding_claim = str(result.revised_finding)[:300]

            shape_check = await check_charter_shape(
                finding_claim, charter_exclusions, charter_text=charter_text or "")
            shape_cost = shape_check.get("cost", 0)
            if shape_cost > 0:
                self.budget.record("validation", shape_cost)

            # Store shape check result on validation result
            result.charter_shape_check = shape_check
            shape_verdict = shape_check.get("verdict", "no_check")
            shape_action = shape_check.get("recommended_action", "pass")

            self.all_validations.append(result)
            self.stats.findings_validated += 1
            if result.verdict in ("confirmed", "confirmed_with_caveats"):
                self.stats.findings_confirmed += 1

            # Log both verdicts
            shape_note = ""
            if shape_verdict == "matches_exclusion":
                shape_note = f" | CHARTER: {shape_action} ({shape_check.get('matched_exclusion', '?')})"
            elif shape_verdict == "partial_match":
                shape_note = f" | CHARTER: partial ({shape_check.get('matched_exclusion', '?')})"
            print(f"    → {result.verdict} (confidence: {result.adjusted_confidence:.2f}){shape_note}")

            events.emit("validation_result", {
                "finding_summary": desc,
                "verdict": result.verdict,
                "confidence": result.adjusted_confidence,
                "charter_shape_verdict": shape_verdict,
                "charter_shape_action": shape_action,
            })

    # --- Significance gate ---

    async def _run_significance_gate(self):
        """Filter validated findings by novelty and actionability before impact analysis."""
        confirmed = [v for v in self.all_validations if v.verdict in ("confirmed", "confirmed_with_caveats", "weakened", "needs_verification")]
        if not confirmed:
            return

        print(f"\n  {'─'*54}")
        print(f"  PHASE 5: SIGNIFICANCE GATE ({len(confirmed)} findings)")
        events.emit("phase_change", {"phase": "significance"})
        print(f"  {'─'*54}\n")

        self.all_significance_scores = []
        for i, v in enumerate(confirmed, 1):
            if not self.budget.can_spend():
                print(f"  Budget exhausted after {i-1}/{len(confirmed)} significance checks")
                break

            desc = str(v.revised_finding or v.original_finding.get("what_conflicts", "")
                       or v.original_finding.get("pattern", ""))[:60]
            print(f"  [SIGNIFICANCE {i}/{len(confirmed)}] {desc}...")

            briefing_for_sig = self._briefing.common_knowledge if self._briefing else ""
            sig_role = getattr(self, '_pipeline_roles', {}).get("significance")
            result = await assess_significance(
                v.finding_id, v.original_finding,
                {"verdict": v.verdict, "revised_finding": v.revised_finding,
                 "adjusted_confidence": v.adjusted_confidence},
                briefing_text=briefing_for_sig,
                role=sig_role)
            self.all_significance_scores.append(result)
            self.budget.record("validation", result.get("cost", 0))
            self._update_tokens(result.get("token_usage", {}))

            score = result.get("composite_score", 0)
            tier = result.get("tier_assignment", "noted")
            headline = result.get("headline", "")[:50]
            print(f"    → {tier.upper()} (score: {score:.1f}) — {headline}")
            events.emit("significance_scored", {
                "finding_summary": desc,
                "composite": score,
                "tier": tier,
                "novelty": result.get("novelty", 0),
                "actionability": result.get("actionability", 0),
            })

        # Summary
        headlines = sum(1 for s in self.all_significance_scores if s.get("tier_assignment") == "headline")
        significant = sum(1 for s in self.all_significance_scores if s.get("tier_assignment") == "significant")
        noted = sum(1 for s in self.all_significance_scores if s.get("tier_assignment") == "noted")
        print(f"\n  Gate results: {headlines} headlines, {significant} significant, {noted} noted")

    # --- Impact (only for headline/significant findings) ---

    async def _run_impact_analysis(self):
        # Only run impact on findings that passed significance gate (3.0+)
        to_impact = [s for s in getattr(self, 'all_significance_scores', [])
                     if s.get("recommendation") == "proceed_to_impact"
                     or s.get("tier_assignment") in ("headline", "significant")]

        # Sort by score — highest first so we get the best findings if budget runs out
        to_impact.sort(key=lambda s: s.get("composite_score", 0), reverse=True)

        if not to_impact:
            print("\n  ⚠ IMPACT SKIPPED — no findings scored high enough for impact analysis")
            return

        # Match significance scores back to validation results
        validated_map = {v.finding_id: v for v in self.all_validations}

        print(f"\n  {'─'*54}")
        print(f"  PHASE 6: IMPACT ANALYSIS ({len(to_impact)} findings, ${self.budget.remaining():.2f} available)")
        events.emit("phase_change", {"phase": "impact"})
        print(f"  {'─'*54}\n")

        for i, sig in enumerate(to_impact, 1):
            if not self.budget.can_spend(0.01):  # lower threshold — impact is critical
                print(f"  ⚠ Budget exhausted after {i-1}/{len(to_impact)} impact analyses")
                break
            fid = sig.get("finding_id", "")
            v = validated_map.get(fid)
            if not v:
                print(f"  ⚠ Finding {fid} not found in validation map — skipping")
                continue

            desc = sig.get("headline", str(v.revised_finding or v.original_finding.get("pattern", "")))
            print(f"  [IMPACT {i}/{len(to_impact)}] {str(desc)[:60]}...")

            result = await analyze_impact(
                fid, str(desc), v.original_finding, v.adjusted_confidence)
            self.all_impacts.append(result)
            self.budget.record("impact", result.cost)
            self._update_tokens(result.token_usage)
            print(f"    → Urgency: {result.urgency}")
            events.emit("impact_result", {
                "finding_summary": str(desc)[:100],
                "urgency": result.urgency,
                "who_affected": ", ".join(result.affected_parties[:3]),
                "action": result.actionability[:100] if result.actionability else "",
            })

    # --- Reader test quality gate ---

    async def _run_reader_test_gate(self):
        """Score findings with reader test and gate inclusion in report.

        Runs after validation + significance + impact, before report generation.
        Reader test scores affect tier assignment and report inclusion:
        - "yes" → stays at current tier
        - "yes_factual" → stays but annotated (interpretation uncertain)
        - "marginal" → demoted one tier
        - "no" → excluded from main report, listed in appendix
        Charter-shape "reject" findings also excluded.
        """
        if not self.all_validations:
            return

        # Load charter and briefing
        charter_text = ""
        briefing_text = self._briefing.common_knowledge if self._briefing else ""
        if hasattr(self, '_workspace') and self._workspace:
            charter_text = self._workspace.read_charter() or ""

        if not charter_text:
            return

        print(f"\n  {'─'*54}")
        print(f"  PHASE 6: READER TEST GATE ({len(self.all_validations)} findings)")
        events.emit("phase_change", {"phase": "reader_test"})
        print(f"  {'─'*54}\n")

        from .reader_test import score_findings

        # Build finding list for scoring
        findings_for_scoring = []
        for v in self.all_validations:
            if v.verdict == "refuted":
                continue  # already filtered by significance gate
            summary = v.original_finding.get("what_conflicts",
                      v.original_finding.get("pattern", ""))
            evidence = v.original_finding.get("evidence_chain",
                       v.original_finding.get("side_a", ""))
            findings_for_scoring.append({
                "summary": summary,
                "evidence": str(evidence)[:2000],
                "validation_status": v.verdict,
                "finding_id": v.finding_id,
            })

        if not findings_for_scoring:
            print("  ⚠ No findings to score")
            return

        # Score findings — pass briefing-augmented charter
        augmented_charter = charter_text
        if briefing_text:
            augmented_charter += (
                f"\n\n## COMMON KNOWLEDGE BRIEFING\n"
                f"(What a domain practitioner already knows — findings that restate "
                f"this content should score NO on factual novelty)\n\n{briefing_text}"
            )

        reader_role = getattr(self, '_pipeline_roles', {}).get("reader_test")
        scores = await score_findings(augmented_charter, findings_for_scoring[:10],
                                       role=reader_role)
        total_cost = sum(s.get("cost", 0) for s in scores)
        self.budget.record("overhead", total_cost)

        # Apply scores to validations
        self._reader_test_scores = scores
        excluded_reader = []
        demoted = []

        for score in scores:
            idx = score.get("finding_index", -1)
            if idx < 0 or idx >= len(findings_for_scoring):
                continue
            fid = findings_for_scoring[idx].get("finding_id", "")
            combined = score.get("score", "no")

            # Find the matching validation result
            for v in self.all_validations:
                if v.finding_id == fid:
                    v.reader_test_score = combined
                    v.reader_test_reasoning = score.get("reasoning", "")

                    # Charter-shape rejection
                    shape = getattr(v, 'charter_shape_check', None) or {}
                    if shape.get("recommended_action") == "reject":
                        v.reader_test_gate = "excluded_charter_shape"
                        excluded_reader.append((v, "charter-shape reject"))
                    elif combined == "no":
                        v.reader_test_gate = "excluded_no_novelty"
                        excluded_reader.append((v, f"reader test: no"))
                    elif combined == "marginal":
                        v.reader_test_gate = "demoted"
                        demoted.append(v)
                    else:
                        v.reader_test_gate = "passed"
                    break

        # Summary
        passed = sum(1 for v in self.all_validations
                     if getattr(v, 'reader_test_gate', '') == 'passed')
        print(f"  Reader test: {passed} passed, {len(demoted)} demoted, "
              f"{len(excluded_reader)} excluded (${total_cost:.3f})")
        for v, reason in excluded_reader:
            desc = v.original_finding.get("what_conflicts",
                   v.original_finding.get("pattern", ""))
            print(f"    EXCLUDED: {str(desc)[:60]} — {reason}")

    # --- Pipeline role authoring ---

    async def _author_pipeline_roles(self, charter: str):
        """Author roles for post-exploration pipeline components from the charter.

        Each role is authored by the LLM based on the charter's domain and
        standards, replacing hardcoded rubrics in the synthesizer, reader test,
        significance scorer, and reporter.
        """
        prompt = (
            f"You are designing the post-exploration analysis team for an investigation.\n\n"
            f"CHARTER:\n{charter}\n\n"
            f"Author roles for four pipeline components. Each role should be specific "
            f"to THIS charter's domain and standards — not generic.\n\n"
            f"Return JSON:\n"
            f'{{\n'
            f'  "synthesizer": {{\n'
            f'    "name": "role name",\n'
            f'    "mission": "what this synthesizer should produce — what counts as a finding worth promoting given this charter",\n'
            f'    "bar": "minimum acceptable — what would make a candidate finding fail",\n'
            f'    "heuristic": "when uncertain about whether to include a finding, lean toward..."\n'
            f'  }},\n'
            f'  "reader_test": {{\n'
            f'    "name": "role name — the knowledgeable reader persona",\n'
            f'    "mission": "what this reader evaluates — what makes a finding notable for this domain",\n'
            f'    "bar": "minimum — what a finding must have for this reader to say I did not know that",\n'
            f'    "heuristic": "when uncertain about novelty, lean toward..."\n'
            f'  }},\n'
            f'  "significance": {{\n'
            f'    "name": "role name",\n'
            f'    "mission": "how to assess significance — what matters in this domain",\n'
            f'    "bar": "minimum for a finding to be significant rather than merely noted",\n'
            f'    "heuristic": "when balancing novelty vs actionability, lean toward..."\n'
            f'  }},\n'
            f'  "reporter": {{\n'
            f'    "name": "role name",\n'
            f'    "mission": "how to present findings — what should lead, what is supporting context",\n'
            f'    "bar": "minimum quality for the report — what would make it fail",\n'
            f'    "heuristic": "when organizing findings, lead with..."\n'
            f'  }}\n'
            f'}}\n\n'
            f"Respond ONLY with valid JSON."
        )

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
        self.budget.record("overhead", cost)

        raw = response.content[0].text
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            roles = json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            roles = {}

        self._pipeline_roles = roles
        for component, role in roles.items():
            print(f"  [PIPELINE ROLE] {component}: {role.get('name', '?')}")

        # Save to workspace for inspection
        roles_path = self.run_dir / "workspace" / "pipeline_roles.json"
        roles_path.parent.mkdir(parents=True, exist_ok=True)
        with open(roles_path, "w") as f:
            json.dump(roles, f, indent=2)
        return roles

    def _write_token_usage(self):
        """Write per-node token usage with cache stats to workspace."""
        usage_data = {"by_node": [], "totals": {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        }}
        for nr in self.all_node_results:
            tu = nr.token_usage
            entry = {
                "node_id": nr.node_id[:8],
                "tree_position": nr.tree_position,
                "scope": nr.scope_description[:60],
                "input_tokens": tu.get("input_tokens", 0),
                "output_tokens": tu.get("output_tokens", 0),
                "cache_read": tu.get("cache_read_input_tokens", 0),
                "cache_write": tu.get("cache_creation_input_tokens", 0),
                "cost": nr.cost,
            }
            usage_data["by_node"].append(entry)
            for k in ("input_tokens", "output_tokens",
                       "cache_read_input_tokens", "cache_creation_input_tokens"):
                usage_data["totals"][k] = usage_data["totals"].get(k, 0) + tu.get(k, 0)

        # Cache hit rate
        total_input = usage_data["totals"]["input_tokens"]
        cache_read = usage_data["totals"]["cache_read_input_tokens"]
        usage_data["cache_hit_rate"] = (cache_read / total_input * 100) if total_input > 0 else 0

        path = self.run_dir / "workspace" / "token_usage.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(usage_data, f, indent=2)

    # --- Budget & context helpers ---

    def _avg_node_cost(self) -> float:
        """Running average cost per node. Used for budget reservations."""
        if len(self.all_node_results) >= 5:
            recent = [r.cost for r in self.all_node_results[-20:] if r.cost > 0]
            if recent:
                return sum(recent) / len(recent)
        return 0.05  # default estimate before we have data

    async def _translate_catalog(self, catalog_stats: dict) -> dict:
        """One cheap LLM call to translate raw statistical cluster names into plain language."""
        clusters = catalog_stats.get("anomaly_clusters", [])
        if not clusters:
            return catalog_stats

        cluster_summaries = json.dumps([
            {"name": c.get("name", ""), "severity": c.get("severity", ""),
             "description": c.get("description", ""), "evidence_count": c.get("evidence_count", 0)}
            for c in clusters[:10]
        ], indent=2)

        client = anthropic.Anthropic()
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=800,
                messages=[{"role": "user", "content": f"""These are anomaly clusters found by a statistical scan of a dataset.
The names are raw field names and statistical descriptions. Translate each into a clear,
one-sentence plain-language description that a non-technical person would understand.

CLUSTERS:
{cluster_summaries}

Return JSON array — one plain_description per cluster, same order:
[
    "plain language description 1",
    "plain language description 2",
    ...
]

Respond ONLY with a JSON array."""}],
            )
            raw = response.content[0].text
            cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
            self.budget.record("overhead", cost)

            # Parse
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                descriptions = json.loads(raw[start:end])
                for i, desc in enumerate(descriptions):
                    if i < len(clusters):
                        clusters[i]["plain_description"] = desc
        except Exception:
            pass  # if translation fails, raw names are used as fallback

        return catalog_stats

    def _compute_tiers(self, catalog_stats: dict = None) -> list[dict]:
        """Compute investigation tiers from actual catalog data.

        Estimates are based on:
        - Number of anomaly clusters found
        - Number of records cataloged
        - Estimated $0.04/node, ~20 nodes per dollar
        - Estimated ~2 nodes/minute with parallelism
        """
        clusters = catalog_stats.get("anomaly_clusters", []) if catalog_stats else []
        n_clusters = len(clusters)
        n_records = catalog_stats.get("record_count", 0) if catalog_stats else 0

        # Use plain language descriptions if available, fall back to raw names
        cluster_descs = [c.get("plain_description", c.get("name", "unnamed")) for c in clusters]

        def _tier_clusters(n):
            """Top N cluster descriptions."""
            top = cluster_descs[:n]
            if not top:
                return "General exploration"
            return "; ".join(c[:60] for c in top)

        # Estimate nodes and time per budget
        # ~20 nodes per dollar, ~2 nodes per minute
        def _estimate(budget):
            nodes = int(budget * 20)
            minutes = int(nodes / 2)
            if minutes < 60:
                time_str = f"~{minutes} min"
            else:
                time_str = f"~{minutes // 60}h {minutes % 60}m"
            return nodes, time_str

        scout_nodes, scout_time = _estimate(5)
        focused_nodes, focused_time = _estimate(15)
        balanced_nodes, balanced_time = _estimate(50)
        deep_nodes, deep_time = _estimate(200)

        return [
            {"name": "Scout", "budget": 5, "time_estimate": scout_time,
             "estimated_nodes": scout_nodes,
             "clusters_covered": min(3, n_clusters),
             "depth": f"Top {min(3, n_clusters)} anomalies: {_tier_clusters(3)}"},
            {"name": "Focused", "budget": 15, "time_estimate": focused_time,
             "estimated_nodes": focused_nodes,
             "clusters_covered": min(5, n_clusters),
             "depth": f"Top {min(5, n_clusters)} anomalies: {_tier_clusters(5)}"},
            {"name": "Balanced", "budget": 50, "time_estimate": balanced_time,
             "estimated_nodes": balanced_nodes,
             "clusters_covered": n_clusters,
             "depth": f"All {n_clusters} anomaly clusters, thorough analysis"},
            {"name": "Deep", "budget": 200, "time_estimate": deep_time,
             "estimated_nodes": deep_nodes,
             "clusters_covered": n_clusters,
             "depth": f"All {n_clusters} clusters + full cross-referencing of {n_records} records"},
        ]

    def _collect_worker_stats(self, workers: list):
        """Collect stats from WorkerNode tree for reporting."""
        def _walk(worker):
            self.stats.nodes_spawned += 1
            self.stats.observations_collected += len(worker.observations)
            depth = worker.pos.count(".") + 1 if worker.pos != "ROOT" else 0
            self.stats.max_depth_reached = max(self.stats.max_depth_reached, depth)
            if not worker.child_workers:
                self.stats.nodes_resolved += 1
            for child in worker.child_workers:
                _walk(child)

        for w in workers:
            _walk(w)

        # Save worker nodes to disk
        self._save_worker_tree(workers)

    def _collect_worker_node_results(self, workers: list):
        """Walk the worker tree and populate all_node_results for downstream pipeline."""
        def _walk(worker):
            self.all_node_results.append(self._worker_result_to_node_result(worker._result()))
            self._populate_kg(self.all_node_results[-1])
            for child in worker.child_workers:
                _walk(child)

        for w in workers:
            _walk(w)

    def _save_worker_tree(self, workers: list):
        """Save all worker nodes to disk for the report."""
        def _save(worker):
            node_data = {
                "node_id": worker.node_id,
                "parent_id": worker.directive.parent_id,
                "scope_description": worker.directive.scope.description,
                "tree_position": worker.pos,
                "survey": "",
                "observations": worker.observations,
                "child_directives_count": len(worker.child_workers),
                "unresolved": [],
                "raw_reasoning": "",
                "thinking_log": worker.thinking_log,  # preserve turn-level structure
                "thinking": "\n\n".join(t.get("thinking", "") for t in worker.thinking_log),  # backward compat
                "turn2_review": getattr(worker, '_turn2_output', None),  # structured Turn 2 output
                "metrics": worker.metrics,  # self-assessment: signal strength, follow-up threads, etc.
                "token_usage": {},
                "cost": worker.spent,
            }
            node_file = self.run_dir / "nodes" / f"{worker.node_id[:8]}.json"
            with open(node_file, "w") as f:
                json.dump(node_data, f, indent=2, default=str)
            for child in worker.child_workers:
                _save(child)

        for w in workers:
            _save(w)

    def _write_diagnostics(self, workers: list):
        """Write per-node diagnostic logs and print summary."""
        diag_dir = self.run_dir / "diagnostics"
        diag_dir.mkdir(exist_ok=True)

        all_diags = []

        def _collect(worker):
            diag = worker._build_diagnostic()
            all_diags.append(diag)
            # Write individual diagnostic file
            diag_file = diag_dir / f"{worker.node_id[:8]}.json"
            with open(diag_file, "w") as f:
                json.dump(diag, f, indent=2, default=str)
            for child in worker.child_workers:
                _collect(child)

        for w in workers:
            _collect(w)

        if not all_diags:
            return

        # Compute summary stats
        total = len(all_diags)
        zero_obs = sum(1 for d in all_diags if d["output"]["observations_count"] == 0)
        with_targets = sum(1 for d in all_diags if d.get("anomaly_targets_received", {}).get("count", 0) > 0)
        targets_with_evidence = sum(
            1 for d in all_diags
            if any(t.get("has_evidence") for t in d["anomaly_targets_received"].get("targets", []))
        )
        decomposed = sum(1 for d in all_diags if d["decision"] == "decomposed")
        gaps_flagged = sum(
            1 for d in all_diags
            if not d["self_evaluation"].get("purpose_addressed", True)
        )
        evidence_cited = sum(d["output"].get("evidence_cited", 0) for d in all_diags)
        total_obs = sum(d["output"]["observations_count"] for d in all_diags)

        # Find best and worst nodes
        best = max(all_diags, key=lambda d: d["output"]["observations_count"])
        worst = [d for d in all_diags if d["output"]["observations_count"] == 0]

        print(f"\n  {'═'*54}")
        print(f"  NODE DIAGNOSTIC SUMMARY")
        print(f"  {'─'*54}")
        print(f"  Total nodes: {total}")
        print(f"  Nodes with 0 observations: {zero_obs} ({zero_obs*100//max(1,total)}%)")
        print(f"  Nodes that received anomaly targets: {with_targets} ({with_targets*100//max(1,total)}%)")
        print(f"  Nodes whose targets included evidence: {targets_with_evidence} ({targets_with_evidence*100//max(1,total)}%)")
        print(f"  Nodes that decomposed: {decomposed} ({decomposed*100//max(1,total)}%)")
        print(f"  Nodes where self-eval flagged gaps: {gaps_flagged} ({gaps_flagged*100//max(1,total)}%)")
        print(f"  Observations citing evidence: {evidence_cited}/{total_obs}")
        print(f"  {'─'*54}")
        print(f"  SAMPLE NODE (highest obs count = {best['output']['observations_count']}):")
        print(f"    Position: {best['tree_position']}")
        print(f"    Purpose: {best['purpose'][:100]}")
        print(f"    Data: {best['data_received'].get('record_count', 0)} records")
        print(f"    Targets: {best['anomaly_targets_received']['count']} anomalies")
        print(f"    Decision: {best['decision']}")
        if best["output"].get("sample_observation"):
            sample = best["output"]["sample_observation"]
            print(f"    Sample obs: {str(sample.get('raw_evidence', ''))[:150]}")
        if worst:
            w = worst[0]
            print(f"  SAMPLE NODE (0 observations):")
            print(f"    Position: {w['tree_position']}")
            print(f"    Purpose: {w['purpose'][:100]}")
            print(f"    Data: {w['data_received'].get('record_count', 0)} records")
            print(f"    Targets: {w.get('anomaly_targets_received', {}).get('count', 0)} anomalies")
        print(f"  {'═'*54}")

        # Write full_diagnostic.txt with every node's input/output
        full_path = self.run_dir / "full_diagnostic.txt"
        with open(full_path, "w") as f:
            f.write(f"{'='*70}\n")
            f.write(f"RUN {self.run_id} — FULL NODE DIAGNOSTIC\n")
            f.write(f"{'='*70}\n")
            f.write(f"Nodes: {total} | Zero-obs: {zero_obs} | With evidence: {targets_with_evidence}\n")
            f.write(f"Decomposed: {decomposed} | Self-eval gaps: {gaps_flagged}\n")
            f.write(f"Observations: {total_obs} ({evidence_cited} citing evidence)\n\n")

            for d in all_diags:
                f.write(f"{'─'*70}\n")
                f.write(f"NODE {d['tree_position']} [{d['decision'].upper()}] ${d['budget']['spent']:.3f}\n")
                f.write(f"{'─'*70}\n")
                f.write(f"SCOPE: {d['scope']}\n")
                f.write(f"PURPOSE: {d['purpose']}\n")
                f.write(f"DATA: {d.get('data_received', {}).get('record_count', 0)} records\n")
                tgts = d.get('anomaly_targets_received', {})
                tc = tgts.get('count', 0)
                te = sum(1 for t in tgts.get('targets', []) if t.get('has_evidence'))
                f.write(f"TARGETS: {tc} ({te} with evidence)\n")
                for t in tgts.get('targets', [])[:5]:
                    ev = " [+evidence]" if t.get("has_evidence") else ""
                    f.write(f"  [{t.get('type','?')}] {t.get('description','')[:120]}{ev}\n")
                if tc > 5:
                    f.write(f"  ... and {tc - 5} more\n")

                se = d.get('self_evaluation') or {}
                f.write(f"SELF-EVAL: addressed={se.get('purpose_addressed')} | "
                        f"quality={se.get('evidence_quality')} | "
                        f"gap={str(se.get('purpose_gap',''))[:120]}\n")

                # Load observations from node file
                nid = d['node_id']
                node_file = self.run_dir / "nodes" / f"{nid[:8]}.json"
                try:
                    with open(node_file) as nf:
                        node_data = json.load(nf)
                    obs_list = node_data.get("observations", [])
                    f.write(f"OUTPUT: {len(obs_list)} observations, {d['output']['children_spawned']} children\n")
                    for i, obs in enumerate(obs_list, 1):
                        raw = obs.get("raw_evidence", obs.get("what_i_saw", ""))
                        grounding = obs.get("statistical_grounding", "")
                        hypothesis = obs.get("local_hypothesis", obs.get("reasoning", ""))
                        src = obs.get("source", {})
                        src_id = src.get("doc_id", src.get("title", "?")) if isinstance(src, dict) else str(src)[:30]
                        f.write(f"  [{i}] {obs.get('observation_type', '?')} | src: {src_id}\n")
                        f.write(f"      EVIDENCE: {str(raw)[:250]}\n")
                        if grounding:
                            f.write(f"      GROUNDING: {str(grounding)[:200]}\n")
                        if hypothesis:
                            f.write(f"      HYPOTHESIS: {str(hypothesis)[:200]}\n")
                except FileNotFoundError:
                    f.write(f"  (no node file)\n")
                f.write("\n")

    def _worker_result_to_node_result(self, result: dict) -> NodeResult:
        """Convert a WorkerNode result dict to a NodeResult for synthesis."""
        observations = []
        for obs in result.get("observations", []):
            src = obs.get("source", {})
            observations.append(Observation(
                node_id=result.get("node_id", ""),
                raw_evidence=obs.get("raw_evidence", obs.get("what_i_saw", "")),
                source=Source(
                    doc_id=src.get("doc_id", ""),
                    title=src.get("title", ""),
                    agency=src.get("agency", ""),
                    date=src.get("date", ""),
                    url=src.get("url", ""),
                ),
                observation_type=obs.get("observation_type", "pattern"),
                statistical_grounding=obs.get("statistical_grounding", ""),
                local_hypothesis=obs.get("local_hypothesis", obs.get("reasoning", "")),
                confidence=obs.get("confidence", 0.5),
                surprising_because=obs.get("surprising_because", ""),
                escalated_adjacency=obs.get("escalated_adjacency", False),
                unaddressed_adjacency=obs.get("unaddressed_adjacency", False),
            ))
        return NodeResult(
            node_id=result.get("node_id", ""),
            parent_id=result.get("parent_id"),
            scope_description=result.get("scope_description", ""),
            survey="",
            observations=observations,
            child_directives=[],
            unresolved=[],
            raw_reasoning="",
            thinking="\n".join(t.get("thinking", "") for t in result.get("thinking_log", [])),
            tree_position=result.get("tree_position", ""),
            token_usage=result.get("token_usage", {}),
            cost=result.get("cost", 0),
        )

    def _capacity_context(self) -> str:
        """How many more exploration nodes can we afford, based on actuals."""
        if self.stats.nodes_spawned < 10:
            return ""
        avg = self._avg_node_cost()
        if avg <= 0:
            return ""
        explore_remaining = self.budget.exploration_remaining()
        est_nodes = int(explore_remaining / avg)
        return (
            f"Based on actual costs (avg ${avg:.3f}/node), approximately "
            f"{est_nodes} more exploration nodes can be created. "
            f"Plan your decomposition accordingly."
        )

    def _emit_reasoning(self, node_id: str, thinking: str):
        """Emit the extended thinking block as events for the visualizer.

        The thinking is genuine chain-of-thought from the LLM. We split it
        into displayable chunks and emit each as a node_thinking event.
        """
        if not thinking:
            return

        # Split into paragraphs
        paragraphs = [p.strip() for p in thinking.split("\n\n") if p.strip()]

        # Merge short paragraphs, cap ~300 chars per chunk
        chunks = []
        current = ""
        for p in paragraphs:
            if len(current) + len(p) < 350:
                current += ("\n" + p if current else p)
            else:
                if current:
                    chunks.append(current)
                current = p
        if current:
            chunks.append(current)
        if not chunks:
            chunks = [thinking[:300]]

        for i, chunk in enumerate(chunks):
            events.emit("node_thinking", {
                "node_id": node_id,
                "turn": "initial",
                "chunk_index": i,
                "total_chunks": len(chunks),
                "text": chunk[:300],
            })

    # --- Helpers ---

    def _populate_kg(self, node_result: NodeResult):
        """Extract entities and relationships from observations and add to knowledge graph."""
        corpus = self.data_source.__class__.__name__
        for obs in node_result.observations:
            # Add the primary entity (the source item)
            entity_name = obs.source.title or obs.source.doc_id
            if not entity_name:
                continue
            entity_type = "item"
            # Try to infer type from observation_type
            if obs.observation_type in ("dependency_risk", "single_point_of_failure"):
                entity_type = "risk_entity"

            self.kg.add_observation(
                entity_name=entity_name,
                claim=obs.what_i_saw,
                source_node_id=obs.node_id,
                source_run_id=self.run_id,
                confidence=max(obs.preliminary_relevance.values()) if obs.preliminary_relevance else 0.5,
                observation_type=obs.observation_type,
                entity_type=entity_type,
                corpus=corpus,
            )

            # Add the author/maintainer as a related entity if present
            if obs.source.agency and obs.source.agency != "unknown":
                self.kg.add_relationship(
                    from_name=entity_name,
                    to_name=obs.source.agency,
                    relationship_type="maintained_by",
                    confidence=0.8,
                    evidence=obs.what_i_saw[:200],
                    from_type=entity_type,
                    to_type="person_or_org",
                    corpus=corpus,
                )

            # Add connections from potential_connections
            for conn in obs.potential_connections:
                self.kg.add_relationship(
                    from_name=entity_name,
                    to_name=conn,
                    relationship_type="related_to",
                    confidence=0.3,
                    evidence=obs.what_i_saw[:200],
                    corpus=corpus,
                )

    def _populate_reasoning_records(self, worker):
        """Extract reasoning quality records from a RoleWorkerNode into the knowledge graph."""
        source_name = getattr(self.data_source, 'source_name',
                              self.data_source.__class__.__name__)
        role = getattr(worker, '_role', None)
        role_name = role.name if role else None
        bar = role.success_bar if role else None
        heuristic = getattr(role, 'heuristic', None) if role else None
        mission = getattr(role, 'mission', None) if role else None

        # Role record
        self.kg.add_role_record(
            run_id=self.run_id,
            node_id=worker.node_id,
            parent_id=worker.directive.parent_id,
            role_name=role_name,
            mission=mission,
            bar=bar,
            heuristic=heuristic,
            scope_description=worker.directive.scope.description[:200],
            budget=worker.budget,
            corpus=source_name,
            tree_position=worker.pos,
            depth=worker.depth,
        )

        # Decision record: formation (investigate vs hire)
        formation = "hire" if worker.child_workers else "investigate"
        self.kg.add_decision_record(
            run_id=self.run_id,
            node_id=worker.node_id,
            decision_type="formation",
            outcome=formation,
            reasoning_summary="",
        )

        # Decision record: turn2 if present
        turn2 = getattr(worker, '_turn2_result', None)
        if turn2 and isinstance(turn2, dict):
            self.kg.add_decision_record(
                run_id=self.run_id,
                node_id=worker.node_id,
                decision_type="turn2",
                outcome=turn2.get("option_chosen", ""),
                reasoning_summary=str(turn2.get("option_reasoning", ""))[:500],
            )

        # Outcome record
        metrics = worker.metrics if isinstance(worker.metrics, dict) else {}
        turn2_class = metrics.get("turn2_classification", "")
        reader_scores = metrics.get("reader_test_scores", {})
        validation = metrics.get("validation_outcomes", {})

        self.kg.add_outcome_record(
            run_id=self.run_id,
            node_id=worker.node_id,
            observation_count=len(worker.observations),
            budget_allocated=worker.budget,
            budget_spent=worker.spent,
            turn2_classification=turn2_class,
            reader_test_scores=reader_scores if isinstance(reader_scores, dict) else {},
            validation_outcomes=validation if isinstance(validation, dict) else {},
        )

    def _update_tokens(self, usage: dict):
        self.stats.total_tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        self.stats.api_calls += 1

    def _summarize_findings(self) -> str:
        lines = []
        for syn in self.all_syntheses:
            for c in syn.contradictions:
                lines.append(f"- CONTRADICTION: {c.get('what_conflicts', '')}")
            for p in syn.cross_cutting:
                lines.append(f"- PATTERN: {p.get('pattern', '')}")
            for q in syn.discovered_questions:
                lines.append(f"- QUESTION: {q}")
        return "\n".join(lines) if lines else ""

    def _summarize_top_observations(self) -> str:
        # Pick the most interesting observations (those with reasoning mentioning risk/anomaly)
        all_obs = []
        for nr in self.all_node_results:
            for obs in nr.get("observations", []) if isinstance(nr, dict) else nr.observations:
                if isinstance(obs, dict):
                    all_obs.append(obs)
                else:
                    all_obs.append({
                        "raw_evidence": obs.raw_evidence,
                        "source": {"doc_id": obs.source.doc_id, "title": obs.source.title},
                        "observation_type": obs.observation_type,
                        "local_hypothesis": obs.local_hypothesis,
                    })

        # Take up to 15 most interesting
        lines = []
        for obs in all_obs[:15]:
            lines.append(f"- [{obs.get('observation_type', '?')}] {obs.get('raw_evidence', obs.get('what_i_saw', ''))[:150]}")
        return "\n".join(lines) if lines else ""

    def _build_exploration_data(self) -> dict:
        elapsed = time.time() - self.start_time
        return {
            "run_id": self.run_id,
            "hints": self.hints,
            "plan": self.plan,
            "stats": {
                "nodes_spawned": self.stats.nodes_spawned,
                "nodes_resolved": self.stats.nodes_resolved,
                "observations_collected": self.stats.observations_collected,
                "max_depth_reached": self.stats.max_depth_reached,
                "total_tokens": self.stats.total_tokens,
                "total_cost": self.budget.spent,
                "budget": self.budget.total,
                "phase_costs": dict(self.budget.phase_spent),
                "segment_costs": dict(self.budget.segment_spent),
                "api_calls": self.stats.api_calls,
                "elapsed_seconds": elapsed,
                "avg_branching_factor": self.stats.avg_branching_factor,
                "chain_breaker_fired": self.stats.chain_breaker_fired,
                "findings_validated": self.stats.findings_validated,
                "findings_confirmed": self.stats.findings_confirmed,
                "deep_dives_executed": self.stats.deep_dives_executed,
            },
            "genesis": self.genesis_result,
            "node_results": [_result_to_dict(r) for r in self.all_node_results],
            "syntheses": [_synthesis_to_dict(s) for s in self.all_syntheses],
            "validations": [_validation_to_dict(v) for v in self.all_validations],
            "significance_scores": self.all_significance_scores,
            "impacts": [_impact_to_dict(im) for im in self.all_impacts],
            "unaddressed_adjacencies": self._collect_adjacencies(),
        }

    def _collect_adjacencies(self) -> list[dict]:
        """Collect unaddressed and escalated adjacency observations for the report."""
        adjacencies = []
        for nr in self.all_node_results:
            for obs in nr.observations:
                if getattr(obs, 'unaddressed_adjacency', False) or \
                   getattr(obs, 'escalated_adjacency', False):
                    adjacencies.append({
                        "raw_evidence": obs.raw_evidence,
                        "observation_type": obs.observation_type,
                        "local_hypothesis": obs.local_hypothesis,
                        "source_node": nr.node_id,
                        "escalated": getattr(obs, 'escalated_adjacency', False),
                        "unaddressed": getattr(obs, 'unaddressed_adjacency', False),
                    })
        return adjacencies

    def _save_node(self, result: NodeResult):
        node_file = self.run_dir / "nodes" / f"{result.node_id[:8]}.json"
        with open(node_file, "w") as f:
            json.dump(_result_to_dict(result), f, indent=2, default=str)

    def _save_tree(self, data: dict):
        tree_file = self.run_dir / "tree.json"
        with open(tree_file, "w") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"\n  Tree saved to {tree_file}")

    def _flatten_anomalies(self, all_anomalies: dict) -> list[dict]:
        """Flatten survey anomaly dict into a list of uniform entries with evidence."""
        flat = []

        def _build_evidence(a: dict) -> dict:
            if "evidence" in a:
                return a["evidence"]
            skip = {"type", "description", "record", "entity", "flagged_by", "indices"}
            return {k: v for k, v in a.items() if k not in skip and v is not None}

        for outlier in all_anomalies.get("outliers", []):
            flat.append({
                "type": "outlier",
                "record": outlier.get("record_id", "?"),
                "description": f"{outlier.get('record_id', '?')}: {outlier.get('field', '?')}="
                               f"{outlier.get('value', '?')} (z-score {outlier.get('z_score', '?')})",
                "flagged_by": ["basic_statistics"],
                "evidence": {"field": outlier.get("field"), "value": outlier.get("value"),
                             "z_score": outlier.get("z_score"), "direction": outlier.get("direction"),
                             "record_summary": outlier.get("record_summary", "")},
            })
        for combo in all_anomalies.get("unusual_combinations", []):
            flat.append({"type": "unusual_combination", "description": combo.get("description", ""),
                         "flagged_by": ["keyword_signals"],
                         "evidence": {"description": combo.get("description", ""),
                                      "overrepresentation": combo.get("overrepresentation")}})
        for conc in all_anomalies.get("concentrations", []):
            top_vals = conc.get("top_values", [])
            dominant = top_vals[0]["value"] if top_vals else ""
            flat.append({"type": "concentration",
                         "description": f"{conc.get('concentration_pct', '?')}% in {conc.get('field', '?')}",
                         "flagged_by": ["entity_concentration"],
                         "evidence": {"field": conc.get("field"), "concentration_pct": conc.get("concentration_pct"),
                                      "dominant_value": dominant,
                                      "top_values": [{"value": v["value"], "pct": v["pct"]} for v in top_vals[:3]]}})
        for key in ("content_anomalies", "entity_anomalies", "graph_anomalies",
                    "similarity_anomalies", "velocity_anomalies"):
            for a in all_anomalies.get(key, []):
                flat.append({"type": a.get("type", "anomaly"), "description": a.get("description", ""),
                             "record": a.get("entity", a.get("record", "")),
                             "flagged_by": [key.replace("_anomalies", "")],
                             "evidence": _build_evidence(a)})
        for tech_key, tech_data in all_anomalies.get("anomalies_by_technique", {}).items():
            if not isinstance(tech_data, dict):
                continue
            for a in tech_data.get("anomalies", []):
                if not isinstance(a, dict):
                    continue
                flat.append({"type": a.get("type", tech_key), "description": a.get("description", ""),
                             "record": a.get("entity", a.get("record", "")),
                             "flagged_by": [tech_key], "evidence": _build_evidence(a)})
        return flat

    async def _run_role_authoring_exploration(self, charter: str, segments: list, lenses: list) -> dict:
        """Role-authoring path: first node runs formation-time assessment, hires recursively."""
        from .worker_v2 import RoleWorkerNode
        from .schemas import RoleDefinition

        print(f"\n  [ROLE PATH] Using role-authoring exploration path")

        # Create the first node — engagement lead
        exploration_budget = self.budget.total * self.budget.phase_limits["exploration"]
        first_role = RoleDefinition(
            name="engagement lead",
            mission=(
                "Produce the most insightful investigation this budget can yield. "
                "Build a team that uncovers what nobody else has noticed — hidden "
                "structural relationships, unexpected failure modes, patterns that "
                "would make a domain expert say 'I didn't know that.' Engage fully "
                "with every line of inquiry. When a line is exhausted — the data "
                "examined, patterns documented, further probing would yield variations "
                "not new shapes — conclude honestly and redirect budget to lines that "
                "can still yield. Excellence is thorough engagement, not exhaustive "
                "continuation."
            ),
            success_bar=(
                "Design and staff an organization that produces findings meeting "
                "the charter's standards. Success means: the team you hire covers "
                "the engagement's territory without gaps or overlaps, each hire has "
                "a concrete bar you can judge their output against, and the findings "
                "that come back would satisfy the person who wrote the charter."
            ),
            heuristic=(
                "When uncertain whether to investigate directly or hire, ask: "
                "does this scope require distinct kinds of work that a single "
                "pass cannot cover? If yes, hire. If the work is cohesive and "
                "fits your budget, investigate."
            ),
        )

        first_directive = Directive(
            scope=Scope(
                source=self.data_source.__class__.__name__,
                filters={},
                description=charter,  # Full charter — engagement lead needs all exclusions
            ),
            lenses=lenses,
            parent_context="You are the first node. The charter above is your engagement directive.",
            purpose="Design and execute the investigation the charter demands.",
            node_id=str(uuid.uuid4()),
            tree_position="1",
            segment_id="root",
            survey_anomalies=getattr(self, '_flat_anomalies', []),
            workspace_path=self._workspace_path,
            role=first_role,
        )

        first_node = RoleWorkerNode(
            directive=first_directive,
            data_source=self.data_source,
            budget=exploration_budget,
            total_budget=self.budget.total,
            semaphore=self._semaphore,
            budget_pool=self.budget,
            parent_pool_available=self.budget.exploration_remaining(),
            depth=0,
            max_depth=self._max_depth,
            leaf_viable_envelope=LEAF_VIABLE_ENVELOPE,
            bulletin_board=self.bulletin_board,
            partition_gate=self._partition_gate,
        )

        print(f"  [ROLE PATH] First node: {first_role.name} (${exploration_budget:.2f})")

        # Run the first node — it will recursively hire and investigate
        result = await first_node.run()

        # Collect results from the role-authoring tree
        self._segment_workers = [first_node]
        self._collect_worker_stats_v2(first_node)
        self._collect_worker_node_results_v2(first_node)
        self._write_node_files_v2(first_node)
        self._write_diagnostics_v2(first_node)

        # Print diagnostic summary
        print(f"\n  {'═'*54}")
        print(f"  NODE DIAGNOSTIC SUMMARY (ROLE PATH)")
        print(f"  {'─'*54}")
        print(f"  Total nodes: {self.stats.nodes_spawned}")
        print(f"  Observations: {self.stats.observations_collected}")
        print(f"  Max depth: {self.stats.max_depth_reached}")
        print(f"  {'═'*54}")

        # Extract synthesis role from engagement lead's output (if authored)
        synthesis_role = result.get("synthesis_role") if isinstance(result, dict) else None

        # Synthesize if we have observations
        if len(self.all_node_results) > 1 and self.budget.can_spend():
            # Load workspace context for synthesis
            ws_context = ""
            if hasattr(self, '_workspace_path') and self._workspace_path:
                from .workspace import OrgWorkspace
                ws = OrgWorkspace(self._workspace_path)
                ws_charter = ws.read_charter()
                if ws_charter:
                    ws_context = f"## ORGANIZATIONAL CHARTER\n\n{ws_charter}\n\n"

            # Use pipeline-authored role if available, else exploration-authored
            pipeline_synth = getattr(self, '_pipeline_roles', {}).get("synthesizer")
            active_role = pipeline_synth or synthesis_role
            if active_role:
                print(f"\n  [ROOT] SYNTHESIZING with role: {active_role.get('name', '?')}")
            else:
                print(f"\n  [ROOT] SYNTHESIZING all observations...")
            virtual_root = NodeResult(
                node_id="root", parent_id=None,
                scope_description="Root synthesis across all departments",
                survey="", observations=[], child_directives=[],
                unresolved=[], raw_reasoning="",
            )
            synth_inputs = list(self.all_node_results)
            root_synthesis = await synthesize(
                virtual_root, synth_inputs, lenses,
                synthesis_role=active_role,
                workspace_context=ws_context,
                data_source=self.data_source,
            )
            self.all_syntheses.append(root_synthesis)
            self.budget.record("synthesis", root_synthesis.cost)
            self._update_tokens(root_synthesis.token_usage)

        self._log_progress()

    def _collect_worker_stats_v2(self, root_worker):
        """Collect stats from the role-authoring worker tree."""
        def walk(worker):
            self.stats.nodes_spawned += 1
            if not worker.child_workers:
                self.stats.nodes_resolved += 1
            self.stats.observations_collected += len(worker.observations)
            self.stats.max_depth_reached = max(self.stats.max_depth_reached, worker.depth)
            self.stats.total_tokens += (
                worker.token_usage.get("input_tokens", 0) +
                worker.token_usage.get("output_tokens", 0)
            )
            self.stats.api_calls += 1
            if worker.child_workers:
                self._branch_counts.append(len(worker.child_workers))
            for child in worker.child_workers:
                walk(child)
        walk(root_worker)
        if self._branch_counts:
            self.stats.avg_branching_factor = sum(self._branch_counts) / len(self._branch_counts)

    def _collect_worker_node_results_v2(self, root_worker):
        """Collect NodeResults from role-authoring worker tree."""
        def walk(worker):
            obs_objects = []
            for obs_data in worker.observations:
                if isinstance(obs_data, dict):
                    src = obs_data.get("source", {})
                    obs_objects.append(Observation(
                        node_id=worker.node_id,
                        raw_evidence=obs_data.get("raw_evidence", ""),
                        source=Source(
                            doc_id=src.get("doc_id", ""),
                            title=src.get("title", ""),
                            agency=src.get("agency", ""),
                            date=src.get("date", ""),
                            section=src.get("section", ""),
                            url=src.get("url", ""),
                        ),
                        observation_type=obs_data.get("observation_type", "pattern"),
                        statistical_grounding=obs_data.get("statistical_grounding", ""),
                        local_hypothesis=obs_data.get("local_hypothesis", ""),
                        confidence=obs_data.get("confidence", 0.5),
                        surprising_because=obs_data.get("surprising_because", ""),
                    ))
            nr = NodeResult(
                node_id=worker.node_id,
                parent_id=worker.directive.parent_id,
                scope_description=worker.directive.scope.description[:200],
                survey="",
                observations=obs_objects,
                child_directives=[],
                unresolved=[],
                raw_reasoning="",
                thinking=worker.thinking_log[0]["thinking"] if worker.thinking_log else "",
                tree_position=worker.pos,
                token_usage=worker.token_usage,
                cost=worker.spent,
            )
            self.all_node_results.append(nr)
            self._populate_kg(nr)
            self._populate_reasoning_records(worker)
            for child in worker.child_workers:
                walk(child)
        walk(root_worker)

    def _write_node_files_v2(self, root_worker):
        """Write per-node JSON files from the role-authoring worker tree."""
        nodes_dir = self.run_dir / "nodes"
        nodes_dir.mkdir(exist_ok=True)

        def walk(worker):
            node_json = worker._build_node_json()
            node_file = nodes_dir / f"{worker.node_id[:8]}.json"
            with open(node_file, "w") as f:
                json.dump(node_json, f, indent=2, default=str)
            for child in worker.child_workers:
                walk(child)
        walk(root_worker)

    def _write_diagnostics_v2(self, root_worker):
        """Write per-node diagnostic logs from the role-authoring worker tree."""
        diag_dir = self.run_dir / "diagnostics"
        diag_dir.mkdir(exist_ok=True)

        all_diags = []

        def collect(worker):
            diag = worker._build_diagnostic()
            all_diags.append(diag)
            diag_file = diag_dir / f"{worker.node_id[:8]}.json"
            with open(diag_file, "w") as f:
                json.dump(diag, f, indent=2, default=str)
            for child in worker.child_workers:
                collect(child)
        collect(root_worker)

        if not all_diags:
            return

        # Write full_diagnostic.txt
        total = len(all_diags)
        zero_obs = sum(1 for d in all_diags if d["output"]["observations_count"] == 0)
        hired = sum(1 for d in all_diags if d["decision"] == "hired")
        total_obs = sum(d["output"]["observations_count"] for d in all_diags)

        full_path = self.run_dir / "full_diagnostic.txt"
        is_first_write = not full_path.exists() or full_path.stat().st_size == 0
        with open(full_path, "a") as f:
            if is_first_write:
                f.write(f"{'='*70}\n")
                f.write(f"RUN {self.run_id} — FULL NODE DIAGNOSTIC\n")
                f.write(f"{'='*70}\n\n")
            f.write(f"--- Phase: {root_worker.pos.split('.')[0] if '.' in root_worker.pos else root_worker.pos} ---\n")
            f.write(f"Nodes: {total} | Zero-obs: {zero_obs} | Hired: {hired}\n")
            f.write(f"Total observations: {total_obs}\n\n")

            for d in all_diags:
                f.write(f"{'─'*70}\n")
                f.write(f"NODE {d['tree_position']} [{d['decision'].upper()}] "
                        f"${d['budget']['spent']:.3f} | Role: {d['role']}\n")
                f.write(f"{'─'*70}\n")
                f.write(f"BAR: {d['role_bar']}\n")
                f.write(f"SCOPE: {d['scope']}\n")
                f.write(f"PURPOSE: {d['purpose']}\n")
                f.write(f"DATA: {d['data_received'].get('record_count', 0)} records\n")
                f.write(f"OUTPUT: {d['output']['observations_count']} observations, "
                        f"{d['output']['children_spawned']} children\n")
                if d['output'].get('sample_observation'):
                    sample = d['output']['sample_observation']
                    f.write(f"  SAMPLE: {str(sample.get('raw_evidence', ''))[:150]}\n")
                if d.get('thinking_summary'):
                    f.write(f"THINKING (first 500): {d['thinking_summary'][:500]}\n")
                f.write("\n")

    async def _aggregate_anomalies(self, all_anomalies: dict) -> tuple[list[dict], str]:
        """Aggregate anomalies into pattern clusters via one LLM call.

        Returns (flat_anomaly_list, pattern_summary_text).
        The pattern summary is what gets sent to per-segment routing calls.
        """
        flat = self._flatten_anomalies(all_anomalies)
        if not flat:
            return [], "No anomalies found."

        # Format numbered list — context-derived cap
        sample_str = json.dumps(flat[0], default=str) if flat else "{}"
        tokens_per = max(1, len(sample_str) // 3)
        max_anomalies = max(50, 180_000 // tokens_per)
        to_send = flat[:max_anomalies]

        lines = []
        for i, a in enumerate(to_send):
            lines.append(f"[{i}] [{a.get('type', '?')}] {a.get('record', '')}: {a.get('description', '')[:150]}")

        prompt = ANOMALY_AGGREGATION_PROMPT.format(anomaly_list="\n".join(lines))

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
            self.budget.record("overhead", cost)

            output = response.content[0].text
            result = json.loads(output) if output.strip().startswith("{") else \
                json.loads(output[output.find("{"):output.rfind("}") + 1])

            patterns = result.get("patterns", [])
            # Build human-readable summary for routing
            summary_lines = []
            for i, p in enumerate(patterns):
                reps = p.get("representative_indices", [])
                rep_descs = [to_send[j].get("description", "")[:80] for j in reps if 0 <= j < len(to_send)]
                summary_lines.append(
                    f"[{i}] {p.get('name', '?')} ({p.get('anomaly_count', '?')} anomalies): "
                    f"{p.get('description', '')}\n    Examples: {'; '.join(rep_descs[:3])}"
                )

            pattern_summary = "\n".join(summary_lines)
            print(f"  Aggregated {len(flat)} anomalies into {len(patterns)} patterns (${cost:.3f})")

            # Store patterns with their representative anomalies for later extraction
            self._aggregated_patterns = patterns
            self._flat_anomalies = to_send
            return flat, pattern_summary

        except Exception as e:
            print(f"  ⚠ Anomaly aggregation failed: {e}")
            # Return raw summary as fallback
            summary = f"{len(flat)} anomalies found across {len(set(a.get('type') for a in flat))} types."
            return flat, summary

    async def _route_patterns_to_segment(self, pattern_summary: str,
                                          flat_anomalies: list[dict],
                                          segment: dict) -> list[dict]:
        """Route aggregated patterns to a segment via one cheap LLM call.

        Receives the pre-computed pattern summary (not raw anomalies).
        On failure: returns empty list.
        """
        prompt = ANOMALY_ROUTING_PROMPT.format(
            segment_name=segment.get("name", ""),
            segment_scope=segment.get("scope_description", segment.get("name", "")),
            segment_reasoning=segment.get("reasoning", ""),
            pattern_summary=pattern_summary,
        )

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
            self.budget.record("overhead", cost)

            output = response.content[0].text
            result = json.loads(output) if output.strip().startswith("{") else \
                json.loads(output[output.find("{"):output.rfind("}") + 1])

            # Map pattern indices back to representative anomalies
            pattern_indices = result.get("relevant_pattern_indices", [])
            patterns = getattr(self, '_aggregated_patterns', [])
            selected = []
            for pi in pattern_indices:
                if 0 <= pi < len(patterns):
                    for ai in patterns[pi].get("representative_indices", []):
                        if 0 <= ai < len(flat_anomalies):
                            selected.append(flat_anomalies[ai])
            return selected

        except Exception as e:
            print(f"    ⚠ Pattern routing failed for '{segment.get('name', '?')}': {e}")
            return []

    def _write_run_metrics(self):
        """Compute and write run metrics from existing data. No LLM calls."""
        elapsed = time.time() - self.start_time

        # Collect token usage from worker tree
        total_tokens = {"input_tokens": 0, "output_tokens": 0}
        all_workers = []

        def _walk_workers(worker):
            all_workers.append(worker)
            total_tokens["input_tokens"] += worker.token_usage.get("input_tokens", 0)
            total_tokens["output_tokens"] += worker.token_usage.get("output_tokens", 0)
            for child in worker.child_workers:
                _walk_workers(child)

        for w in getattr(self, '_segment_workers', []):
            _walk_workers(w)

        # Count zero-obs nodes and self-eval gaps
        zero_obs_nodes = sum(1 for w in all_workers if not w.observations)
        zero_obs_cost = sum(w.spent for w in all_workers if not w.observations)
        self_eval_gaps = sum(1 for w in all_workers
                            if not w.metrics.get("purpose_addressed", True))

        # Count signal strength categories across all observations
        signal_counts = {"data_originated_novel": 0, "data_originated_confirmatory": 0,
                         "confirmatory": 0, "data_originated": 0, "unset": 0}
        for w in all_workers:
            for obs in w.observations:
                sig = obs.get("signal_strength", "")
                if sig in signal_counts:
                    signal_counts[sig] += 1
                elif sig:
                    signal_counts[sig] = signal_counts.get(sig, 0) + 1
                else:
                    signal_counts["unset"] += 1

        # Count commonly_known findings from significance scoring
        commonly_known_count = sum(
            1 for s in getattr(self, 'all_significance_scores', [])
            if s.get("commonly_known", False))

        # Count spawn rejections
        depth_cap_rejections = 0
        envelope_floor_rejections = 0
        for w in all_workers:
            for rej in getattr(w, '_spawn_rejections', []):
                if rej.get("reason") == "depth_cap":
                    depth_cap_rejections += 1
                elif rej.get("reason") == "envelope_floor":
                    envelope_floor_rejections += 1

        # Count validated findings
        # Count synthesis quality — findings with specific data points
        synthesis_with_specifics = 0
        total_data_points = 0
        for s in self.all_syntheses:
            for c in s.contradictions:
                has_specifics = bool(
                    c.get("side_a", {}).get("specific_data_points") or
                    c.get("side_b", {}).get("specific_data_points"))
                if has_specifics:
                    synthesis_with_specifics += 1
                    total_data_points += len(c.get("side_a", {}).get("specific_data_points", []))
                    total_data_points += len(c.get("side_b", {}).get("specific_data_points", []))
            for p in s.cross_cutting:
                chain = p.get("evidence_chain", [])
                has_specifics = any(
                    isinstance(e, dict) and e.get("specific_data_points")
                    for e in chain) if chain else False
                if has_specifics:
                    synthesis_with_specifics += 1
                    for e in chain:
                        if isinstance(e, dict):
                            total_data_points += len(e.get("specific_data_points", []))
        total_synthesis_findings = sum(
            len(s.contradictions) + len(s.cross_cutting) for s in self.all_syntheses)

        confirmed = sum(1 for v in self.all_validations if v.verdict == "confirmed")
        confirmed_with_caveats = sum(1 for v in self.all_validations if v.verdict == "confirmed_with_caveats")
        weakened = sum(1 for v in self.all_validations if v.verdict == "weakened")
        refuted = sum(1 for v in self.all_validations if v.verdict == "refuted")
        needs_verification = sum(1 for v in self.all_validations if v.verdict == "needs_verification")
        pipeline_issues = sum(1 for v in self.all_validations if v.is_pipeline_issue)
        total_validated = len(self.all_validations)
        corpus_validated = total_validated - pipeline_issues

        total_obs = self.stats.observations_collected
        total_nodes = len(all_workers) if all_workers else self.stats.nodes_spawned
        total_cost = self.budget.spent

        # Enrichment data
        enriched_count = len(getattr(self.data_source, '_enriched_filings', []))
        source_name = self.data_source.__class__.__name__

        metrics = {
            "run_id": self.run_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": source_name,
            "cost": {
                "total": round(total_cost, 3),
                "by_phase": {k: round(v, 3) for k, v in self.budget.phase_spent.items()},
                "per_node": round(total_cost / max(1, total_nodes), 3),
                "per_observation": round(total_cost / max(1, total_obs), 3),
                "per_validated_finding": round(total_cost / max(1, confirmed), 3) if confirmed else None,
                "budget_authorized": self.budget.total,
                "budget_utilization": round(total_cost / max(0.01, self.budget.total), 3),
                "wasted_on_zero_obs": round(zero_obs_cost, 3),
            },
            "quality": {
                "total_observations": total_obs,
                "findings_submitted": total_validated,
                "findings_confirmed": confirmed,
                "findings_confirmed_with_caveats": confirmed_with_caveats,
                "findings_weakened": weakened,
                "findings_refuted": refuted,
                "findings_needs_verification": needs_verification,
                "findings_pipeline_issue": pipeline_issues,
                "validation_rate_corpus": round(
                    sum(1 for v in self.all_validations
                        if v.verdict in ("confirmed", "confirmed_with_caveats") and not v.is_pipeline_issue)
                    / max(1, corpus_validated), 3),
                "validation_rate_total": round((confirmed + confirmed_with_caveats) / max(1, total_validated), 3),
                "observations_per_node": round(total_obs / max(1, total_nodes), 2),
                "observations_per_dollar": round(total_obs / max(0.01, total_cost), 1),
                "zero_obs_nodes": zero_obs_nodes,
                "zero_obs_pct": round(zero_obs_nodes / max(1, total_nodes), 3),
                "self_eval_gaps": self_eval_gaps,
                "self_eval_gap_pct": round(self_eval_gaps / max(1, total_nodes), 3),
            },
            "efficiency": {
                "wall_clock_seconds": round(elapsed),
                "nodes_spawned": total_nodes,
                "nodes_resolved": self.stats.nodes_resolved,
                "nodes_decomposed": self.stats.nodes_spawned - self.stats.nodes_resolved,
                "max_depth": self.stats.max_depth_reached,
                "avg_branching_factor": round(self.stats.avg_branching_factor, 2),
                "depth_cap_rejections": depth_cap_rejections,
                "envelope_floor_rejections": envelope_floor_rejections,
                "deep_dives": self.stats.deep_dives_executed,
            },
            "tokens": {
                "input_tokens": total_tokens["input_tokens"],
                "output_tokens": total_tokens["output_tokens"],
                "total_tokens": total_tokens["input_tokens"] + total_tokens["output_tokens"],
                "avg_tokens_per_node": round(
                    (total_tokens["input_tokens"] + total_tokens["output_tokens"]) / max(1, total_nodes)),
            },
            "data_coverage": {
                "source": source_name,
                "records_enriched": enriched_count,
                "records_analyzed_by_survey": enriched_count,
            },
            "planner_decisions": getattr(self, '_planner_envelope', {}),
            "briefing": {
                "cost": self._briefing.cost if self._briefing else 0,
                "length": len(self._briefing.common_knowledge) if self._briefing else 0,
                "signal_strength_counts": signal_counts,
                "commonly_known_findings": commonly_known_count,
            },
            "synthesis_quality": {
                "total_findings": total_synthesis_findings,
                "findings_with_specifics": synthesis_with_specifics,
                "total_data_points_cited": total_data_points,
                "avg_data_points_per_finding": round(total_data_points / max(1, total_synthesis_findings), 1),
            },
        }

        # Write metrics.json
        metrics_path = self.run_dir / "metrics.json"
        with open(metrics_path, "w") as f:
            json.dump(metrics, f, indent=2, default=str)

        # Append to run history
        from pathlib import Path
        history_path = Path("catalog/run_history.jsonl")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_line = {
            "run_id": self.run_id,
            "timestamp": metrics["timestamp"],
            "source": source_name,
            "budget": self.budget.total,
            "cost": round(total_cost, 2),
            "nodes": total_nodes,
            "observations": total_obs,
            "confirmed_findings": confirmed,
            "cost_per_valid_finding": metrics["cost"]["per_validated_finding"],
            "zero_obs_pct": metrics["quality"]["zero_obs_pct"],
            "wall_clock_seconds": round(elapsed),
        }
        with open(history_path, "a") as f:
            f.write(json.dumps(history_line, default=str) + "\n")

        # Print summary
        valid_cost = f"${metrics['cost']['per_validated_finding']:.2f}" if confirmed else "N/A"
        print(f"\n  RUN METRICS: ${total_cost:.2f}/{self.budget.total:.2f} | "
              f"{total_obs} obs, {confirmed} confirmed findings | "
              f"{valid_cost}/valid finding | "
              f"{zero_obs_nodes}/{total_nodes} zero-obs nodes | "
              f"{int(elapsed//60)}m{int(elapsed%60)}s | "
              f"{enriched_count} records")


# --- Serialization ---

def _result_to_dict(r: NodeResult) -> dict:
    return {
        "node_id": r.node_id, "parent_id": r.parent_id,
        "scope_description": r.scope_description, "survey": r.survey,
        "observations": [
            {"raw_evidence": o.raw_evidence,
             "statistical_grounding": o.statistical_grounding,
             "local_hypothesis": o.local_hypothesis,
             "source": {"doc_id": o.source.doc_id, "title": o.source.title,
                        "agency": o.source.agency, "date": o.source.date, "url": o.source.url},
             "observation_type": o.observation_type,
             "confidence": o.confidence,
             "surprising_because": o.surprising_because,
             "escalated_adjacency": getattr(o, 'escalated_adjacency', False),
             "unaddressed_adjacency": getattr(o, 'unaddressed_adjacency', False)}
            for o in r.observations
        ],
        "child_directives_count": len(r.child_directives),
        "unresolved": r.unresolved,
        "raw_reasoning": r.raw_reasoning,
        "thinking": r.thinking,
        "token_usage": r.token_usage, "cost": r.cost,
    }

def _synthesis_to_dict(s: SynthesisResult) -> dict:
    return {
        "node_id": s.node_id, "reinforced": s.reinforced,
        "contradictions": s.contradictions, "cross_cutting": s.cross_cutting,
        "discovered_questions": s.discovered_questions,
        "unresolved_threads": s.unresolved_threads,
        "raw_reasoning": s.raw_reasoning, "token_usage": s.token_usage, "cost": s.cost,
    }

def _validation_to_dict(v: ValidationResult) -> dict:
    d = {
        "finding_id": v.finding_id, "original_finding": v.original_finding,
        "verdict": v.verdict, "reasoning": v.reasoning,
        "factual_assessment": v.factual_assessment,
        "interpretive_assessment": v.interpretive_assessment,
        "is_pipeline_issue": v.is_pipeline_issue,
        "pipeline_issue_reasoning": v.pipeline_issue_reasoning,
        "adjusted_confidence": v.adjusted_confidence, "adjusted_tier": v.adjusted_tier,
        "verification_action": v.verification_action, "revised_finding": v.revised_finding,
        "reader_test_gate": getattr(v, 'reader_test_gate', ''),
        "reader_test_score": getattr(v, 'reader_test_score', ''),
        "reader_test_reasoning": getattr(v, 'reader_test_reasoning', ''),
        "charter_shape_check": getattr(v, 'charter_shape_check', {}),
        "cost": v.cost,
    }
    return d

def _impact_to_dict(im: ImpactResult) -> dict:
    return {
        "finding_id": im.finding_id, "affected_parties": im.affected_parties,
        "estimated_scale": im.estimated_scale, "financial_exposure": im.financial_exposure,
        "risk_scenario": im.risk_scenario, "who_needs_to_know": im.who_needs_to_know,
        "urgency": im.urgency, "actionability": im.actionability,
        "reasoning": im.reasoning, "cost": im.cost,
    }

def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        s = text.find("```json") + 7
        e = text.find("```", s)
        if e > s:
            return json.loads(text[s:e].strip())
    s = text.find("{")
    e = text.rfind("}") + 1
    if s >= 0 and e > s:
        return json.loads(text[s:e])
    raise ValueError("Could not extract JSON")


    # _filter_anomalies DELETED — replaced by Orchestrator._route_anomalies_to_segment()
    # which uses LLM reasoning instead of keyword matching.
