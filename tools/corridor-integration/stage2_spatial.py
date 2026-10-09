"""Geometry processing on worker-owned QGIS objects; original GUI layers untouched."""
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCoordinateTransformContext,
                       QgsFeature, QgsGeometry, QgsPointXY, QgsRectangle, QgsSpatialIndex)
from download_inputs import check_cancel, save_json
from stage2_graph import barrier_blocks, build_graph, nearest_places, padded_bounds, settlement_seeds, snap_to_segment

def feature(geom, properties):
    return {"type": "Feature", "geometry": geom, "properties": properties}

def collection(features):
    return {"type": "FeatureCollection", "features": features}

def components(value, kind):
    if value["type"] in {kind, "Multi" + kind}:
        return value
    if value["type"] == "GeometryCollection":
        values = []
        for part in value["geometries"]:
            selected = components(part, kind)
            if selected:
                values.extend([selected["coordinates"]] if selected["type"] == kind else selected["coordinates"])
        return {"type": "Multi" + kind, "coordinates": values} if values else None
    return None

def geometry(source):
    def ring(points):
        return "(" + ",".join(repr(float(p[0])) + " " + repr(float(p[1])) for p in points) + ")"
    kind, values = source["type"], source["coordinates"]
    if kind == "Polygon":
        wkt = "POLYGON(" + ",".join(ring(r) for r in values) + ")"
    elif kind == "MultiPolygon":
        wkt = "MULTIPOLYGON(" + ",".join("(" + ",".join(ring(r) for r in p) + ")" for p in values) + ")"
    elif kind == "LineString":
        wkt = "LINESTRING" + ring(values)
    elif kind == "Point":
        return QgsGeometry.fromPointXY(QgsPointXY(*values[:2]))
    else:
        raise ValueError("Unsupported source geometry: " + kind)
    result = QgsGeometry.fromWkt(wkt)
    if result.isNull() or result.isEmpty():
        raise ValueError("Invalid or empty source geometry")
    return result

def valid_polygon(value, label, audit):
    if value.isNull() or value.isEmpty():
        raise ValueError("Empty polygon: " + label)
    if value.isGeosValid():
        return value
    original_area = value.area()
    repaired = value.makeValid()
    if repaired.isNull() or repaired.isEmpty():
        raise ValueError("Geometry repair failed: " + label + ": " + repaired.lastError())
    selected = components(json.loads(repaired.asJson(12)), "Polygon")
    if selected is None:
        raise ValueError("Geometry repair retained no polygon: " + label)
    result = geometry(selected)
    if not result.isGeosValid():
        raise ValueError("Geometry still invalid after repair: " + label)
    delta = abs(result.area() - original_area) / max(abs(original_area), 1e-12)
    if delta > 0.001:
        raise ValueError("Geometry repair changes area by more than 0.1%: " + label)
    audit.append({"geometry": label, "method": "GEOS makeValid linework; polygon components only",
                  "relative_area_change": delta, "source_sha256": hashlib.sha256(value.asWkt(12).encode()).hexdigest(),
                  "derived_sha256": hashlib.sha256(result.asWkt(12).encode()).hexdigest(), "original_saved_over": False})
    return result

