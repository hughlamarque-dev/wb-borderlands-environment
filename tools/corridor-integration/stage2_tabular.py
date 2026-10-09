"""Preserve source project IDs, row identities, units and missing observations."""
import csv
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path
from download_inputs import hashes

GEMS_COLUMNS = {"lead GP", "project", "country", "long", "lat"}
MASS_TO_TONNES = {"kg": 0.001, "g": 0.000001, "t": 1.0, "tonne": 1.0, "tonnes": 1.0, "mt": 1.0, "lb": 0.00045359237}
MASS_TYPES = {"Weight", "Mass", "Weight/Mass"}

def normalise_gems_columns(names):
    return [str(name).strip().rstrip(":").strip() for name in names or []]

def is_gems_file(path):
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
            return GEMS_COLUMNS.issubset(normalise_gems_columns(next(csv.reader(stream), [])))
    except (OSError, UnicodeError):
        return False

def read_gems(path, baseline):
    source_hash = hashes(path)["sha256"]
    rows, quarantine = [], []
    exact, location_counts = Counter(), Counter()
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        normal = normalise_gems_columns(reader.fieldnames)
        if len(normal) != len(set(normal)) or not GEMS_COLUMNS.issubset(normal):
            raise ValueError("GEMS CSV has duplicate or missing required columns")
        for number, raw in enumerate(reader, 1):
            row = {k: str(raw.get(original, "")).strip() for original, k in zip(reader.fieldnames, normal)}
            source_id = "GEMS:" + source_hash[:16] + ":" + str(number)
            pid = row["project"]
            try:
                lon, lat = float(row["long"]), float(row["lat"])
            except ValueError:
                quarantine.append({"source_id": source_id, "source_row": number, "project_id": pid, "reason": "Non-numeric coordinate"})
                continue
            if not re.fullmatch(r"P\d{6}", pid) or not math.isfinite(lon) or not math.isfinite(lat) or abs(lon) > 180 or abs(lat) > 90:
                quarantine.append({"source_id": source_id, "source_row": number, "project_id": pid, "reason": "Invalid project ID or coordinate range"})
                continue
            signature = tuple(sorted(row.items()))
            exact[signature] += 1
            location_counts[(pid, lon, lat)] += 1
            rows.append({"source_id": source_id, "source_row": number, "project_id": pid, "lead_practice": row["lead GP"],
                         "source_country": row["country"], "longitude": lon, "latitude": lat,
                         "duplicate_occurrence": exact[signature], "project_coordinate_occurrence": location_counts[(pid, lon, lat)],
                         "location_date": None, "implementation_status": "not supplied", "coordinate_precision": "not supplied",
                         "location_review": "user-supplied undated location", "country_label_check": "not independently validated"})
    projects = []
    for pid, count in sorted(Counter(r["project_id"] for r in rows).items()):
        metadata = baseline["official_projects"].get(pid, {})
        matches = {code: data["project_index"].get(pid, []) for code, data in baseline["maps"].items()}
        focal = any(any(m["scope"] == "focal" for m in found) for found in matches.values())
        projects.append({"project_id": pid, "source_rows": count, "official_title": metadata.get("title", ""),
                         "metadata_verified": bool(metadata), "approval_date": metadata.get("approval_date", ""),
                         "closing_date": metadata.get("closing_date", ""), "project_status": "not inferred from dates",
                         "directory_relationship": "focal" if focal else "context_only" if any(matches.values()) else "absent",
                         "map_matches": matches, "official_source_url": metadata.get("source_url", ""),
                         "source_note": "Each project remains distinct; directory/family relationships are links, not deduplication keys."})
    audit = {"source_sha256": source_hash, "valid_rows": len(rows), "invalid_rows": len(quarantine), "project_ids": len(projects),
             "exact_duplicate_rows": sum(c - 1 for c in exact.values()), "duplicate_project_coordinates": sum(c - 1 for c in location_counts.values()),
             "country_rows": dict(Counter(r["source_country"] for r in rows)),
             "directory_relationships": dict(Counter(p["directory_relationship"] for p in projects)),
             "unverified_project_metadata": [p["project_id"] for p in projects if not p["metadata_verified"]]}
    return rows, projects, quarantine, audit

