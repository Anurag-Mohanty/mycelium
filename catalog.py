"""Catalog builder — one-time full registry index.

Builds a complete catalog of the npm registry:
  Step 1: Fetch all ~4M package names (5 min)
  Step 2: Fetch download counts for all packages (3-4 hours)
  Step 3: Filter to active packages (>1000 downloads)
  Step 4: Enrich active packages with full metadata (2-3 hours)
  Step 5: Run analytical survey on enriched data

Usage:
    python3 catalog.py --source npm --full
    python3 catalog.py --source npm --downloads-only
    python3 catalog.py --source npm --resume
    python3 catalog.py --source npm --enrich
    python3 catalog.py --source npm --survey
"""

import argparse
import json
import sys
import time
import httpx
from pathlib import Path


CATALOG_DIR = Path("catalog")
NPM_NAMES_FILE = CATALOG_DIR / "npm_names.jsonl"
NPM_DOWNLOADS_FILE = CATALOG_DIR / "npm_downloads.jsonl"
NPM_ACTIVE_FILE = CATALOG_DIR / "npm_active.jsonl"
NPM_ENRICHED_FILE = CATALOG_DIR / "npm_enriched.jsonl"
NPM_SURVEY_FILE = CATALOG_DIR / "npm_survey.json"

REPLICATE_URL = "https://replicate.npmjs.com/_all_docs"
DOWNLOADS_URL = "https://api.npmjs.org/downloads/point/last-month"
REGISTRY_URL = "https://registry.npmjs.org"

DOWNLOAD_BATCH_SIZE = 128
DOWNLOAD_DELAY = 0.1  # 100ms between requests
ENRICH_DELAY = 0.1


def _progress(msg: str):
    """Print progress with timestamp."""
    print(f"\r  {msg}", end="", flush=True)


