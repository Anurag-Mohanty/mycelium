"""npm Registry API connector.

The npm registry is public, no auth required, 2.5M+ packages.
Rich relational data: dependencies, maintainers, licenses, publish history.

APIs:
  Registry:   https://registry.npmjs.org/{package}
  Search:     https://registry.npmjs.org/-/v1/search?text={query}&size={n}
  Downloads:  https://api.npmjs.org/downloads/point/last-month/{package}

Rate limit: generous, but we add 500ms between calls to be polite.
"""

import asyncio
import httpx
from .base import DataSource

REGISTRY_URL = "https://registry.npmjs.org"
DOWNLOADS_URL = "https://api.npmjs.org/downloads"


class NpmRegistrySource(DataSource):
    """Connector for the npm registry."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self._last_call = 0.0
        self._enriched_index = None  # lazy-loaded from catalog/npm_enriched.jsonl

    async def _rate_limit(self):
        """500ms between API calls."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_call
        if elapsed < 0.5:
            await asyncio.sleep(0.5 - elapsed)
        self._last_call = asyncio.get_event_loop().time()

    async def _get(self, url: str, params: dict = None) -> dict | None:
        """Make a rate-limited GET request. Returns None on error."""
        await self._rate_limit()
        try:
            resp = await self.client.get(url, params=params or {})
            if resp.status_code == 200:
                return resp.json()
            return None
        except (httpx.HTTPError, Exception):
            return None

    async def survey(self, filters: dict) -> dict:
        """Get ecosystem shape: top packages, download distribution, maintainer patterns.

        For npm, a "survey" means understanding the shape of an ecosystem segment.
        The filters dict can contain:
          - keyword: search term (e.g. "react", "http client", "testing")
          - packages: specific package names to examine
          - scope: e.g. "@babel" for scoped packages
        """
        survey_data = {
            "source": "npm_registry",
            "total_packages": "2,500,000+",
            "scope": filters.get("keyword", "top packages"),
            "packages": [],
            "ecosystem_shape": {},
        }

        # Search for packages matching the scope
        keyword = filters.get("keyword", "")
        search_url = f"{REGISTRY_URL}/-/v1/search"

        # For broad surveys, sample across ecosystem segments with multiple queries
        # to get a representative view of the ecosystem (200+ packages)
        search_terms = [keyword] if keyword else [
            "react", "express", "typescript", "lodash", "webpack",
            "vue", "next", "testing", "database", "cli",
            "angular", "axios", "babel", "eslint", "jest",
            "graphql", "redis", "aws", "firebase", "auth",
        ]
        target_packages = 20 if keyword else 200

        seen = set()
        for term in search_terms:
            if len(survey_data["packages"]) >= target_packages:
                break
            per_query = min(25, target_packages - len(survey_data["packages"]))
            result = await self._get(search_url, {
                "text": term, "size": per_query,
                "popularity": 0.7, "quality": 0.3,
            })
            if result and "objects" in result:
                if not survey_data.get("total_in_scope"):
                    survey_data["total_in_scope"] = result.get("total", 0)
                for obj in result["objects"]:
                    pkg = obj.get("package", {})
                    name = pkg.get("name", "")
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    survey_data["packages"].append({
                        "name": name,
                        "version": pkg.get("version", ""),
                        "description": (pkg.get("description", "") or "")[:200],
                        "keywords": pkg.get("keywords", [])[:5],
                        "date": pkg.get("date", ""),
                        "publisher": _extract_publisher(pkg),
                        "maintainers_count": len(pkg.get("maintainers", [])),
                        "license": _extract_license(pkg.get("links", {}), pkg),
                        "score": {
                            "quality": obj.get("score", {}).get("detail", {}).get("quality", 0),
                            "popularity": obj.get("score", {}).get("detail", {}).get("popularity", 0),
                            "maintenance": obj.get("score", {}).get("detail", {}).get("maintenance", 0),
                        },
                    })

        # Get download counts for sampled packages (batch in chunks)
        pkg_names = [p["name"] for p in survey_data["packages"]]
        if pkg_names:
            all_downloads = {}
            # npm bulk API handles comma-separated names, up to ~50 at a time
            for chunk_start in range(0, len(pkg_names), 50):
                chunk = pkg_names[chunk_start:chunk_start + 50]
                downloads = await self._get_bulk_downloads(chunk)
                all_downloads.update(downloads)
            for pkg in survey_data["packages"]:
                pkg["monthly_downloads"] = all_downloads.get(pkg["name"], 0)

        # Compute ecosystem shape metrics
        if survey_data["packages"]:
            pkgs = survey_data["packages"]
            maintainer_counts = [p.get("maintainers_count", 0) for p in pkgs]
            survey_data["ecosystem_shape"] = {
                "packages_sampled": len(pkgs),
                "total_in_scope": survey_data.get("total_in_scope", 0),
                "download_range": f"{min(p.get('monthly_downloads', 0) for p in pkgs):,} - {max(p.get('monthly_downloads', 0) for p in pkgs):,}",
                "unique_publishers": len(set(p.get("publisher", "") for p in pkgs if p.get("publisher"))),
                "license_distribution": _count_field(pkgs, "license"),
                "maintainer_distribution": {
                    "1_maintainer": sum(1 for m in maintainer_counts if m <= 1),
                    "2_maintainers": sum(1 for m in maintainer_counts if m == 2),
                    "3+_maintainers": sum(1 for m in maintainer_counts if m >= 3),
                },
                "single_maintainer_packages": sum(1 for m in maintainer_counts if m <= 1),
            }

        return survey_data

    async def fetch(self, filters: dict, max_results: int = 50) -> list[dict]:
        """Fetch package data matching filters.

        Returns package records with metadata, dependencies, maintainers.
        Two modes controlled by filters:
          - Search mode (keyword): search API + enrich with metadata
          - Direct mode (packages): fetch specific package metadata
        """
        packages = []

        # If specific packages are requested, fetch them directly
        if "packages" in filters and filters["packages"]:
            for pkg_name in filters["packages"][:max_results]:
                pkg_data = await self._get_package_metadata(pkg_name)
                if pkg_data:
                    packages.append(pkg_data)
            return packages

        # Search by keyword, or do broad ecosystem scan if no keyword
        keyword = filters.get("keyword", "")
        search_url = f"{REGISTRY_URL}/-/v1/search"

        if keyword:
            # Targeted search
            params = {
                "text": keyword,
                "size": min(max_results, 250),
                "quality": 0.3,
                "popularity": 0.5,
                "maintenance": 0.2,
            }
            result = await self._get(search_url, params)
            if result and "objects" in result:
                for obj in result["objects"][:max_results]:
                    pkg_info = obj.get("package", {})
                    name = pkg_info.get("name", "")
                    if not name:
                        continue
                    full_meta = await self._get_package_metadata(name)
                    if full_meta:
                        full_meta["search_score"] = obj.get("score", {})
                        packages.append(full_meta)
                    else:
                        packages.append(_search_result_to_record(obj))
        else:
            # Broad ecosystem scan — search across major ecosystem segments
            # to build a diverse picture of the npm landscape
            segments = [
                "react", "express", "typescript", "webpack", "lodash",
                "axios", "next", "vue", "testing", "cli",
                "database", "authentication", "validation", "logging",
                "graphql", "mongodb", "redis", "aws", "utility",
                "parser", "crypto", "http", "stream", "build tool",
            ]
            seen = set()
            for segment in segments:
                if len(packages) >= max_results:
                    break
                result = await self._get(search_url, {
                    "text": segment, "size": 8, "popularity": 0.8,
                })
                if result and "objects" in result:
                    for obj in result["objects"]:
                        if len(packages) >= max_results:
                            break
                        name = obj.get("package", {}).get("name", "")
                        if not name or name in seen:
                            continue
                        seen.add(name)
                        full_meta = await self._get_package_metadata(name)
                        if full_meta:
                            full_meta["search_score"] = obj.get("score", {})
                            packages.append(full_meta)

        return packages

    async def fetch_document(self, doc_id: str) -> dict:
        """Fetch a single package's full details by name."""
        return await self._get_package_metadata(doc_id) or {"id": doc_id, "error": "not found"}

    def _load_enriched_index(self):
        """Lazy-load the enriched cache into a name→record dict."""
        if self._enriched_index is not None:
            return
        import json as _json
        from pathlib import Path as _Path
        cache_path = _Path("catalog/npm_enriched.jsonl")
        if cache_path.exists() and cache_path.stat().st_size > 1000:
            self._enriched_index = {}
            with open(cache_path) as f:
                for line in f:
                    if line.strip():
                        r = _json.loads(line)
                        self._enriched_index[r.get("name", r.get("id", ""))] = r
        else:
            self._enriched_index = {}

    async def _get_package_metadata(self, name: str) -> dict | None:
        """Fetch package metadata — from enriched cache if available, else live."""
        self._load_enriched_index()
        if name in self._enriched_index:
            return self._enriched_index[name]

        # Live fallback: use abbreviated metadata for speed (only latest version)
        data = await self._get(f"{REGISTRY_URL}/{name}/latest")
        if not data:
            return None

        # Also get the full package doc for time/maintainer info
        full_doc = await self._get(f"{REGISTRY_URL}/{name}")

        # Extract dependency info
        deps = data.get("dependencies", {})
        dev_deps = data.get("devDependencies", {})

        # Extract maintainer info from full doc
        maintainers = []
        if full_doc:
            maintainers = full_doc.get("maintainers", [])

        # Get version count and publish history from full doc
        versions_list = []
        time_data = {}
        if full_doc:
            time_data = full_doc.get("time", {})
            versions_list = list(full_doc.get("versions", {}).keys())

        # Get downloads
        dl_data = await self._get(
            f"{DOWNLOADS_URL}/point/last-month/{name}"
        )
        monthly_downloads = dl_data.get("downloads", 0) if dl_data else 0

        # Build record
        latest_version = data.get("version", "")
        return {
            "id": name,
            "title": name,  # for compatibility with generic formatting
            "type": "npm_package",
            "description": (data.get("description", "") or "")[:500],
            "agency": _extract_author(data),  # "agency" = author/publisher for generic compat
            "date": time_data.get(latest_version, time_data.get("modified", "")),
            "url": f"https://www.npmjs.com/package/{name}",

            # npm-specific fields
            "version": latest_version,
            "license": data.get("license", "UNKNOWN"),
            "dependencies": deps,
            "dependency_count": len(deps),
            "dev_dependencies": dev_deps,
            "dev_dependency_count": len(dev_deps),
            "maintainers": [m.get("name", m.get("email", "?")) for m in maintainers],
            "maintainer_count": len(maintainers),
            "version_count": len(versions_list),
            "latest_versions": versions_list[-5:] if versions_list else [],
            "created": time_data.get("created", ""),
            "last_modified": time_data.get("modified", ""),
            "monthly_downloads": monthly_downloads,
            "repository": _extract_repo(data),
            "keywords": data.get("keywords", []),
            "homepage": data.get("homepage", ""),

            # Computed risk signals
            "abstract": _build_abstract(name, data, deps, maintainers,
                                         monthly_downloads, versions_list, time_data),
        }

    async def fetch_bulk_metadata(self, max_records: int = 2000,
                                   progress_callback=None) -> list[dict]:
        """Fetch metadata for many packages — used by ProgrammaticSurvey.

        Loads from catalog/npm_enriched.jsonl if available (100K+ packages).
        Otherwise falls back to search API sampling.
        """
        import json as _json
        from pathlib import Path as _Path

        cache_path = _Path("catalog/npm_enriched.jsonl")
        if cache_path.exists() and cache_path.stat().st_size > 1000:
            print(f"  [CATALOG] Loading cached enrichment from {cache_path}...")
            records = []
            with open(cache_path) as f:
                for line in f:
                    if line.strip():
                        records.append(_json.loads(line))
            # Use the full catalog — no cap when loading from cache
            if records:
                print(f"  [CATALOG] Loaded {len(records)} packages (cached)")
                return records

        search_url = f"{REGISTRY_URL}/-/v1/search"
        segments = [
            "react", "express", "typescript", "lodash", "webpack",
            "vue", "next", "testing", "jest", "database",
            "cli", "angular", "axios", "babel", "eslint",
            "graphql", "redis", "aws", "firebase", "auth",
            "http", "server", "utility", "crypto", "stream",
            "parser", "compiler", "bundler", "linter", "formatter",
        ]

        seen = set()
        records = []
        total_estimated = max_records

        for term in segments:
            if len(records) >= max_records:
                break

            remaining = max_records - len(records)
            batch_size = min(250, remaining)

            result = await self._get(search_url, {
                "text": term, "size": batch_size,
                "popularity": 0.5, "quality": 0.3, "maintenance": 0.2,
            })

            if not result or "objects" not in result:
                continue

            if not total_estimated or total_estimated == max_records:
                total_estimated = min(result.get("total", max_records), max_records)

            for obj in result["objects"]:
                pkg = obj.get("package", {})
                name = pkg.get("name", "")
                if not name or name in seen:
                    continue
                seen.add(name)

                score = obj.get("score", {}).get("detail", {})
                records.append({
                    "name": name,
                    "version": pkg.get("version", ""),
                    "description": (pkg.get("description", "") or "")[:200],
                    "publisher": _extract_publisher(pkg),
                    "maintainer_count": len(pkg.get("maintainers", [])),
                    "maintainers": ", ".join(m.get("username", m.get("name", "?"))
                                             for m in pkg.get("maintainers", [])[:5]),
                    "license": _extract_license(pkg.get("links", {}), pkg),
                    "date": pkg.get("date", ""),
                    "keywords": ", ".join(pkg.get("keywords", [])[:5]),
                    "quality_score": round(score.get("quality", 0), 3),
                    "popularity_score": round(score.get("popularity", 0), 3),
                    "maintenance_score": round(score.get("maintenance", 0), 3),
                    "search_term": term,
                })

            if progress_callback:
                progress_callback({
                    "fetched": len(records),
                    "total_estimated": total_estimated,
                })

        # Fetch download counts in bulk
        pkg_names = [r["name"] for r in records]
        all_downloads = {}
        for chunk_start in range(0, len(pkg_names), 50):
            chunk = pkg_names[chunk_start:chunk_start + 50]
            downloads = await self._get_bulk_downloads(chunk)
            all_downloads.update(downloads)

            if progress_callback:
                progress_callback({
                    "fetched": len(records),
                    "total_estimated": total_estimated,
                    "downloads_fetched": chunk_start + len(chunk),
                })

        for r in records:
            r["monthly_downloads"] = all_downloads.get(r["name"], 0)

        return records

    async def _get_bulk_downloads(self, names: list[str]) -> dict[str, int]:
        """Fetch monthly downloads for multiple packages."""
        results = {}
        # npm bulk API supports comma-separated names but has URL length limits
        # Batch in groups of 50
        for i in range(0, len(names), 50):
            batch = names[i:i+50]
            batch_str = ",".join(batch)
            data = await self._get(
                f"{DOWNLOADS_URL}/point/last-month/{batch_str}"
            )
            if data:
                # Bulk endpoint returns {pkg_name: {downloads: N}} or {downloads: N} for single
                if isinstance(data, dict):
                    if "downloads" in data and len(batch) == 1:
                        results[batch[0]] = data.get("downloads", 0)
                    else:
                        for name in batch:
                            pkg_data = data.get(name, {})
                            if isinstance(pkg_data, dict):
                                results[name] = pkg_data.get("downloads", 0)
        return results

    def filter_schema(self) -> dict:
        return {
            "keyword": {
                "type": "string",
                "description": "Search term passed to npm search API. Matches package name, description, and keywords.",
                "example": "react hooks",
                "required": False,
            },
            "packages": {
                "type": "list[string]",
                "description": "Exact package names to fetch directly by registry lookup.",
                "example": ["lodash", "express", "@vue/reactivity"],
                "required": False,
            },
            "scope": {
                "type": "string",
                "description": "npm scope prefix to filter by organization.",
                "example": "@babel",
                "required": False,
            },
        }

    async def close(self):
        await self.client.aclose()


