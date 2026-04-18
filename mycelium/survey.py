"""Programmatic Survey — catalog everything before spending on AI.

Pure Python. Zero LLM cost. Scans all accessible records from a data
source and computes statistics, distributions, outliers, concentrations,
and anomaly clusters. The output tells you what's interesting BEFORE
you commit any budget to AI-driven exploration.

20 analysis types across 6 groups:
  A. Distribution (6): numeric, categorical, temporal, outliers, combos, correlations
  B. Record Content (3): keyword extraction, completeness, content size
  C. Entity (3): grouping, temporal change, cross-referencing
  D. Graph (4): construction, centrality, inversions, orphans
  E. Similarity (2): near-duplicates, templates
  F. Velocity (2): rate of change, recency

All analysis is PURE MATH. No LLM calls. No domain knowledge.
Works identically on npm packages, SEC filings, hospital records,
or any other list of dicts.
"""

import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


class ProgrammaticSurvey:
    """Catalogs a dataset with pure statistics. No LLM. No cost."""

    def __init__(self):
        self._records = []
        self._field_types = {}  # field_name -> "numeric" | "categorical" | "temporal" | "list"
        self._numeric_values = defaultdict(list)  # field -> [values]
        self._categorical_values = defaultdict(list)  # field -> [values]
        self._record_count = 0

    def analyze(self, records: list[dict], progress_callback=None) -> dict:
        """Analyze a list of records and return statistical summary.

        Args:
            records: list of dicts, each representing one record
            progress_callback: optional callable(dict) for progress updates

        Returns:
            dict with field_types, distributions, outliers, concentrations,
            correlations, unusual_combinations, anomaly_clusters
        """
        if not records:
            return {"error": "No records provided", "record_count": 0}

        self._records = records
        self._record_count = len(records)

        # Phase 1: Detect field types from first 100 records
        self._detect_field_types(records[:100])

        # Phase 2: Accumulate values with progress reporting
        batch_size = max(1, len(records) // 20)  # ~20 progress updates
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            self._accumulate_batch(batch)

            if progress_callback:
                scanned = min(i + batch_size, len(records))
                progress_callback({
                    "scanned": scanned,
                    "total": len(records),
                    "percent": round(scanned / len(records) * 100),
                    "fields_detected": list(self._field_types.keys()),
                    "running_stats": self._quick_stats(),
                })

        # Phase 3: Group A — Distribution analysis (existing)
        distributions = self._compute_distributions()
        outliers = self._find_outliers()
        concentrations = self._find_concentrations()
        correlations = self._find_correlations()
        unusual = self._find_unusual_combinations()

        if progress_callback:
            progress_callback({"phase": "distributions_complete"})

        # Phase 4: Group B — Record content analysis (new)
        text_fields = [f for f, t in self._field_types.items()
                       if t == "categorical" and self._looks_like_text(f)]
        keyword_anomalies = []
        for tf in text_fields:
            keyword_anomalies.extend(self._analyze_text_keywords(tf))
        completeness = self._analyze_completeness()
        content_size = self._analyze_content_size(text_fields)

        if progress_callback:
            progress_callback({"phase": "content_analysis_complete"})

        # Phase 5: Group C — Entity analysis (new)
        entity_stats = self._analyze_entities()
        entity_changes = self._analyze_entity_changes()
        cross_refs = self._analyze_cross_references()

        if progress_callback:
            progress_callback({"phase": "entity_analysis_complete"})

        # Phase 6: Group D — Graph analysis (new)
        graph = self._build_graph()
        graph_centrality = self._analyze_graph(graph) if graph else []
        inversions = self._detect_inversions(graph) if graph else []
        orphans = self._detect_orphans(graph) if graph else []

        if progress_callback:
            progress_callback({"phase": "graph_analysis_complete"})

        # Phase 7: Group E — Similarity analysis (new)
        duplicates = self._detect_near_duplicates(text_fields)
        templates = self._detect_templates()

        # Phase 8: Group F — Velocity analysis (new)
        velocity = self._analyze_velocity()
        recency = self._analyze_recency()

        if progress_callback:
            progress_callback({"phase": "all_analysis_complete"})

        # Combine all anomalies
        all_anomalies = (
            keyword_anomalies + completeness + content_size +
            entity_stats + entity_changes + cross_refs +
            graph_centrality + inversions + orphans +
            duplicates + templates + velocity + recency
        )

        clusters = self._build_anomaly_clusters(
            outliers, concentrations, unusual, all_anomalies)

        return {
            "record_count": self._record_count,
            "field_types": dict(self._field_types),
            "distributions": distributions,
            "outliers": outliers,
            "concentrations": concentrations,
            "correlations": correlations,
            "unusual_combinations": unusual,
            "anomaly_clusters": clusters,
            # New: all anomalies from Groups B-F in a flat list
            "content_anomalies": keyword_anomalies + completeness + content_size,
            "entity_anomalies": entity_stats + entity_changes + cross_refs,
            "graph_anomalies": graph_centrality + inversions + orphans,
            "similarity_anomalies": duplicates + templates,
            "velocity_anomalies": velocity + recency,
            "graph_summary": {
                "nodes": len(graph["nodes"]),
                "edges": len(graph["edges"]),
            } if graph else None,
        }

    # --- Field type detection ---

    def _detect_field_types(self, sample: list[dict]):
        """Infer field types from a sample of records."""
        field_examples = defaultdict(list)
        for record in sample:
            for key, value in record.items():
                if value is not None:
                    field_examples[key].append(value)

        for field, values in field_examples.items():
            self._field_types[field] = self._infer_type(values)

    def _infer_type(self, values: list) -> str:
        """Infer the type of a field from sample values."""
        if not values:
            return "unknown"

        # Check if it's a list field
        if any(isinstance(v, (list, tuple)) for v in values):
            return "list"

        # Check numeric
        numeric_count = 0
        for v in values[:50]:
            if isinstance(v, (int, float)):
                numeric_count += 1
            elif isinstance(v, str):
                try:
                    float(v.replace(",", ""))
                    numeric_count += 1
                except (ValueError, AttributeError):
                    pass

        if numeric_count > len(values[:50]) * 0.7:
            return "numeric"

        # Check temporal (dates)
        date_indicators = ["-", "/", "T", "Z"]
        date_count = sum(1 for v in values[:50]
                         if isinstance(v, str) and len(v) >= 8
                         and any(d in v for d in date_indicators))
        if date_count > len(values[:50]) * 0.5:
            return "temporal"

        return "categorical"

    # --- Value accumulation ---

    def _accumulate_batch(self, batch: list[dict]):
        """Accumulate field values from a batch of records."""
        for record in batch:
            for field, ftype in self._field_types.items():
                value = record.get(field)
                if value is None:
                    continue

                if ftype == "numeric":
                    try:
                        if isinstance(value, str):
                            value = float(value.replace(",", ""))
                        self._numeric_values[field].append(float(value))
                    except (ValueError, TypeError):
                        pass

                elif ftype == "categorical":
                    if isinstance(value, str):
                        self._categorical_values[field].append(value)
                    else:
                        self._categorical_values[field].append(str(value))

    # --- Quick stats (for progress updates) ---

    def _quick_stats(self) -> dict:
        """Compute quick summary stats for progress reporting."""
        stats = {}
        for field, values in self._numeric_values.items():
            if len(values) >= 10:
                sorted_v = sorted(values)
                stats[field] = {
                    "median": sorted_v[len(sorted_v) // 2],
                    "min": sorted_v[0],
                    "max": sorted_v[-1],
                    "count": len(values),
                }

        for field, values in self._categorical_values.items():
            if len(values) >= 10:
                counter = Counter(values)
                top = counter.most_common(3)
                total = len(values)
                stats[field] = {
                    "unique_values": len(counter),
                    "top_3": [{"value": v, "count": c, "pct": round(c / total * 100, 1)} for v, c in top],
                }

        return stats

    # --- Full distributions ---

    def _compute_distributions(self) -> dict:
        """Compute detailed distributions for all fields."""
        dists = {}

        for field, values in self._numeric_values.items():
            if not values:
                continue
            sorted_v = sorted(values)
            n = len(sorted_v)
            mean = sum(sorted_v) / n
            variance = sum((x - mean) ** 2 for x in sorted_v) / n
            std = math.sqrt(variance) if variance > 0 else 0

            dists[field] = {
                "type": "numeric",
                "count": n,
                "min": sorted_v[0],
                "max": sorted_v[-1],
                "mean": round(mean, 2),
                "median": sorted_v[n // 2],
                "std": round(std, 2),
                "p25": sorted_v[n // 4],
                "p75": sorted_v[3 * n // 4],
                "p95": sorted_v[int(n * 0.95)],
                "zeros": sum(1 for v in sorted_v if v == 0),
            }

        for field, values in self._categorical_values.items():
            if not values:
                continue
            counter = Counter(values)
            total = len(values)
            top_10 = counter.most_common(10)

            dists[field] = {
                "type": "categorical",
                "count": total,
                "unique_values": len(counter),
                "top_10": [
                    {"value": v, "count": c, "pct": round(c / total * 100, 1)}
                    for v, c in top_10
                ],
                "concentration_ratio": round(
                    sum(c for _, c in top_10[:3]) / total * 100, 1
                ) if total > 0 else 0,
            }

        return dists

    # --- Outlier detection ---

    def _find_outliers(self) -> list[dict]:
        """Find records with numeric values >2 std dev from the mean."""
        outliers = []

        for field, values in self._numeric_values.items():
            if len(values) < 20:
                continue

            mean = sum(values) / len(values)
            variance = sum((x - mean) ** 2 for x in values) / len(values)
            std = math.sqrt(variance) if variance > 0 else 0
            if std == 0:
                continue

            threshold_high = mean + 2 * std
            threshold_low = mean - 2 * std

            # Find records with outlier values
            for record in self._records:
                val = record.get(field)
                if val is None:
                    continue
                try:
                    val = float(val) if isinstance(val, str) else float(val)
                except (ValueError, TypeError):
                    continue

                if val > threshold_high or val < threshold_low:
                    z_score = (val - mean) / std
                    outliers.append({
                        "field": field,
                        "value": val,
                        "z_score": round(z_score, 2),
                        "direction": "high" if val > mean else "low",
                        "record_id": record.get("name", record.get("id", "?")),
                        "record_summary": self._record_summary(record),
                    })

            # Keep top 20 outliers per field
            field_outliers = [o for o in outliers if o["field"] == field]
            field_outliers.sort(key=lambda x: abs(x["z_score"]), reverse=True)

        # Deduplicate and limit
        seen = set()
        unique = []
        for o in sorted(outliers, key=lambda x: abs(x["z_score"]), reverse=True):
            key = (o["field"], o["record_id"])
            if key not in seen:
                seen.add(key)
                unique.append(o)
            if len(unique) >= 50:
                break

        return unique

    # --- Concentration detection ---

    def _find_concentrations(self) -> list[dict]:
        """Find fields where a few values dominate."""
        concentrations = []

        for field, values in self._categorical_values.items():
            if len(values) < 20:
                continue

            counter = Counter(values)
            total = len(values)
            unique = len(counter)

            # Skip fields with very high cardinality (like IDs)
            if unique > total * 0.8:
                continue

            top_3 = counter.most_common(3)
            top_3_pct = sum(c for _, c in top_3) / total * 100

            # Flag if top 3 values account for >50% or single value >30%
            if top_3_pct > 50 or (top_3[0][1] / total * 100 > 30):
                concentrations.append({
                    "field": field,
                    "unique_values": unique,
                    "total_records": total,
                    "top_values": [
                        {"value": v, "count": c, "pct": round(c / total * 100, 1)}
                        for v, c in top_3
                    ],
                    "concentration_pct": round(top_3_pct, 1),
                    "severity": "high" if top_3_pct > 80 else "medium" if top_3_pct > 60 else "moderate",
                })

        # Also check numeric concentration (many zeros, many identical values)
        for field, values in self._numeric_values.items():
            if len(values) < 20:
                continue
            counter = Counter(values)
            total = len(values)
            most_common_val, most_common_count = counter.most_common(1)[0]
            if most_common_count / total > 0.3:
                concentrations.append({
                    "field": field,
                    "type": "numeric_concentration",
                    "dominant_value": most_common_val,
                    "count": most_common_count,
                    "pct": round(most_common_count / total * 100, 1),
                    "total_records": total,
                    "severity": "high" if most_common_count / total > 0.6 else "moderate",
                })

        return concentrations

    # --- Correlation detection ---

    def _find_correlations(self) -> list[dict]:
        """Find correlated numeric field pairs."""
        correlations = []
        numeric_fields = list(self._numeric_values.keys())

        # Only check pairs if reasonable number of fields
        if len(numeric_fields) > 15:
            numeric_fields = numeric_fields[:15]

        for i in range(len(numeric_fields)):
            for j in range(i + 1, len(numeric_fields)):
                f1, f2 = numeric_fields[i], numeric_fields[j]
                v1, v2 = self._numeric_values[f1], self._numeric_values[f2]

                if len(v1) < 20 or len(v2) < 20:
                    continue

                # Align values by record position (take min length)
                n = min(len(v1), len(v2), 500)  # sample for speed
                corr = self._pearson(v1[:n], v2[:n])

                if abs(corr) > 0.5:
                    correlations.append({
                        "field_a": f1,
                        "field_b": f2,
                        "correlation": round(corr, 3),
                        "direction": "positive" if corr > 0 else "negative",
                        "strength": "strong" if abs(corr) > 0.8 else "moderate",
                        "sample_size": n,
                    })

        return sorted(correlations, key=lambda x: abs(x["correlation"]), reverse=True)

    def _pearson(self, x: list, y: list) -> float:
        """Compute Pearson correlation coefficient."""
        n = min(len(x), len(y))
        if n < 5:
            return 0.0

        mx = sum(x[:n]) / n
        my = sum(y[:n]) / n

        num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
        dx = math.sqrt(sum((x[i] - mx) ** 2 for i in range(n)))
        dy = math.sqrt(sum((y[i] - my) ** 2 for i in range(n)))

        if dx == 0 or dy == 0:
            return 0.0
        return num / (dx * dy)

    # --- Unusual combinations ---

    def _find_unusual_combinations(self) -> list[dict]:
        """Find field value pairs that co-occur more/less than expected."""
        unusual = []
        cat_fields = [f for f in self._categorical_values if len(self._categorical_values[f]) >= 20]

        # Limit to most interesting categorical fields (not too many unique values)
        cat_fields = [f for f in cat_fields
                      if 2 <= len(set(self._categorical_values[f])) <= 50]

        if len(cat_fields) > 8:
            cat_fields = cat_fields[:8]

        # Also look at high/low numeric vs categorical
        numeric_fields = [f for f in self._numeric_values if len(self._numeric_values[f]) >= 20]

        # Categorical x categorical
        for i in range(len(cat_fields)):
            for j in range(i + 1, len(cat_fields)):
                f1, f2 = cat_fields[i], cat_fields[j]
                combos = self._cross_tabulate(f1, f2)
                if combos:
                    unusual.extend(combos)

        # Numeric outlier x categorical (e.g., "high downloads AND 1 maintainer")
        for nf in numeric_fields[:5]:
            values = self._numeric_values[nf]
            mean = sum(values) / len(values)
            std = math.sqrt(sum((x - mean) ** 2 for x in values) / len(values))
            if std == 0:
                continue
            threshold = mean + 2 * std

            for cf in cat_fields[:5]:
                high_records = [r for r in self._records
                                if _safe_float(r.get(nf)) is not None
                                and _safe_float(r.get(nf)) > threshold
                                and r.get(cf)]

                if len(high_records) < 3:
                    continue

                cat_dist = Counter(r.get(cf) for r in high_records)
                overall_dist = Counter(self._categorical_values[cf])
                total_overall = len(self._categorical_values[cf])

                for val, count in cat_dist.most_common(3):
                    expected_pct = overall_dist.get(val, 0) / total_overall * 100
                    actual_pct = count / len(high_records) * 100
                    if actual_pct > expected_pct * 1.5 and count >= 3:
                        unusual.append({
                            "type": "numeric_categorical",
                            "description": f"High-{nf} records are {actual_pct:.0f}% '{val}' for {cf} "
                                           f"(vs {expected_pct:.0f}% overall)",
                            "field_a": nf,
                            "field_b": cf,
                            "condition": f"{nf} > {threshold:.0f}",
                            "value": val,
                            "actual_pct": round(actual_pct, 1),
                            "expected_pct": round(expected_pct, 1),
                            "overrepresentation": round(actual_pct / max(expected_pct, 0.1), 1),
                            "count": count,
                        })

        return sorted(unusual, key=lambda x: x.get("overrepresentation", 0), reverse=True)[:20]

    def _cross_tabulate(self, f1: str, f2: str) -> list[dict]:
        """Find unusual co-occurrences between two categorical fields."""
        results = []
        pairs = defaultdict(int)
        f1_counts = Counter()
        f2_counts = Counter()
        total = 0

        for record in self._records:
            v1, v2 = record.get(f1), record.get(f2)
            if v1 is None or v2 is None:
                continue
            v1, v2 = str(v1), str(v2)
            pairs[(v1, v2)] += 1
            f1_counts[v1] += 1
            f2_counts[v2] += 1
            total += 1

        if total < 20:
            return []

        for (v1, v2), observed in pairs.items():
            if observed < 3:
                continue
            expected = (f1_counts[v1] * f2_counts[v2]) / total
            if expected < 1:
                continue
            ratio = observed / expected
            if ratio > 2.0:
                results.append({
                    "type": "categorical_pair",
                    "description": f"'{v1}' ({f1}) + '{v2}' ({f2}) co-occur {ratio:.1f}x more than expected",
                    "field_a": f1,
                    "value_a": v1,
                    "field_b": f2,
                    "value_b": v2,
                    "observed": observed,
                    "expected": round(expected, 1),
                    "overrepresentation": round(ratio, 1),
                })

        return sorted(results, key=lambda x: x["overrepresentation"], reverse=True)[:5]

    # =================================================================
    # GROUP B: Record Content Analysis
    # =================================================================

    def _looks_like_text(self, field_name: str) -> bool:
        """Heuristic: is this categorical field actually free text?"""
        values = self._categorical_values.get(field_name, [])
        if len(values) < 10:
            return False
        # Text fields have high cardinality and longer values
        unique_ratio = len(set(values)) / len(values)
        avg_len = sum(len(str(v)) for v in values[:100]) / min(len(values), 100)
        return unique_ratio > 0.3 and avg_len > 15

    def _get_id(self, record: dict) -> str:
        """Get the best identifier for a record."""
        for key in ("name", "id", "title", "key"):
            if record.get(key):
                return str(record[key])
        return "?"

    def _analyze_text_keywords(self, field_name: str) -> list[dict]:
        """Extract keywords from a text field and find unusual keyword + numeric combinations."""
        anomalies = []
        all_words = Counter()
        record_words = {}

        for i, r in enumerate(self._records):
            text = str(r.get(field_name, "")).lower()
            words = set(re.findall(r'[a-z]{3,}', text))
            record_words[i] = words
            all_words.update(words)

        total = len(self._records)
        if total < 20:
            return anomalies

        # Signal words: uncommon but present (< 5% of records, >= 3 occurrences)
        signal_words = {
            word: count for word, count in all_words.items()
            if 3 <= count <= total * 0.05
        }

        # Cross-reference signal words with numeric fields
        for word, word_count in list(signal_words.items())[:30]:
            word_records = [
                self._records[i] for i, words in record_words.items()
                if word in words
            ]

            for num_field, num_values in self._numeric_values.items():
                if len(num_values) < 20:
                    continue

                word_values = [
                    _safe_float(r.get(num_field))
                    for r in word_records
                    if _safe_float(r.get(num_field)) is not None
                ]
                if len(word_values) < 3:
                    continue

                word_mean = statistics.mean(word_values)
                all_mean = statistics.mean(num_values)
                if all_mean == 0:
                    continue

                ratio = word_mean / all_mean
                if (ratio > 3.0 or (ratio < 0.33 and word_mean > 0)):
                    anomalies.append({
                        "type": "keyword_numeric_anomaly",
                        "keyword": word,
                        "field": field_name,
                        "numeric_field": num_field,
                        "keyword_count": word_count,
                        "keyword_mean": round(word_mean, 1),
                        "overall_mean": round(all_mean, 1),
                        "ratio": round(ratio, 2),
                        "description": (
                            f"Records containing '{word}' in '{field_name}' have "
                            f"{ratio:.1f}x the average '{num_field}' "
                            f"({word_mean:.0f} vs {all_mean:.0f})"
                        ),
                        "examples": [self._get_id(r) for r in word_records[:5]],
                    })

        anomalies.sort(key=lambda x: abs(x.get("ratio", 1) - 1), reverse=True)
        return anomalies[:15]

    def _analyze_completeness(self) -> list[dict]:
        """Find records missing fields that most records have."""
        anomalies = []
        total = self._record_count

        for field_name in self._field_types:
            present = sum(
                1 for r in self._records
                if r.get(field_name) is not None
                and r.get(field_name) != ""
                and r.get(field_name) != 0
            )
            presence_rate = present / total if total > 0 else 0

            if presence_rate > 0.8:
                missing = [
                    r for r in self._records
                    if r.get(field_name) is None
                    or r.get(field_name) == ""
                    or r.get(field_name) == 0
                ]
                if missing and len(missing) < total * 0.2:
                    anomalies.append({
                        "type": "missing_field",
                        "field": field_name,
                        "presence_rate": round(presence_rate * 100, 1),
                        "missing_count": len(missing),
                        "description": (
                            f"{len(missing)} records are MISSING '{field_name}' "
                            f"which {presence_rate * 100:.0f}% of records have"
                        ),
                        "examples": [self._get_id(r) for r in missing[:10]],
                    })

        return anomalies

    def _analyze_content_size(self, text_fields: list[str]) -> list[dict]:
        """Flag records whose text content is unusually short or long."""
        anomalies = []

        for field_name in text_fields:
            lengths = [len(str(r.get(field_name, ""))) for r in self._records]
            if not lengths or max(lengths) == 0:
                continue

            mean_len = statistics.mean(lengths)
            if len(lengths) < 2:
                continue
            std_len = statistics.stdev(lengths)
            if std_len == 0:
                continue

            short = [(r, len(str(r.get(field_name, ""))))
                     for r in self._records
                     if 0 < len(str(r.get(field_name, ""))) < mean_len - 2 * std_len]
            long = [(r, len(str(r.get(field_name, ""))))
                    for r in self._records
                    if len(str(r.get(field_name, ""))) > mean_len + 2 * std_len]

            if short:
                anomalies.append({
                    "type": "unusually_short_content",
                    "field": field_name,
                    "mean_length": round(mean_len),
                    "count": len(short),
                    "description": (
                        f"{len(short)} records have unusually short "
                        f"'{field_name}' (mean: {mean_len:.0f} chars)"
                    ),
                    "examples": [self._get_id(r) for r, _ in short[:5]],
                })
            if long:
                anomalies.append({
                    "type": "unusually_long_content",
                    "field": field_name,
                    "mean_length": round(mean_len),
                    "count": len(long),
                    "description": (
                        f"{len(long)} records have unusually long "
                        f"'{field_name}' (mean: {mean_len:.0f} chars)"
                    ),
                    "examples": [self._get_id(r) for r, _ in long[:5]],
                })

        return anomalies

    # =================================================================
    # GROUP C: Entity Analysis
    # =================================================================

    def _analyze_entities(self) -> list[dict]:
        """Group records by categorical fields and flag entities with outlier aggregates."""
        anomalies = []
        cat_fields = [f for f, t in self._field_types.items() if t == "categorical"]
        # Skip high-cardinality fields
        cat_fields = [f for f in cat_fields
                      if 3 <= len(set(self._categorical_values.get(f, []))) <= 100]

        for group_field in cat_fields[:6]:
            groups = defaultdict(list)
            for r in self._records:
                key = str(r.get(group_field, ""))
                if key:
                    groups[key].append(r)

            if len(groups) < 3:
                continue

            for num_field in list(self._numeric_values.keys())[:5]:
                group_totals = {}
                for gk, grs in groups.items():
                    vals = [_safe_float(r.get(num_field)) for r in grs
                            if _safe_float(r.get(num_field)) is not None]
                    if vals:
                        group_totals[gk] = {
                            "count": len(grs), "total": sum(vals),
                            "mean": statistics.mean(vals),
                        }

                if len(group_totals) < 3:
                    continue

                all_totals = [s["total"] for s in group_totals.values()]
                if len(all_totals) < 2:
                    continue
                mean_total = statistics.mean(all_totals)
                std_total = statistics.stdev(all_totals)
                if std_total == 0:
                    continue

                for gk, gs in group_totals.items():
                    z = (gs["total"] - mean_total) / std_total
                    if abs(z) > 2:
                        anomalies.append({
                            "type": "entity_concentration",
                            "group_field": group_field,
                            "entity": gk,
                            "numeric_field": num_field,
                            "entity_total": gs["total"],
                            "entity_count": gs["count"],
                            "z_score": round(z, 2),
                            "description": (
                                f"'{gk}' (grouped by '{group_field}') has "
                                f"{gs['total']:,.0f} total '{num_field}' across "
                                f"{gs['count']} records — {z:.1f} sigma from peers"
                            ),
                        })

        anomalies.sort(key=lambda x: abs(x.get("z_score", 0)), reverse=True)
        return anomalies[:20]

    def _analyze_entity_changes(self) -> list[dict]:
        """Track how entity values change over time."""
        anomalies = []
        temp_fields = [f for f, t in self._field_types.items() if t == "temporal"]
        cat_fields = [f for f, t in self._field_types.items()
                      if t == "categorical"
                      and 3 <= len(set(self._categorical_values.get(f, []))) <= 100]

        if not temp_fields:
            return anomalies

        time_field = temp_fields[0]

        for entity_field in cat_fields[:4]:
            entities = defaultdict(list)
            for r in self._records:
                key = str(r.get(entity_field, ""))
                if key:
                    entities[key].append(r)

            for entity_key, entity_records in entities.items():
                if len(entity_records) < 3:
                    continue

                try:
                    sorted_recs = sorted(entity_records,
                                         key=lambda r: str(r.get(time_field, "")))
                except Exception:
                    continue

                for num_field in list(self._numeric_values.keys())[:3]:
                    values = [_safe_float(r.get(num_field)) for r in sorted_recs
                              if _safe_float(r.get(num_field)) is not None]
                    if len(values) < 3:
                        continue

                    mid = len(values) // 2
                    first_half = statistics.mean(values[:mid])
                    second_half = statistics.mean(values[mid:])
                    if first_half == 0:
                        continue

                    change = second_half / first_half
                    if change > 2.0 or change < 0.5:
                        anomalies.append({
                            "type": "entity_temporal_change",
                            "entity_field": entity_field,
                            "entity": entity_key,
                            "numeric_field": num_field,
                            "direction": "increasing" if change > 1 else "decreasing",
                            "change_ratio": round(change, 2),
                            "description": (
                                f"'{entity_key}' shows '{num_field}' "
                                f"{'increased' if change > 1 else 'decreased'} "
                                f"{change:.1f}x over time"
                            ),
                        })

        return anomalies[:15]

    def _analyze_cross_references(self) -> list[dict]:
        """Find records that reference other records. Detect asymmetric references."""
        anomalies = []

        # Build identifier index
        id_field = None
        identifier_set = set()
        for f in ("name", "id", "key", "title"):
            if f in self._field_types:
                id_field = f
                identifier_set = {str(r.get(f, "")).lower() for r in self._records if r.get(f)}
                break

        if not identifier_set or len(identifier_set) < 10:
            return anomalies

        # For each text/categorical field, count references to other records
        reference_counts = Counter()
        for r in self._records:
            record_id = str(r.get(id_field, "")).lower()
            for field_name, ftype in self._field_types.items():
                if field_name == id_field:
                    continue
                text = str(r.get(field_name, "")).lower()
                for identifier in identifier_set:
                    if identifier in text and identifier != record_id and len(identifier) > 2:
                        reference_counts[identifier] += 1

        for ref_id, count in reference_counts.most_common(10):
            if count >= 3:
                anomalies.append({
                    "type": "highly_referenced",
                    "referenced_entity": ref_id,
                    "reference_count": count,
                    "description": f"'{ref_id}' is referenced by {count} other records",
                })

        return anomalies

    # =================================================================
    # GROUP D: Graph / Relationship Analysis
    # =================================================================

    def _build_graph(self) -> dict | None:
        """Build a directed graph from list-type fields (dependencies, references)."""
        # Detect list fields
        list_fields = [f for f, t in self._field_types.items() if t == "list"]

        # Also check for dict fields (e.g., dependencies as {name: version})
        for f in self._field_types:
            sample = [r.get(f) for r in self._records[:50] if r.get(f)]
            if any(isinstance(v, dict) for v in sample) and f not in list_fields:
                list_fields.append(f)

        if not list_fields:
            return None

        # Find ID field
        id_field = None
        for f in ("name", "id", "key", "title"):
            if f in self._field_types:
                id_field = f
                break
        if not id_field:
            return None

        edges = []
        nodes = set()

        for r in self._records:
            source = str(r.get(id_field, ""))
            if not source:
                continue
            nodes.add(source)

            for list_field in list_fields:
                deps = r.get(list_field, [])
                if isinstance(deps, dict):
                    deps = list(deps.keys())
                if isinstance(deps, str):
                    deps = [d.strip() for d in deps.split(",") if d.strip()]
                if not isinstance(deps, list):
                    continue

                for dep in deps:
                    dep = str(dep).strip()
                    if dep and dep != source:
                        edges.append((source, dep))
                        nodes.add(dep)

        if not edges:
            return None

        return {"nodes": nodes, "edges": edges, "id_field": id_field}

    def _analyze_graph(self, graph: dict) -> list[dict]:
        """Compute centrality metrics. Flag high in-degree nodes."""
        anomalies = []
        if not graph:
            return anomalies

        in_degree = Counter()
        out_degree = Counter()
        for source, target in graph["edges"]:
            out_degree[source] += 1
            in_degree[target] += 1

        if not in_degree:
            return anomalies

        mean_in = statistics.mean(in_degree.values()) if in_degree else 0

        for node, degree in in_degree.most_common(15):
            if degree > mean_in * 5 and degree >= 5:
                anomalies.append({
                    "type": "high_in_degree",
                    "node": node,
                    "in_degree": degree,
                    "mean_in_degree": round(mean_in, 1),
                    "ratio": round(degree / max(mean_in, 1), 1),
                    "description": (
                        f"'{node}' is depended on by {degree} records "
                        f"({degree / max(mean_in, 1):.0f}x the average)"
                    ),
                })

        # External dependencies: depended on but not in the record set
        record_ids = {str(r.get(graph["id_field"], "")) for r in self._records
                      if r.get(graph["id_field"])}
        external = {n: in_degree[n] for n in in_degree
                    if n not in record_ids and in_degree[n] >= 3}
        if external:
            top_ext = sorted(external.items(), key=lambda x: x[1], reverse=True)[:10]
            anomalies.append({
                "type": "external_dependencies",
                "count": len(external),
                "top_external": [{"name": n, "depended_by": c} for n, c in top_ext],
                "description": (
                    f"{len(external)} external dependencies not in the catalog "
                    f"but depended on by records in the dataset"
                ),
            })

        return anomalies

    def _detect_inversions(self, graph: dict) -> list[dict]:
        """Find dependencies with HIGHER numeric values than their parents."""
        anomalies = []
        if not graph:
            return anomalies

        lookup = {}
        id_field = graph["id_field"]
        for r in self._records:
            name = str(r.get(id_field, ""))
            if name:
                lookup[name] = r

        for source, target in graph["edges"]:
            if source not in lookup or target not in lookup:
                continue

            for num_field in list(self._numeric_values.keys())[:5]:
                sv = _safe_float(lookup[source].get(num_field))
                tv = _safe_float(lookup[target].get(num_field))
                if sv is not None and tv is not None and sv > 0 and tv > sv * 1.5:
                    anomalies.append({
                        "type": "dependency_inversion",
                        "source": source,
                        "dependency": target,
                        "field": num_field,
                        "source_value": sv,
                        "dependency_value": tv,
                        "ratio": round(tv / sv, 2),
                        "description": (
                            f"'{target}' (dependency) has {tv:,.0f} '{num_field}' "
                            f"while '{source}' (parent) has only {sv:,.0f} — "
                            f"dependency exceeds parent {tv / sv:.1f}x"
                        ),
                    })

        anomalies.sort(key=lambda x: x.get("ratio", 0), reverse=True)
        return anomalies[:20]

    def _detect_orphans(self, graph: dict) -> list[dict]:
        """Find references to things that don't exist in the dataset."""
        anomalies = []
        if not graph:
            return anomalies

        record_ids = {str(r.get(graph["id_field"], ""))
                      for r in self._records if r.get(graph["id_field"])}

        orphan_refs = Counter()
        for source, target in graph["edges"]:
            if target not in record_ids:
                orphan_refs[target] += 1

        for ref, count in orphan_refs.most_common(10):
            if count >= 2:
                anomalies.append({
                    "type": "orphan_reference",
                    "referenced": ref,
                    "reference_count": count,
                    "description": (
                        f"'{ref}' is referenced by {count} records "
                        f"but does not exist in the dataset"
                    ),
                })

        return anomalies

    # =================================================================
    # GROUP E: Similarity Analysis
    # =================================================================

    def _detect_near_duplicates(self, text_fields: list[str]) -> list[dict]:
        """Find records with suspiciously similar text content."""
        anomalies = []

        for field_name in text_fields[:3]:
            record_tokens = []
            for r in self._records:
                text = str(r.get(field_name, "")).lower()
                tokens = set(re.findall(r'[a-z]{3,}', text))
                record_tokens.append((r, tokens))

            clusters = []
            seen = set()

            for i, (r1, t1) in enumerate(record_tokens):
                if i in seen or not t1 or len(t1) < 3:
                    continue

                cluster = [r1]
                for j, (r2, t2) in enumerate(record_tokens[i + 1:], i + 1):
                    if j in seen or not t2:
                        continue
                    intersection = len(t1 & t2)
                    union = len(t1 | t2)
                    if union > 0 and intersection / union > 0.8:
                        cluster.append(r2)
                        seen.add(j)

                if len(cluster) >= 2:
                    seen.add(i)
                    clusters.append(cluster)

            for cluster in clusters[:5]:
                anomalies.append({
                    "type": "near_duplicate_cluster",
                    "field": field_name,
                    "cluster_size": len(cluster),
                    "description": (
                        f"{len(cluster)} records have >80% similar "
                        f"'{field_name}' content"
                    ),
                    "examples": [self._get_id(r) for r in cluster[:5]],
                })

        return anomalies

    def _detect_templates(self) -> list[dict]:
        """Find groups of records sharing identical values in multiple fields."""
        anomalies = []
        cat_fields = [f for f, t in self._field_types.items()
                      if t == "categorical"
                      and 2 <= len(set(self._categorical_values.get(f, []))) <= 50]

        if len(cat_fields) < 2:
            return anomalies

        # Check pairs of categorical fields
        for i in range(min(len(cat_fields), 6)):
            for j in range(i + 1, min(len(cat_fields), 6)):
                f1, f2 = cat_fields[i], cat_fields[j]
                combos = Counter()
                combo_records = defaultdict(list)

                for r in self._records:
                    v1 = str(r.get(f1, ""))
                    v2 = str(r.get(f2, ""))
                    if v1 and v2:
                        key = (v1, v2)
                        combos[key] += 1
                        combo_records[key].append(r)

                for (v1, v2), count in combos.most_common(3):
                    if 5 <= count < self._record_count * 0.5:
                        anomalies.append({
                            "type": "template_pattern",
                            "fields": [f1, f2],
                            "values": [v1, v2],
                            "count": count,
                            "description": (
                                f"{count} records share '{f1}'='{v1}' AND "
                                f"'{f2}'='{v2}' — possible templated creation"
                            ),
                            "examples": [self._get_id(r) for r in combo_records[(v1, v2)][:3]],
                        })

        return anomalies[:10]

    # =================================================================
    # GROUP F: Velocity Analysis
    # =================================================================

    def _analyze_velocity(self) -> list[dict]:
        """Measure how fast things are changing relative to age."""
        anomalies = []
        temp_fields = [f for f, t in self._field_types.items() if t == "temporal"]
        if not temp_fields:
            return anomalies

        time_field = temp_fields[0]
        now = datetime.now(timezone.utc)

        for num_field in list(self._numeric_values.keys())[:5]:
            velocities = []

            for r in self._records:
                num_val = _safe_float(r.get(num_field))
                if num_val is None:
                    continue

                dt = _parse_date(r.get(time_field))
                if dt is None:
                    continue

                age_days = max((now - dt).days, 1)
                velocity = num_val / (age_days / 365.25)
                velocities.append((r, velocity))

            if len(velocities) < 10:
                continue

            all_v = [v for _, v in velocities]
            mean_v = statistics.mean(all_v)
            std_v = statistics.stdev(all_v) if len(all_v) > 1 else 0
            if std_v == 0:
                continue

            for r, v in velocities:
                z = (v - mean_v) / std_v
                if z > 3:
                    anomalies.append({
                        "type": "high_velocity",
                        "record": self._get_id(r),
                        "field": num_field,
                        "velocity": round(v, 1),
                        "mean_velocity": round(mean_v, 1),
                        "z_score": round(z, 2),
                        "description": (
                            f"'{self._get_id(r)}' has {v:.0f} '{num_field}'/year — "
                            f"{z:.1f} sigma above average"
                        ),
                    })

        anomalies.sort(key=lambda x: x.get("z_score", 0), reverse=True)
        return anomalies[:10]

    def _analyze_recency(self) -> list[dict]:
        """Flag records that are stale but still heavily used."""
        anomalies = []
        temp_fields = [f for f, t in self._field_types.items() if t == "temporal"]
        if not temp_fields:
            return anomalies

        time_field = temp_fields[0]
        now = datetime.now(timezone.utc)
        old_cutoff = now - timedelta(days=730)  # 2 years

        for num_field in list(self._numeric_values.keys())[:5]:
            stale_but_used = []

            for r in self._records:
                num_val = _safe_float(r.get(num_field))
                if num_val is None or num_val <= 0:
                    continue

                dt = _parse_date(r.get(time_field))
                if dt is None:
                    continue

                if dt < old_cutoff:
                    stale_but_used.append((r, num_val, dt))

            if stale_but_used:
                stale_but_used.sort(key=lambda x: x[1], reverse=True)
                anomalies.append({
                    "type": "stale_but_active",
                    "field": num_field,
                    "time_field": time_field,
                    "count": len(stale_but_used),
                    "description": (
                        f"{len(stale_but_used)} records have no activity in 2+ years "
                        f"but non-zero '{num_field}'"
                    ),
                    "examples": [
                        {"id": self._get_id(r), num_field: v,
                         "last_activity": dt.isoformat()[:10]}
                        for r, v, dt in stale_but_used[:10]
                    ],
                })

        return anomalies

    # =================================================================
    # Anomaly clusters (updated)
    # =================================================================

    def _build_anomaly_clusters(self, outliers: list, concentrations: list,
                                unusual: list, all_anomalies: list = None) -> list[dict]:
        """Group related anomalies into clusters with severity scores.

        Domain-agnostic: clusters are built from statistical signals only,
        not field names. Works for npm packages, hospital records, pollution
        data, news archives — anything.
        """
        clusters = []

        # Cluster 1: High-value numeric outliers paired with categorical concentration
        # (e.g., high downloads + few maintainers, high pollution + one source,
        #  high readmission rate + one department)
        high_outliers = [o for o in outliers if o.get("direction") == "high" and o.get("z_score", 0) > 3]
        high_concentrations = [c for c in concentrations if c.get("severity") in ("high", "medium")]

        if high_outliers and high_concentrations:
            # Find the most extreme numeric field and most concentrated categorical field
            top_numeric = high_outliers[0]["field"]
            top_categorical = high_concentrations[0]["field"]
            clusters.append({
                "name": f"Extreme {top_numeric} values with {top_categorical} concentration",
                "severity": "high",
                "evidence_count": len(high_outliers) + len(high_concentrations),
                "description": (
                    f"{len(high_outliers)} records with outlier {top_numeric} values "
                    f"combined with {top_categorical} concentration "
                    f"({high_concentrations[0].get('concentration_pct', '?')}% in top 3 values)"
                ),
                "outliers": high_outliers[:5],
                "concentrations": high_concentrations[:3],
            })

        # Cluster 2: All high-severity concentrations (few values dominating a field)
        for conc in concentrations:
            if conc.get("severity") == "high":
                field = conc.get("field", "?")
                top_val = conc.get("top_values", [{}])[0]
                clusters.append({
                    "name": f"{field} dominated by '{top_val.get('value', '?')}'",
                    "severity": "high",
                    "evidence_count": conc.get("total_records", 0),
                    "description": (
                        f"Field '{field}' has {conc.get('concentration_pct', '?')}% concentration "
                        f"in top 3 values across {conc.get('total_records', '?')} records"
                    ),
                    "concentrations": [conc],
                })

        # Cluster 3: Numeric fields with many zeros or identical values
        numeric_concentrations = [c for c in concentrations if c.get("type") == "numeric_concentration"]
        for nc in numeric_concentrations:
            clusters.append({
                "name": f"{nc.get('field', '?')} has {nc.get('pct', '?')}% identical values ({nc.get('dominant_value', '?')})",
                "severity": nc.get("severity", "moderate"),
                "evidence_count": nc.get("count", 0),
                "description": (
                    f"{nc.get('count', '?')} of {nc.get('total_records', '?')} records "
                    f"have {nc.get('field', '?')} = {nc.get('dominant_value', '?')}"
                ),
                "concentrations": [nc],
            })

        # Cluster 4: Unusual combinations (statistical co-occurrences)
        for u in unusual[:5]:
            overrep = u.get("overrepresentation", 0)
            if overrep >= 2.0:
                clusters.append({
                    "name": u.get("description", "Unusual co-occurrence"),
                    "severity": "high" if overrep > 5 else "moderate" if overrep > 3 else "low",
                    "evidence_count": u.get("count", u.get("observed", 0)),
                    "description": u.get("description", ""),
                    "detail": u,
                })

        # Cluster 5+: All anomalies from Groups B-F
        if all_anomalies:
            # Map anomaly types to severity
            type_severity = {
                "dependency_inversion": "high",
                "high_in_degree": "high",
                "entity_concentration": "high",
                "keyword_numeric_anomaly": "high",
                "stale_but_active": "high",
                "high_velocity": "moderate",
                "missing_field": "moderate",
                "near_duplicate_cluster": "moderate",
                "template_pattern": "moderate",
                "orphan_reference": "moderate",
                "highly_referenced": "moderate",
                "entity_temporal_change": "moderate",
                "unusually_short_content": "low",
                "unusually_long_content": "low",
                "external_dependencies": "moderate",
            }

            for a in all_anomalies:
                desc = a.get("description", "")
                if not desc:
                    continue
                severity = type_severity.get(a.get("type", ""), "low")
                clusters.append({
                    "name": desc[:100],
                    "severity": severity,
                    "evidence_count": a.get("count", a.get("cluster_size",
                                     a.get("reference_count", 1))),
                    "description": desc,
                    "detail": a,
                })

        # Deduplicate by name
        seen_names = set()
        unique_clusters = []
        for c in clusters:
            if c["name"] not in seen_names:
                seen_names.add(c["name"])
                unique_clusters.append(c)

        # Sort by severity
        severity_order = {"high": 0, "medium": 1, "moderate": 2, "low": 3}
        unique_clusters.sort(key=lambda c: severity_order.get(c.get("severity", "low"), 4))

        return unique_clusters

    # --- Helpers ---

    def _record_summary(self, record: dict) -> str:
        """Create a brief summary of a record for display."""
        parts = []
        for key in ["name", "title", "id", "description"]:
            if record.get(key):
                val = str(record[key])
                parts.append(f"{key}={val[:60]}")
                if len(parts) >= 2:
                    break
        return ", ".join(parts) if parts else str(list(record.keys())[:3])


def _safe_float(v) -> float | None:
    """Safely convert a value to float."""
    if v is None:
        return None
    try:
        return float(v) if not isinstance(v, (list, dict)) else None
    except (ValueError, TypeError):
        return None


def _parse_date(v) -> datetime | None:
    """Safely parse a date value."""
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
    if not isinstance(v, str) or len(v) < 8:
        return None
    try:
        # ISO 8601
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    try:
        # YYYY-MM-DD
        return datetime.strptime(v[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
