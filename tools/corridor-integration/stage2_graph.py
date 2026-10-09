"""Shared-node OSM topology and directed local-road access; no QGIS imports."""
import heapq
import json
import math
import sqlite3
import uuid
from collections import defaultdict
from pathlib import Path
from download_inputs import check_cancel

MAJOR = {"motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"}
LOCAL = {"secondary", "tertiary", "secondary_link", "tertiary_link", "residential", "unclassified", "service", "living_street", "track"}
PLACES = {"city", "town", "village"}
ROAD_TAGS = {"highway", "name", "ref", "bridge", "tunnel", "layer", "junction", "area", "oneway", "oneway:motor_vehicle", "oneway:motorcar",
             "access", "vehicle", "motor_vehicle", "motorcar", "access:conditional", "vehicle:conditional", "motor_vehicle:conditional", "motorcar:conditional",
             "oneway:conditional", "oneway:motor_vehicle:conditional", "oneway:motorcar:conditional", "surface"}
BLOCKED_ACCESS = {"no", "private", "agricultural", "forestry", "delivery", "customers", "permit"}
BLOCKED_BARRIERS = {"bollard", "block", "bus_trap", "cycle_barrier", "jersey_barrier", "sump_buster", "stile", "turnstile", "wall", "fence"}

def motor_access(tags):
    if tags.get("area") == "yes":
        return False, "road_area_not_a_linear_route"
    if any(tags.get(k + ":conditional") for k in ("access", "vehicle", "motor_vehicle", "motorcar")):
        return False, "conditional_motor_access"
    for key in ("motorcar", "motor_vehicle", "vehicle", "access"):
        if tags.get(key):
            value = tags[key].lower()
            if value in BLOCKED_ACCESS:
                return False, key + "=" + value
            if value in {"yes", "permissive", "designated", "destination"}:
                return True, ""
            return False, "unresolved_" + key + "=" + value
    return True, ""

def allowed_directions(tags):
    allowed, reason = motor_access(tags)
    if not allowed:
        return set(), reason
    if any(tags.get(k) for k in ("oneway:conditional", "oneway:motor_vehicle:conditional", "oneway:motorcar:conditional")):
        return set(), "conditional_direction"
    value = tags.get("oneway:motorcar", tags.get("oneway:motor_vehicle", tags.get("oneway", "yes" if tags.get("junction") == "roundabout" else "no"))).lower()
    if value in {"yes", "1", "true"}:
        return {1}, ""
    if value == "-1":
        return {-1}, ""
    if value in {"no", "0", "false"}:
        return {1, -1}, ""
    return set(), "unresolved_direction=" + value

def barrier_blocks(tags):
    # A specific motor permission can override a generic restricted access tag.
    if not motor_access(tags)[0]:
        return True
    return tags.get("barrier") in BLOCKED_BARRIERS and not any(tags.get(k) in {"yes", "permissive", "designated"} for k in ("motorcar", "motor_vehicle"))

def bbox_overlap(a, b):
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]

def padded_bounds(bounds, halo_km=15):
    south, west = bounds[0]
    north, east = bounds[1]
    lat_pad = halo_km / 110.0
    lon_pad = lat_pad / max(0.1, math.cos(math.radians(max(abs(south), abs(north)) + lat_pad)))
    return [west - lon_pad, south - lat_pad, east + lon_pad, north + lat_pad]