def _build_abstract(name, data, deps, maintainers, downloads, versions, time_data) -> str:
    """Build a rich text abstract summarizing the package for the LLM to read.

    This is the key to data richness — the LLM reads this like a document abstract.
    """
    parts = [f"Package: {name} v{data.get('version', '?')}"]

    desc = data.get("description", "")
    if desc:
        parts.append(f"Description: {desc}")

    parts.append(f"License: {data.get('license', 'UNKNOWN')}")
    parts.append(f"Monthly downloads: {downloads:,}")
    parts.append(f"Maintainers ({len(maintainers)}): {', '.join(m.get('name', '?') for m in maintainers[:5])}")

    if deps:
        dep_list = list(deps.keys())
        parts.append(f"Dependencies ({len(deps)}): {', '.join(dep_list[:10])}"
                     + (f" ... and {len(dep_list)-10} more" if len(dep_list) > 10 else ""))

    if versions:
        parts.append(f"Total versions published: {len(versions)}")
        parts.append(f"Latest versions: {', '.join(versions[-3:])}")

    created = time_data.get("created", "")
    modified = time_data.get("modified", "")
    if created:
        parts.append(f"Created: {created[:10]}")
    if modified:
        parts.append(f"Last modified: {modified[:10]}")
    if created and modified:
        # Age signal
        parts.append(f"Age: created {created[:10]} → last modified {modified[:10]}")

    repo = _extract_repo(data)
    if repo:
        parts.append(f"Repository: {repo}")

    keywords = data.get("keywords", [])
    if keywords:
        parts.append(f"Keywords: {', '.join(keywords[:8])}")

    return "\n".join(parts)


