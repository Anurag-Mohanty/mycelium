"""Organizational Behavior — nodes as knowledge workers with full agency.

STATUS: v3 DESIGN — skeleton only, not yet wired into orchestrator.

Each node behaves as a full knowledge worker with:
- Job (explore this scope)
- Tools (self-equipped via MCP)
- Judgment (resolve vs decompose vs both)
- Budget authority (manage allocation, request more)
- Communication (report up, coordinate laterally, escalate)
- Self-awareness (knows when it's overwhelmed, found something big, or done)

This builds on:
- v1: Knowledge graph (persistent understanding)
- v2: Lateral communication (budget routing toward signal)
- v3: Full organizational model (this module)
"""


class WorkerNode:
    """A node with full organizational awareness.

    Unlike the current node.py which is a single LLM call,
    a WorkerNode maintains state across its lifecycle and
    can communicate with other workers.
    """

    def __init__(self, directive, budget_authority: float):
        self.directive = directive
        self.budget = budget_authority
        self.tools = []  # MCP connections from EQUIP
        self.observations = []
        self.children = []
        self.status = "idle"  # idle → equipping → surveying → resolving → done

        # Awareness
        self.overwhelm_threshold = None  # set during assessment
        self.signal_strength = 0.0  # how interesting is what I'm finding
        self.related_siblings = []  # nodes working on related scopes

    def assess_self(self) -> str:
        """Self-evaluation of current state."""
        if len(self.observations) == 0 and self.budget < 0.05:
            return "starving"  # no findings, no budget
        if self.signal_strength > 0.8 and self.budget < 0.10:
            return "underfunded"  # found something big, can't pursue it
        if self.signal_strength < 0.2 and self.budget > 0.20:
            return "overfunded"  # nothing interesting, too much budget
        return "healthy"


class JointInvestigation:
    """Triggered when two nodes in different branches discover related findings.

    Example:
        Branch A finds: "Package X has a license conflict"
        Branch B finds: "Package Y depends on Package X"
        Common parent spawns joint investigation:
        "Trace the license conflict through the dependency chain"
    """

    def __init__(self, finding_a: dict, finding_b: dict, common_ancestor_id: str):
        self.finding_a = finding_a
        self.finding_b = finding_b
        self.ancestor = common_ancestor_id
        self.directive = self._create_joint_directive()

    def _create_joint_directive(self) -> dict:
        return {
            "scope_description": (
                f"Joint investigation: Connect '{self.finding_a.get('summary', '')}' "
                f"with '{self.finding_b.get('summary', '')}' — trace the relationship "
                f"between these findings from different branches."
            ),
            "parent_context": (
                f"Two independent investigators found potentially connected findings. "
                f"Your job is to verify the connection and trace its full implications."
            ),
        }


class PersistentOrganization:
    """The org doesn't dissolve after one run.

    New explorations build on prior understanding via the knowledge graph.
    Workers can query what previous runs discovered before deciding
    where to focus their effort.
    """

    def __init__(self, knowledge_graph):
        self.kg = knowledge_graph

    def get_prior_knowledge(self, scope: str) -> dict:
        """What do we already know about this area from previous runs?"""
        entities = self.kg.find_entities(scope)
        if not entities:
            return {"known": False, "prior_observations": 0}

        total_obs = 0
        for e in entities[:5]:
            ctx = self.kg.get_entity_context(e["name"], depth=1)
            total_obs += len(ctx.get("observations", []))

        return {
            "known": True,
            "entities_found": len(entities),
            "prior_observations": total_obs,
            "suggestion": "Focus on updating stale observations and finding new connections"
                          if total_obs > 10 else "Limited prior knowledge — explore broadly",
        }