def create_store(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS ways(id INTEGER PRIMARY KEY, version INTEGER, tags TEXT, refs TEXT, coords TEXT,
                                        minlon REAL, minlat REAL, maxlon REAL, maxlat REAL);
        CREATE TABLE IF NOT EXISTS places(id INTEGER PRIMARY KEY, version INTEGER, lon REAL, lat REAL, name TEXT, kind TEXT);
        CREATE TABLE IF NOT EXISTS barriers(id INTEGER PRIMARY KEY, version INTEGER, lon REAL, lat REAL, tags TEXT);
    """)
    return connection

def store_way(connection, way_id, version, tags, refs, coords):
    serial = (json.dumps(tags, sort_keys=True, separators=(",", ":")), json.dumps(refs), json.dumps(coords))
    previous = connection.execute("SELECT version,tags,refs,coords FROM ways WHERE id=?", (way_id,)).fetchone()
    if previous:
        if previous[0] == version and tuple(previous[1:]) != serial:
            raise ValueError("Conflicting same-version OSM way across extracts: " + str(way_id))
        if previous[0] >= version:
            return False
    x, y = zip(*coords)
    connection.execute("INSERT OR REPLACE INTO ways VALUES (?,?,?,?,?,?,?,?,?)", (way_id, version, *serial, min(x), min(y), max(x), max(y)))
    return True

def parse_extracts(osmium, entries, input_folder, database_path, boxes, emit, cancel):
    connection = create_store(database_path)
    stats = {"retained_way_updates": 0, "retained_places": 0, "retained_barriers": 0, "countries": [], "retained_node_cache_files": []}
    class Handler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.operations = 0
        def checkpoint(self):
            self.operations += 1
            if self.operations % 5000 == 0:
                check_cancel(cancel)
                connection.commit()
                emit("Reading OSM topology: " + str(stats["retained_way_updates"]) + " retained way updates", 0)
        def node(self, node):
            self.checkpoint()
            tags = {t.k: t.v for t in node.tags}
            kind = tags.get("place")
            is_barrier = bool(tags.get("barrier")) or barrier_blocks(tags)
            if kind not in PLACES and not is_barrier:
                return
            point = (node.location.lon, node.location.lat)
            if not any(bbox_overlap((*point, *point), box) for box in boxes):
                return
            for table, current in (("places", (node.version, *point, tags.get("name", ""), kind)) if kind in PLACES else ("", ()),
                                   ("barriers", (node.version, *point, json.dumps(tags, sort_keys=True))) if is_barrier else ("", ())):
                if not table:
                    continue
                columns = "version,lon,lat,name,kind" if table == "places" else "version,lon,lat,tags"
                old = connection.execute("SELECT " + columns + " FROM " + table + " WHERE id=?", (node.id,)).fetchone()
                if old and old[0] == node.version and old != current:
                    raise ValueError("Conflicting same-version OSM " + table + ": " + str(node.id))
                if not old or old[0] < node.version:
                    connection.execute("INSERT OR REPLACE INTO " + table + " VALUES (" + ",".join("?" for _ in range(len(current) + 1)) + ")", (node.id, *current))
                    stats["retained_" + table] += 1
        def way(self, way):
            self.checkpoint()
            if way.tags.get("highway") not in MAJOR | LOCAL:
                return
            tags = {t.k: t.v for t in way.tags if t.k in ROAD_TAGS}
            refs = [int(n.ref) for n in way.nodes]
            if len(refs) < 2:
                return
            coords = [[n.lon, n.lat] for n in way.nodes]
            x, y = zip(*coords)
            if any(bbox_overlap((min(x), min(y), max(x), max(y)), box) for box in boxes):
                stats["retained_way_updates"] += int(store_way(connection, int(way.id), int(way.version), tags, refs, coords))
    try:
        for entry in entries:
            check_cancel(cancel)
            emit("Reading " + entry["country"] + " PBF (original shared node IDs)", 0)
            handler = Handler()
            cache = Path(database_path).with_name("nodes_" + uuid.uuid4().hex + ".cache")
            index_type = "sparse_file_array," + str(cache) if "sparse_file_array" in osmium.index.map_types() else "flex_mem"
            # All node locations are populated before the tag filters are applied.
            handler.apply_file(str(Path(input_folder) / entry["filename"]), locations=True, idx=index_type,
                               filters=[osmium.filter.KeyFilter("highway", "place", "barrier", "motor_vehicle", "motorcar", "access")])
            connection.commit()
            stats["countries"].append({"country": entry["country"], "index_type": index_type.split(",", 1)[0]})
            del handler
            if cache.exists():
                try:
                    cache.unlink()
                except PermissionError:
                    stats["retained_node_cache_files"].append(str(cache))
        for table in ("ways", "places", "barriers"):
            stats["unique_" + table] = connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return stats
    finally:
        connection.close()

def build_graph(ways, project_xy, blocked_nodes=()):
    coords, xy, edges = {}, {}, {}
    exclusions = defaultdict(int)
    blocked = set(blocked_nodes)
    major_ways = []
    for way in ways:
        refs, locations, tags = way["refs"], way["coords"], way["tags"]
        is_major = tags["highway"] in MAJOR
        directions, reason = allowed_directions(tags)
        if is_major:
            major_ways.append(way)
        if not directions:
            exclusions[reason] += 1
        for node, coordinate in zip(refs, locations):
            point = tuple(coordinate)
            if node in coords and coords[node] != point:
                raise ValueError("Conflicting OSM node coordinates: " + str(node))
            coords[node] = point
            if node not in xy:
                xy[node] = project_xy(*point)
        for u, v in zip(refs, refs[1:]):
            if u == v:
                continue
            key = tuple(sorted((u, v)))
            edge = edges.setdefault(key, {"u": key[0], "v": key[1], "major": False, "directions": set(), "way_ids": set(), "highways": set()})
            edge["major"] |= is_major
            edge["way_ids"].add(way["id"])
            edge["highways"].add(tags["highway"])
            if u not in blocked and v not in blocked:
                if 1 in directions:
                    edge["directions"].add((u, v))
                if -1 in directions:
                    edge["directions"].add((v, u))
    adjacency = defaultdict(set)
    major_nodes, local_nodes = set(), set()
    reverse_local = defaultdict(list)
    local_edges = []
    for edge in edges.values():
        u, v = edge["u"], edge["v"]
        edge["length_m"] = math.dist(xy[u], xy[v])
        if edge["length_m"] <= 0:
            continue
        if edge["directions"]:
            adjacency[u].add(v)
            adjacency[v].add(u)
            (major_nodes if edge["major"] else local_nodes).update((u, v))
        if not edge["major"] and edge["directions"]:
            local_edges.append(edge)
            for origin, destination in edge["directions"]:
                reverse_local[destination].append((origin, edge["length_m"]))
    junctions = sorted(n for n in major_nodes & local_nodes if len(adjacency[n]) >= 3)
    return {"coords": coords, "xy": xy, "edges": edges, "junctions": junctions, "reverse_local": reverse_local,
            "local_edges": local_edges, "major_ways": major_ways, "exclusions": dict(exclusions), "blocked_nodes": len(blocked)}

def snap_to_segment(point, first, last):
    dx, dy = last[0] - first[0], last[1] - first[1]
    denom = dx * dx + dy * dy
    fraction = max(0.0, min(1.0, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / denom)) if denom else 0.0
    return math.dist(point, (first[0] + fraction * dx, first[1] + fraction * dy)), fraction

def settlement_seeds(place, edge, snap_m, fraction):
    u, v = edge["u"], edge["v"]
    result = []
    if (u, v) in edge["directions"]:
        result.append((u, snap_m + fraction * edge["length_m"], place))
    if (v, u) in edge["directions"]:
        result.append((v, snap_m + (1.0 - fraction) * edge["length_m"], place))
    return result

def nearest_places(reverse_graph, seeds, maximum_m=10000.0):
    distances, targets, heap = {}, {}, []
    for node, distance, place in seeds:
        if distance <= maximum_m and distance < distances.get(node, math.inf):
            distances[node], targets[node] = distance, str(place)
            heapq.heappush(heap, (distance, node, str(place)))
    while heap:
        distance, node, place = heapq.heappop(heap)
        if distance != distances[node] or place != targets[node]:
            continue
        for origin, length in reverse_graph.get(node, ()):
            candidate = distance + length
            if candidate <= maximum_m and candidate < distances.get(origin, math.inf):
                distances[origin], targets[origin] = candidate, place
                heapq.heappush(heap, (candidate, origin, place))
    return distances, targets

