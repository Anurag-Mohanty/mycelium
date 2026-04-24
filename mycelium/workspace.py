"""Phase F Workspace — filesystem-based shared context for the organization.

Org-level workspace: charter, rules of engagement, initial scopes.
Department-level workspaces: created when a manager decomposes (Milestone 2).

Workers read from workspaces when making quality judgments. The workspace
is authored once and read many times — no paraphrase drift.
"""

import json
from pathlib import Path


class OrgWorkspace:
    """Org-level workspace containing charter, rules, and scopes.

    Created once at run start. Every worker has read access.
    """

    def __init__(self, workspace_dir: str | Path):
        self.path = Path(workspace_dir)
        self.path.mkdir(parents=True, exist_ok=True)

    def write_charter(self, charter_text: str):
        """Write the Genesis charter to the workspace."""
        (self.path / "charter.md").write_text(charter_text)

    def write_rules(self, rules_text: str):
        """Write the Planner's rules of engagement."""
        (self.path / "rules.md").write_text(rules_text)

    def write_scopes(self, scopes: list[dict], budget_allocation: dict):
        """Write the Planner's initial scopes and budget allocation."""
        (self.path / "scopes.json").write_text(json.dumps({
            "scopes": scopes,
            "budget_allocation": budget_allocation,
        }, indent=2))

    def read_charter(self) -> str:
        """Read the charter. Returns empty string if not yet written."""
        p = self.path / "charter.md"
        return p.read_text() if p.exists() else ""

    def read_rules(self) -> str:
        """Read the rules of engagement. Returns empty string if not yet written."""
        p = self.path / "rules.md"
        return p.read_text() if p.exists() else ""

    def read_scopes(self) -> dict:
        """Read the scopes. Returns empty dict if not yet written."""
        p = self.path / "scopes.json"
        if p.exists():
            return json.loads(p.read_text())
        return {}
