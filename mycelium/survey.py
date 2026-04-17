"""Programmatic Survey — catalog everything before spending on AI.

Pure Python. Zero LLM cost. Scans all accessible records from a data
source and computes statistics, distributions, outliers, concentrations,
and anomaly clusters. The output tells you what's interesting BEFORE
you commit any budget to AI-driven exploration.

Usage:
    survey = ProgrammaticSurvey()
    results = survey.analyze(records, progress_callback=emit_progress)

    # results contains:
    # - field_types: detected types for each field
    # - distributions: histograms and stats per field
    # - outliers: records with values >2 std dev from median
    # - concentrations: fields where few values dominate
    # - correlations: numeric field pairs that move together
    # - unusual_combinations: field value pairs that co-occur unexpectedly
    # - anomaly_clusters: grouped anomalies with severity scores
"""

import math
from collections import Counter, defaultdict


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

        # Phase 3: Compute full analysis
        distributions = self._compute_distributions()
        outliers = self._find_outliers()
        concentrations = self._find_concentrations()
        correlations = self._find_correlations()
        unusual = self._find_unusual_combinations()
        clusters = self._build_anomaly_clusters(outliers, concentrations, unusual)

        return {
            "record_count": self._record_count,
            "field_types": dict(self._field_types),
            "distributions": distributions,
            "outliers": outliers,
            "concentrations": concentrations,
            "correlations": correlations,
            "unusual_combinations": unusual,
            "anomaly_clusters": clusters,
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

    # --- Anomaly clusters ---

    def _build_anomaly_clusters(self, outliers: list, concentrations: list,
                                unusual: list) -> list[dict]:
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