def _eta(elapsed: float, done: int, total: int) -> str:
    """Compute ETA string."""
    if done == 0:
        return "calculating..."
    rate = done / elapsed
    remaining = (total - done) / rate
    if remaining < 60:
        return f"{remaining:.0f}s"
    elif remaining < 3600:
        return f"{remaining / 60:.0f}min"
    else:
        h = int(remaining // 3600)
        m = int((remaining % 3600) // 60)
        return f"{h}h {m}m"


# =================================================================
# STEP 1: Full name index
# =================================================================

def fetch_names():
    """Fetch all package names from the npm registry."""
    print("\n  STEP 1: Fetching all package names...")

    if NPM_NAMES_FILE.exists():
        existing = sum(1 for _ in open(NPM_NAMES_FILE))
        print(f"  Found existing {NPM_NAMES_FILE} with {existing:,} names. Overwriting.")

    client = httpx.Client(timeout=30)
    start_key = None
    total_fetched = 0
    batch_size = 10000

    # Get total count
    r = client.get(f"{REPLICATE_URL}?limit=1")
    data = r.json()
    total_packages = data.get("total_rows", 4000000)
    print(f"  Registry has {total_packages:,} packages")

    start = time.time()

    with open(NPM_NAMES_FILE, "w") as f:
        while True:
            url = f"{REPLICATE_URL}?limit={batch_size}"
            if start_key:
                url += f"&startkey=%22{start_key}%22"

            r = client.get(url)
            if r.status_code != 200:
                print(f"\n  HTTP {r.status_code} — retrying in 2s...")
                time.sleep(2)
                continue

            data = r.json()
            rows = data.get("rows", [])

            if not rows:
                break

            # Skip the first row if paginating (it's the startkey itself)
            skip_first = 1 if start_key else 0

            for row in rows[skip_first:]:
                name = row["id"]
                if not name.startswith("_"):
                    f.write(name + "\n")
                    total_fetched += 1

            start_key = rows[-1]["id"]

            elapsed = time.time() - start
            _progress(
                f"Names: {total_fetched:,} / {total_packages:,} "
                f"({total_fetched / total_packages * 100:.1f}%) — "
                f"{elapsed:.0f}s elapsed, ETA {_eta(elapsed, total_fetched, total_packages)}"
            )

            if len(rows) < batch_size:
                break

    client.close()
    elapsed = time.time() - start
    print(f"\n  Done: {total_fetched:,} names in {elapsed:.0f}s → {NPM_NAMES_FILE}")


# =================================================================
# STEP 2: Download counts
# =================================================================

def fetch_downloads(resume: bool = False):
    """Fetch download counts for all packages."""
    print("\n  STEP 2: Fetching download counts...")

    if not NPM_NAMES_FILE.exists():
        print("  ERROR: No names file. Run --full or Step 1 first.")
        return

    # Load all names
    all_names = [line.strip() for line in open(NPM_NAMES_FILE) if line.strip()]
    total = len(all_names)
    print(f"  {total:,} packages to fetch")

    # Resume: load already-fetched names
    already_fetched = set()
    if resume and NPM_DOWNLOADS_FILE.exists():
        for line in open(NPM_DOWNLOADS_FILE):
            try:
                rec = json.loads(line)
                already_fetched.add(rec["name"])
            except (json.JSONDecodeError, KeyError):
                pass
        print(f"  Resuming: {len(already_fetched):,} already fetched")

    # Filter to unfetched
    to_fetch = [n for n in all_names if n not in already_fetched]
    print(f"  Remaining: {len(to_fetch):,}")

    if not to_fetch:
        print("  All packages already fetched.")
        return

    client = httpx.Client(timeout=15)
    mode = "a" if resume and NPM_DOWNLOADS_FILE.exists() else "w"
    start = time.time()
    fetched = 0
    errors = 0
    batch_count = 0

    with open(NPM_DOWNLOADS_FILE, mode) as f:
        for i in range(0, len(to_fetch), DOWNLOAD_BATCH_SIZE):
            batch = to_fetch[i:i + DOWNLOAD_BATCH_SIZE]
            names_str = ",".join(batch)

            try:
                r = client.get(f"{DOWNLOADS_URL}/{names_str}")
                if r.status_code == 200:
                    data = r.json()
                    for name in batch:
                        pkg_data = data.get(name)
                        if isinstance(pkg_data, dict):
                            downloads = pkg_data.get("downloads", 0)
                        else:
                            downloads = 0
                        f.write(json.dumps({"name": name, "downloads": downloads}) + "\n")
                        fetched += 1
                elif r.status_code == 429:
                    # Rate limited — back off and retry
                    errors += 1
                    time.sleep(2)
                    continue
                else:
                    errors += 1
                    # Write zeros for failed batch
                    for name in batch:
                        f.write(json.dumps({"name": name, "downloads": 0}) + "\n")
                        fetched += 1
            except Exception as e:
                errors += 1
                for name in batch:
                    f.write(json.dumps({"name": name, "downloads": 0}) + "\n")
                    fetched += 1

            batch_count += 1

            # Flush every 100 batches
            if batch_count % 100 == 0:
                f.flush()

            elapsed = time.time() - start
            done = len(already_fetched) + fetched
            _progress(
                f"Downloads: {done:,} / {total:,} "
                f"({done / total * 100:.1f}%) — "
                f"{elapsed / 60:.0f}min elapsed, "
                f"ETA {_eta(elapsed, fetched, len(to_fetch))} — "
                f"{errors} errors"
            )

            time.sleep(DOWNLOAD_DELAY)

    client.close()
    elapsed = time.time() - start
    print(f"\n  Done: {fetched:,} downloads in {elapsed / 60:.1f}min → {NPM_DOWNLOADS_FILE}")


# =================================================================
# STEP 3: Filter active packages
# =================================================================

def filter_active(threshold: int = 1000):
    """Filter to packages with >threshold monthly downloads."""
    print(f"\n  STEP 3: Filtering to packages with >{threshold:,} downloads...")

    if not NPM_DOWNLOADS_FILE.exists():
        print("  ERROR: No downloads file. Run Step 2 first.")
        return

    total = 0
    active = 0
    total_downloads = 0
    active_downloads = 0

    with open(NPM_ACTIVE_FILE, "w") as out:
        for line in open(NPM_DOWNLOADS_FILE):
            try:
                rec = json.loads(line)
                downloads = rec.get("downloads", 0)
                total += 1
                total_downloads += downloads

                if downloads >= threshold:
                    out.write(line)
                    active += 1
                    active_downloads += downloads
            except json.JSONDecodeError:
                pass

    pct_packages = active / total * 100 if total > 0 else 0
    pct_downloads = active_downloads / total_downloads * 100 if total_downloads > 0 else 0

    print(f"  {active:,} packages have >{threshold:,} downloads "
          f"out of {total:,} total ({pct_packages:.1f}%)")
    print(f"  These represent {pct_downloads:.1f}% of total ecosystem download volume")
    print(f"  → {NPM_ACTIVE_FILE}")


# =================================================================
# STEP 4: Enrich active packages
# =================================================================

def enrich_active():
    """Fetch full metadata for active packages."""
    print("\n  STEP 4: Enriching active packages with full metadata...")

    if not NPM_ACTIVE_FILE.exists():
        print("  ERROR: No active file. Run Step 3 first.")
        return

    # Load active package names
    active = []
    for line in open(NPM_ACTIVE_FILE):
        try:
            rec = json.loads(line)
            active.append(rec)
        except json.JSONDecodeError:
            pass

    total = len(active)
    print(f"  {total:,} packages to enrich")

    # Resume: load already-enriched
    already_enriched = set()
    if NPM_ENRICHED_FILE.exists():
        for line in open(NPM_ENRICHED_FILE):
            try:
                rec = json.loads(line)
                already_enriched.add(rec.get("name", ""))
            except json.JSONDecodeError:
                pass
        print(f"  Already enriched: {len(already_enriched):,}")

    to_enrich = [a for a in active if a["name"] not in already_enriched]
    print(f"  Remaining: {len(to_enrich):,}")

    if not to_enrich:
        print("  All packages already enriched.")
        return

    client = httpx.Client(timeout=15)
    mode = "a" if already_enriched else "w"
    start = time.time()
    enriched = 0
    errors = 0

    with open(NPM_ENRICHED_FILE, mode) as f:
        for i, pkg in enumerate(to_enrich):
            name = pkg["name"]
            downloads = pkg.get("downloads", 0)

            try:
                # Fetch abbreviated metadata (latest version only)
                r = client.get(f"{REGISTRY_URL}/{name}/latest",
                               headers={"Accept": "application/json"})

                if r.status_code == 200:
                    data = r.json()

                    record = {
                        "name": name,
                        "monthly_downloads": downloads,
                        "description": (data.get("description", "") or "")[:300],
                        "version": data.get("version", ""),
                        "license": _extract_license(data),
                        "keywords": data.get("keywords", [])[:10],
                        "dependencies": list(data.get("dependencies", {}).keys()),
                        "dependency_count": len(data.get("dependencies", {})),
                        "dev_dependency_count": len(data.get("devDependencies", {})),
                        "repository": _extract_repo(data),
                    }

                    # Fetch full doc for maintainers + version count
                    r2 = client.get(f"{REGISTRY_URL}/{name}",
                                    headers={"Accept": "application/json"})
                    if r2.status_code == 200:
                        full = r2.json()
                        maintainers = full.get("maintainers", [])
                        record["maintainers"] = [
                            m.get("name", m.get("email", "?"))
                            for m in maintainers[:10]
                        ]
                        record["maintainer_count"] = len(maintainers)
                        record["version_count"] = len(full.get("versions", {}))

                        time_data = full.get("time", {})
                        record["created"] = time_data.get("created", "")[:10]
                        record["last_modified"] = time_data.get("modified", "")[:10]
                    else:
                        record["maintainers"] = []
                        record["maintainer_count"] = 0
                        record["version_count"] = 0

                    f.write(json.dumps(record) + "\n")
                    enriched += 1
                else:
                    errors += 1
            except Exception:
                errors += 1

            # Flush every 500
            if enriched % 500 == 0 and enriched > 0:
                f.flush()

            elapsed = time.time() - start
            done = len(already_enriched) + enriched
            _progress(
                f"Enrich: {done:,} / {total:,} "
                f"({done / total * 100:.1f}%) — "
                f"{elapsed / 60:.0f}min elapsed, "
                f"ETA {_eta(elapsed, enriched, len(to_enrich))} — "
                f"{errors} errors"
            )

            time.sleep(ENRICH_DELAY)

    client.close()
    elapsed = time.time() - start
    print(f"\n  Done: {enriched:,} enriched in {elapsed / 60:.1f}min → {NPM_ENRICHED_FILE}")


# =================================================================
# STEP 5: Analytical survey
# =================================================================

def run_survey():
    """Run AnalyticalSurvey on enriched data."""
    print("\n  STEP 5: Running analytical survey...")

    if not NPM_ENRICHED_FILE.exists():
        print("  ERROR: No enriched file. Run Step 4 first.")
        return

    records = []
    for line in open(NPM_ENRICHED_FILE):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    print(f"  {len(records):,} records loaded")

    from mycelium.survey import AnalyticalSurvey

    def _survey_progress(p):
        print(f"    [{p.get('phase', '?')}]", flush=True)

    survey = AnalyticalSurvey()
    results = survey.analyze(records, progress_callback=_survey_progress)

    # Save
    with open(NPM_SURVEY_FILE, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n  {results.get('summary', '')}")
    print(f"  → {NPM_SURVEY_FILE}")


# =================================================================
# Helpers
# =================================================================

def _extract_license(data: dict) -> str:
    lic = data.get("license", "")
    if isinstance(lic, dict):
        return lic.get("type", str(lic))
    return str(lic) if lic else "UNKNOWN"


def _extract_repo(data: dict) -> str:
    repo = data.get("repository", {})
    if isinstance(repo, dict):
        url = repo.get("url", "")
        return url.replace("git+", "").replace("git://", "https://")
    if isinstance(repo, str):
        return repo
    return ""


# =================================================================
# CLI
# =================================================================

def main():
    parser = argparse.ArgumentParser(description="Build full registry catalog")
    parser.add_argument("--source", required=True, help="Data source (npm)")
    parser.add_argument("--full", action="store_true", help="Run all 5 steps")
    parser.add_argument("--downloads-only", action="store_true",
                        help="Steps 1-2 only (names + downloads)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume interrupted download/enrich")
    parser.add_argument("--enrich", action="store_true",
                        help="Run enrichment (Step 4) only")
    parser.add_argument("--survey", action="store_true",
                        help="Run survey (Step 5) only")
    parser.add_argument("--threshold", type=int, default=1000,
                        help="Download threshold for active filter (default: 1000)")

    args = parser.parse_args()

    if args.source != "npm":
        print(f"ERROR: Only 'npm' supported for now. Got: {args.source}")
        sys.exit(1)

    CATALOG_DIR.mkdir(exist_ok=True)

    print(f"\n╔{'═' * 50}╗")
    print(f"║  CATALOG BUILDER — {args.source:30s} ║")
    print(f"╚{'═' * 50}╝")

    if args.full:
        fetch_names()
        fetch_downloads(resume=False)
        filter_active(args.threshold)
        enrich_active()
        run_survey()
    elif args.downloads_only:
        fetch_names()
        fetch_downloads(resume=False)
    elif args.resume:
        if not NPM_NAMES_FILE.exists():
            fetch_names()
        fetch_downloads(resume=True)
        filter_active(args.threshold)
        enrich_active()
    elif args.enrich:
        if not NPM_ACTIVE_FILE.exists():
            filter_active(args.threshold)
        enrich_active()
    elif args.survey:
        run_survey()
    else:
        parser.print_help()
        sys.exit(1)

    print("\n  Catalog complete.\n")


if __name__ == "__main__":
    main()