def _extract_publisher(pkg: dict) -> str:
    publisher = pkg.get("publisher", {})
    if isinstance(publisher, dict):
        return publisher.get("username", publisher.get("email", ""))
    return str(publisher) if publisher else ""


def _extract_author(data: dict) -> str:
    author = data.get("author", {})
    if isinstance(author, dict):
        return author.get("name", author.get("email", "unknown"))
    if isinstance(author, str):
        return author
    # Try maintainers
    maintainers = data.get("maintainers", [])
    if maintainers:
        first = maintainers[0]
        if isinstance(first, dict):
            return first.get("name", first.get("email", "unknown"))
    return "unknown"


def _extract_license(links: dict, pkg: dict) -> str:
    # Try the package-level license field
    lic = pkg.get("license", "")
    if isinstance(lic, dict):
        return lic.get("type", str(lic))
    return str(lic) if lic else "UNKNOWN"


def _extract_repo(data: dict) -> str:
    repo = data.get("repository", {})
    if isinstance(repo, dict):
        url = repo.get("url", "")
        # Clean up git+https:// prefixes
        url = url.replace("git+", "").replace("git://", "https://")
        return url
    if isinstance(repo, str):
        return repo
    return ""


def _count_field(packages: list[dict], field: str) -> dict:
    counts = {}
    for p in packages:
        val = p.get(field, "UNKNOWN")
        counts[val] = counts.get(val, 0) + 1
    return counts


def _search_result_to_record(obj: dict) -> dict:
    """Convert a search result to a package record when full fetch fails."""
    pkg = obj.get("package", {})
    return {
        "id": pkg.get("name", ""),
        "title": pkg.get("name", ""),
        "type": "npm_package",
        "description": (pkg.get("description", "") or "")[:500],
        "agency": _extract_publisher(pkg),
        "date": pkg.get("date", ""),
        "url": f"https://www.npmjs.com/package/{pkg.get('name', '')}",
        "version": pkg.get("version", ""),
        "license": _extract_license({}, pkg),
        "abstract": f"Package: {pkg.get('name', '')} - {pkg.get('description', '')}",
        "monthly_downloads": 0,
        "maintainer_count": len(pkg.get("maintainers", [])),
        "dependency_count": 0,
    }
