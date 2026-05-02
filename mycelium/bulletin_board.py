"""Bulletin Board — lateral communication between nodes.

Workers broadcast observations worth sharing. Workers pull at formation
and mid-reassessment. Posts have attribution lineage (references to prior
posts). Pull events track whether pulled content influenced reasoning.

Single workspace for now. Multi-workspace is a later refinement.
"""

import json
import time
import uuid
from pathlib import Path


class BulletinBoard:
    """In-memory bulletin board with JSON persistence."""

    def __init__(self):
        self.posts: list[dict] = []
        self.pulls: list[dict] = []

    def post(self, author_node_id: str, author_role_name: str,
             post_type: str, content: str,
             references: list[str] = None) -> str:
        """Broadcast a post to the board. Returns post_id."""
        post_id = str(uuid.uuid4())[:12]
        entry = {
            "post_id": post_id,
            "author_node_id": author_node_id,
            "author_role_name": author_role_name,
            "post_type": post_type,  # OBSERVATION, HYPOTHESIS, DEAD_END
            "content": content,
            "timestamp": time.time(),
            "references": references or [],
        }
        self.posts.append(entry)
        return post_id

    def pull(self, pulling_node_id: str, post_id: str,
             influence: bool = False) -> dict | None:
        """Record a pull event. Returns the pulled post, or None if not found."""
        post = next((p for p in self.posts if p["post_id"] == post_id), None)
        if post is None:
            return None
        pull_entry = {
            "pull_id": str(uuid.uuid4())[:12],
            "pulling_node_id": pulling_node_id,
            "post_id": post_id,
            "timestamp": time.time(),
            "influence": influence,
        }
        self.pulls.append(pull_entry)
        return post

    def get_posts(self, exclude_author: str = None,
                  post_type: str = None) -> list[dict]:
        """Get all posts, optionally excluding a specific author or filtering by type."""
        results = self.posts
        if exclude_author:
            results = [p for p in results if p["author_node_id"] != exclude_author]
        if post_type:
            results = [p for p in results if p["post_type"] == post_type]
        return results

    def get_posts_since(self, since_timestamp: float,
                        exclude_author: str = None) -> list[dict]:
        """Get posts added after a given timestamp."""
        results = [p for p in self.posts if p["timestamp"] > since_timestamp]
        if exclude_author:
            results = [p for p in results if p["author_node_id"] != exclude_author]
        return results

    def format_for_prompt(self, posts: list[dict]) -> str:
        """Format posts for inclusion in an LLM prompt."""
        if not posts:
            return "(no posts on the board yet)"
        lines = []
        for p in posts:
            refs = f" [refs: {', '.join(p['references'])}]" if p["references"] else ""
            content = p['content'] if p['post_type'] == 'EQUIP_BRIEFING' else p['content'][:2000]
            lines.append(
                f"  [{p['post_type']}] by {p['author_role_name']} "
                f"(post_id={p['post_id']}): {content}{refs}"
            )
        return "\n".join(lines)

    def stats(self) -> dict:
        """Board statistics."""
        type_counts = {}
        for p in self.posts:
            type_counts[p["post_type"]] = type_counts.get(p["post_type"], 0) + 1
        return {
            "total_posts": len(self.posts),
            "total_pulls": len(self.pulls),
            "influenced_pulls": sum(1 for p in self.pulls if p["influence"]),
            "posts_by_type": type_counts,
            "unique_authors": len(set(p["author_node_id"] for p in self.posts)),
        }

    def save(self, path: str | Path):
        """Save board state to JSON."""
        Path(path).write_text(json.dumps({
            "posts": self.posts,
            "pulls": self.pulls,
        }, indent=2, default=str))

    def load(self, path: str | Path):
        """Load board state from JSON."""
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text())
            self.posts = data.get("posts", [])
            self.pulls = data.get("pulls", [])