def finite_number(value):
    if value is None or not str(value).strip():
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None

def read_fews(folder, config):
    coverage, raw_receipts = Counter(), {}
    for point in config["fews_points"]:
        pid = str(point["id"])
        path = Path(folder) / ("fews_" + pid + "_daily.csv")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["border_point_id"] != pid:
                    raise ValueError("Wrong crossing ID in FEWS raw records")
                if row["collection_status"] == "Published" and row["data_usage_policy"] == "Public":
                    coverage[(pid, row["dataseries"], row["period_date"][:7])] += 1
        raw_receipts[pid] = hashes(path)["sha256"]
    monthly, audit, identity_counts = [], [], Counter()
    for point in config["fews_points"]:
        pid = str(point["id"])
        path = Path(folder) / ("fews_" + pid + "_monthly.csv")
        published, missing, excluded = 0, 0, 0
        periods = []
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for number, row in enumerate(csv.DictReader(stream), 1):
                if row["border_point_id"] != pid:
                    raise ValueError("Wrong crossing ID in FEWS monthly records")
                if row["collection_status"] != "Published":
                    excluded += 1
                    continue
                if row["data_usage_policy"] != "Public":
                    raise ValueError("FEWS record is not marked Public; eligibility must be reviewed")
                period = row["period_date"]
                date.fromisoformat(period[:10])
                date.fromisoformat(row["start_date"][:10])
                published += 1
                periods.append(period)
                value = finite_number(row["common_unit_quantity"])
                missing += int(value is None)
                unit = row["common_unit"].strip().lower()
                tonnes = value * MASS_TO_TONNES[unit] if value is not None and row["unit_type"] in MASS_TYPES and unit in MASS_TO_TONNES else None
                month = period[:7]
                key = (pid, row["dataseries"], row["reporting_country_code"], row["cpcv2"], row["flow_type"], row["trade_type"], month, unit)
                identity_counts[key] += 1
                item = dict(row)
                item.update(source_csv_row=number, physical_place=point["physical_place"], observation_identity="|".join(key),
                            common_quantity_available=value is not None, mass_tonnes=tonnes,
                            raw_published_records_in_month=coverage[(pid, row["dataseries"], month)],
                            coverage_note="Record count, not days monitored or a completeness percentage.",
                            coordinate_status="quarantined" if point.get("quarantine") else "audited catalogue reference",
                            duplicate_series_month=identity_counts[key] > 1)
                monthly.append(item)
        audit.append({"border_point_id": point["id"], "physical_place": point["physical_place"], "published_monthly_rows": published,
                      "excluded_nonpublished_rows": excluded, "missing_quantity_rows": missing, "first_published_period": min(periods, default=None),
                      "last_published_period": max(periods, default=None), "monthly_sha256": hashes(path)["sha256"],
                      "raw_sha256": raw_receipts[pid], "coordinate_quarantine": point.get("quarantine", "")})
    return monthly, {"points": audit, "duplicate_series_month_keys": ["|".join(k) for k, c in identity_counts.items() if c > 1],
                     "aggregation_policy": "No totals across distinct series/reporting sides; commodity mass and item counts remain separate."}

def physical_fews_places(config, observations):
    groups = {}
    for point in config["fews_points"]:
        if point.get("quarantine") or point.get("coordinates") is None:
            continue
        group = groups.setdefault(point["physical_place"], {"name": point["physical_place"], "coordinates": point["coordinates"],
                                                            "point_ids": [], "source_coordinates": []})
        group["point_ids"].append(point["id"])
        group["source_coordinates"].append({"point_id": point["id"], "country": point["country"], "coordinates": point["coordinates"]})
    for group in groups.values():
        selected = [o for o in observations if int(o["border_point_id"]) in group["point_ids"]]
        dates = [o["period_date"] for o in selected]
        group.update(published_series_rows=len(selected), first_period=min(dates, default=None), last_period=max(dates, default=None),
                     mass_series_rows=sum(o["mass_tonnes"] is not None for o in selected), item_series_rows=sum(o["unit_type"] == "Item" for o in selected),
                     location_method="FEWS catalogue representative; reporting-side coordinates retained separately")
    return list(groups.values())