class MapSpace:
    def __init__(self, baseline):
        south, west = baseline["bounds"][0]
        north, east = baseline["bounds"][1]
        self.proj = "+proj=aeqd +lat_0=" + str((south + north) / 2) + " +lon_0=" + str((west + east) / 2) + " +datum=WGS84 +units=m +no_defs"
        metric = QgsCoordinateReferenceSystem()
        if not metric.createFromProj(self.proj):
            raise ValueError("Could not create metric CRS")
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        self.forward = QgsCoordinateTransform(wgs, metric, QgsCoordinateTransformContext())
        self.backward = QgsCoordinateTransform(metric, wgs, QgsCoordinateTransformContext())
        self.geometry_audit = []
        parts = [valid_polygon(geometry(f["geometry"]), "published boundary part " + str(i), self.geometry_audit)
                 for i, f in enumerate(baseline["cluster"]["features"])]
        self.cluster_wgs = valid_polygon(QgsGeometry.unaryUnion(parts), "published boundary union", self.geometry_audit)
        self.cluster = valid_polygon(self.project_geometry(self.cluster_wgs), "projected boundary", self.geometry_audit)
        self.hex_index, self.hexes, self.hex_source = QgsSpatialIndex(), {}, {}
        self.repaired_cells = set()
        for source in baseline["hexes"]["features"]:
            cell = int(source["properties"]["cell_id"])
            if cell in self.hexes:
                raise ValueError("Duplicate published cell ID")
            before = len(self.geometry_audit)
            wgs_geom = valid_polygon(geometry(source["geometry"]), "published cell " + str(cell), self.geometry_audit)
            projected = valid_polygon(self.project_geometry(wgs_geom), "projected cell " + str(cell), self.geometry_audit)
            f = QgsFeature()
            f.setId(cell)
            f.setGeometry(projected)
            self.hex_index.addFeature(f)
            self.hexes[cell] = projected
            if len(self.geometry_audit) > before:
                back = QgsGeometry(projected)
                back.transform(self.backward)
                self.hex_source[cell] = json.loads(back.asJson(12))
                self.repaired_cells.add(cell)
            else:
                self.hex_source[cell] = source["geometry"]
        discrepancy = QgsGeometry.unaryUnion(list(self.hexes.values())).symDifference(self.cluster)
        if discrepancy.isNull():
            raise ValueError("Could not compare grid and boundary")
        self.grid_difference_ratio = discrepancy.area() / max(1.0, self.cluster.area())
        if self.grid_difference_ratio > 0.001:
            raise ValueError("Published grid/boundary differ by more than 0.1% of area")

    def xy(self, lon, lat):
        point = self.forward.transform(QgsPointXY(lon, lat))
        return point.x(), point.y()

    def project_geometry(self, value):
        result = QgsGeometry(value)
        result.transform(self.forward)
        return result

    def point(self, lon, lat):
        return QgsGeometry.fromPointXY(QgsPointXY(*self.xy(lon, lat)))

    def inside(self, lon, lat):
        return self.cluster.intersects(self.point(lon, lat))

    def cell_for(self, xy):
        point = QgsGeometry.fromPointXY(QgsPointXY(*xy))
        selected = [c for c in sorted(self.hex_index.intersects(point.boundingBox())) if self.hexes[c].intersects(point)]
        return selected[0] if selected else None

def edge_index(edges, xy):
    index = QgsSpatialIndex(flags=QgsSpatialIndex.FlagStoreFeatureGeometries)
    lookup = {}
    for number, edge in enumerate(edges, 1):
        if edge["length_m"] <= 0:
            continue
        f = QgsFeature()
        f.setId(number)
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*xy[edge["u"]]), QgsPointXY(*xy[edge["v"]])]))
        index.addFeature(f)
        lookup[number] = edge
    return index, lookup

def nearest_edge(point, index, lookup, xy, maximum=None):
    if not lookup:
        return None
    candidates = index.nearestNeighbor(QgsPointXY(*point), 1) if maximum is None else index.intersects(
        QgsRectangle(point[0] - maximum, point[1] - maximum, point[0] + maximum, point[1] + maximum))
    found = []
    for key in candidates:
        edge = lookup[key]
        distance, fraction = snap_to_segment(point, xy[edge["u"]], xy[edge["v"]])
        if maximum is None or distance <= maximum:
            found.append((distance, key, fraction, edge))
    return min(found, key=lambda v: (v[0], v[1])) if found else None

def load_graph(connection, box, space):
    west, south, east, north = box
    ways = [{"id": r[0], "tags": json.loads(r[1]), "refs": json.loads(r[2]), "coords": json.loads(r[3])}
            for r in connection.execute("SELECT id,tags,refs,coords FROM ways WHERE minlon<=? AND maxlon>=? AND minlat<=? AND maxlat>=?", (east, west, north, south))]
    blocked = {r[0] for r in connection.execute("SELECT id,tags FROM barriers") if barrier_blocks(json.loads(r[1]))}
    graph = build_graph(ways, space.xy, blocked)
    places = [{"id": str(r[0]), "lon": r[1], "lat": r[2], "name": r[3], "kind": r[4]}
              for r in connection.execute("SELECT id,lon,lat,name,kind FROM places WHERE lon>=? AND lon<=? AND lat>=? AND lat<=?", (west, east, south, north))]
    return graph, places

def compute_map(code, data, database, gems, projects, fews_places, config, output, emit, cancel, local_boundary=None):
    emit("Preparing " + data["title"] + " in a local metric CRS", 0)
    space = MapSpace(data)
    boundary_difference = None
    if local_boundary:
        parts = [valid_polygon(geometry(f["geometry"]), "local boundary part " + str(i), space.geometry_audit)
                 for i, f in enumerate(local_boundary["features"])]
        local = valid_polygon(space.project_geometry(QgsGeometry.unaryUnion(parts)), "projected local boundary", space.geometry_audit)
        difference = local.symDifference(space.cluster)
        if difference.isNull():
            raise ValueError("Could not compare current/published boundaries")
        boundary_difference = difference.area() / max(1.0, space.cluster.area())
        if boundary_difference > 0.001:
            raise ValueError("Current QGIS/published boundaries differ by more than 0.1%: " + code)
    with sqlite3.connect(database) as connection:
        graph, places = load_graph(connection, padded_bounds(data["bounds"], config["network_halo_km"]), space)
    check_cancel(cancel)
    emit("Finding off-major-road paths for " + code, 0)
    local_index, local_lookup = edge_index(graph["local_edges"], graph["xy"])
    all_seeds, urban_seeds, place_output = [], [], []
    for place in places:
        snap = nearest_edge(space.xy(place["lon"], place["lat"]), local_index, local_lookup, graph["xy"], config["maximum_place_snap_m"])
        if snap:
            distance, unused, fraction, edge = snap
            seeds = settlement_seeds(place["id"], edge, distance, fraction)
            all_seeds.extend(seeds)
            if place["kind"] in {"city", "town"}:
                urban_seeds.extend(seeds)
        else:
            distance = None
        if space.inside(place["lon"], place["lat"]):
            place_output.append(feature({"type": "Point", "coordinates": [place["lon"], place["lat"]]},
                                        {"osm_node_id": place["id"], "name": place["name"], "place_tag": place["kind"],
                                         "local_road_snap_m": distance, "snap_available": int(snap is not None),
                                         "settlement_definition": "OSM city/town/village node; not a built-up footprint"}))
    all_distance, all_target = nearest_places(graph["reverse_local"], all_seeds)
    urban_distance, urban_target = nearest_places(graph["reverse_local"], urban_seeds)
    del local_index, local_lookup
    thresholds = config["distance_thresholds_m"]
    fields = [prefix + "_" + str(t // 1000) + "km" for prefix in ("urban", "place") for t in thresholds]
    counts = {cell: {"major_road_m": 0.0, "junction_nodes": 0, **{f: 0 for f in fields}} for cell in space.hexes}
    node_output, unassigned = [], []
    for node in graph["junctions"]:
        lon, lat = graph["coords"][node]
        if not space.inside(lon, lat):
            continue
        cell = space.cell_for(graph["xy"][node])
        if cell is None:
            unassigned.append(str(node))
        props = {"osm_node_id": str(node), "cell_id": cell, "nearest_town_node": urban_target.get(node),
                 "nearest_place_node": all_target.get(node), "junction_measure": "shared OSM node, not merged functional interchange"}
        for field, distances in (("town_path_km", urban_distance), ("place_path_km", all_distance)):
            props[field] = distances[node] / 1000 if node in distances else None
        if cell is not None:
            counts[cell]["junction_nodes"] += 1
        for threshold in thresholds:
            for prefix, distances in (("urban", urban_distance), ("place", all_distance)):
                field = prefix + "_" + str(threshold // 1000) + "km"
                value = int(distances.get(node, math.inf) <= threshold)
                props[field] = value
                if cell is not None:
                    counts[cell][field] += value
        node_output.append(feature({"type": "Point", "coordinates": [lon, lat]}, props))
    check_cancel(cancel)
    major_edges = [e for e in graph["edges"].values() if e["major"] and e["length_m"] > 0]
    major_index, major_lookup = edge_index(major_edges, graph["xy"])
    total_focus_length, outside_grid_length = 0.0, 0.0
    for number, edge in enumerate(major_edges):
        if number % 5000 == 0:
            check_cancel(cancel)
            emit("Assigning major-road lengths to " + code + " grid: " + str(number), 0)
        line = QgsGeometry.fromPolylineXY([QgsPointXY(*graph["xy"][edge["u"]]), QgsPointXY(*graph["xy"][edge["v"]])])
        remaining = line.intersection(space.cluster)
        if remaining.isEmpty():
            continue
        total_focus_length += remaining.length()
        for cell in sorted(space.hex_index.intersects(remaining.boundingBox())):
            if remaining.isEmpty():
                break
            piece = remaining.intersection(space.hexes[cell])
            counts[cell]["major_road_m"] += piece.length()
            if not piece.isEmpty():
                remaining = remaining.difference(space.hexes[cell])
        outside_grid_length += remaining.length()
    assigned = sum(c["major_road_m"] for c in counts.values())
    conservation_error = assigned + outside_grid_length - total_focus_length
    if abs(conservation_error) > max(2.0, total_focus_length * 0.00001):
        raise ValueError("Major-road lengths do not conserve across grid/boundary: " + code)
    hex_output = []
    for cell, raw in sorted(counts.items()):
        km, n = raw["major_road_m"] / 1000, raw["junction_nodes"]
        props = {"cell_id": cell, "major_road_km": km, "junction_nodes": n if km > 0 else None,
                 "junction_nodes_per_10km": 10 * n / km if km >= config["minimum_normalisation_km"] else None,
                 "small_length_denominator": int(0 < km < config["minimum_normalisation_km"]),
                 "metric_applicable": int(km > 0), "metric_version": config["metric_version"], "geometry_repaired": int(cell in space.repaired_cells)}
        for field in fields:
            props[field + "_nodes"] = raw[field] if km > 0 else None
            props[field + "_share"] = raw[field] / n if n else None
        hex_output.append(feature(space.hex_source[cell], props))
    major_output = []
    for way in graph["major_ways"]:
        clipped = space.project_geometry(geometry({"type": "LineString", "coordinates": way["coords"]})).intersection(space.cluster)
        if clipped.isEmpty() or clipped.length() <= 0:
            continue
        clipped.transform(space.backward)
        selected = components(json.loads(clipped.asJson(8)), "LineString")
        if selected:
            major_output.append(feature(selected, {"osm_way_id": str(way["id"]), "highway": way["tags"]["highway"],
                                        "name": way["tags"].get("name", ""), "ref": way["tags"].get("ref", ""),
                                        "osm_tags": json.dumps(way["tags"]), "snapshot": "2026-10-08"}))
    projects_by_id = {p["project_id"]: p for p in projects}
    gems_output, gems_review = [], []
    project_counts = defaultdict(lambda: {"source_rows": 0, "review_rows": 0, "unique_locations": set()})
    for row in gems:
        lon, lat = row["longitude"], row["latitude"]
        if not space.inside(lon, lat):
            continue
        props = {k: v for k, v in row.items() if k not in {"longitude", "latitude"}}
        pid = row["project_id"]
        p = projects_by_id[pid]
        matches = data["project_index"].get(pid, [])
        props.update(existing_map_relationship="focal" if any(m["scope"] == "focal" for m in matches) else "context_only" if matches else "absent",
                     official_title=p["official_title"], closing_date=p["closing_date"],
                     pre2020_closing_review=int(bool(p["closing_date"]) and p["closing_date"][:4].isdigit() and p["closing_date"][:4] < "2020"),
                     existing_directory_links=json.dumps(matches, ensure_ascii=False))
        nearest = nearest_edge(space.xy(lon, lat), major_index, major_lookup, graph["xy"])
        props["major_road_straight_distance_km"] = nearest[0] / 1000 if nearest else None
        reasons = [q["reason"] for q in config["gems_quarantines"] if q["project_id"] == pid and q["map"] == code]
        if not p["metadata_verified"]:
            reasons.append("Project metadata not independently matched")
        if row["duplicate_occurrence"] > 1:
            reasons.append("Duplicate original CSV row; retain provenance, exclude from point counts")
        props["coordinate_review"] = "; ".join(reasons) if reasons else "undated source location; no implementation claim"
        (gems_review if reasons else gems_output).append(feature({"type": "Point", "coordinates": [lon, lat]}, props))
        project_counts[pid]["source_rows"] += 1
        project_counts[pid]["review_rows"] += int(bool(reasons))
        project_counts[pid]["unique_locations"].add((lon, lat))
    fews_output = []
    for place in fews_places:
        lon, lat = place["coordinates"]
        if space.inside(lon, lat):
            props = {k: v for k, v in place.items() if k != "coordinates"}
            props["point_ids"] = json.dumps(props["point_ids"])
            props["source_coordinates"] = json.dumps(props["source_coordinates"])
            props["reference_date"] = config["fews_reference_date"]
            fews_output.append(feature({"type": "Point", "coordinates": place["coordinates"]}, props))
    outputs = {"connectivity_hexes": hex_output, "junction_nodes": node_output, "mapped_places": place_output, "major_roads": major_output,
               "gems_locations": gems_output, "gems_review": gems_review, "fews_places": fews_output}
    paths = {}
    for kind, records in outputs.items():
        path = Path(output) / (code + "_" + kind + ".geojson")
        save_json(path, collection(records))
        paths[kind] = str(path)
    return {"code": code, "title": data["title"], "metric_crs": space.proj, "hexes": len(hex_output), "junction_nodes": len(node_output),
            "major_road_km": total_focus_length / 1000, "major_road_km_assigned_to_grid": assigned / 1000,
            "road_length_outside_grid_m": outside_grid_length, "length_conservation_error_m": conservation_error,
            "junction_nodes_unassigned_to_grid": unassigned, "grid_boundary_difference_area_ratio": space.grid_difference_ratio,
            "geometry_repairs": space.geometry_audit, "current_boundary_difference_ratio": boundary_difference,
            "gems_source_rows_in_focus": len(gems_output) + len(gems_review), "gems_review_rows": len(gems_review),
            "fews_physical_places": len(fews_output), "place_nodes_in_halo": len(places), "place_nodes_in_focus": len(place_output),
            "urban_place_nodes_in_halo": sum(p["kind"] in {"city", "town"} for p in places),
            "source_place_nodes_without_local_snap_in_focus": sum(not f["properties"]["snap_available"] for f in place_output),
            "graph_access_exclusions_by_way": graph["exclusions"], "paths": paths,
            "gems_projects": [{"project_id": pid, "source_rows": v["source_rows"], "review_rows": v["review_rows"],
                               "unique_project_locations": len(v["unique_locations"])} for pid, v in sorted(project_counts.items())]}

