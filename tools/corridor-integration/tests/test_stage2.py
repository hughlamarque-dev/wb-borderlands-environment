import base64
import csv
import gzip
import hashlib
import io
import json
import re
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import stage2_graph as graph
import stage2_tabular as tabular
from stage2_runtime import unpack_wheel

def way(n, nodes, coords, highway="primary", **tags):
    return {"id": n, "refs": nodes, "coords": coords, "tags": {"highway": highway, **tags}}

class GraphTest(unittest.TestCase):
    def test_crossing_lines_without_shared_nodes_are_not_junctions(self):
        built = graph.build_graph([way(1, [1, 2], [(-1, 0), (1, 0)]), way(2, [3, 4], [(0, -1), (0, 1)], "residential")], lambda x,y:(x,y))
        self.assertEqual(built["junctions"], [])
    def test_true_t_junction_counted_once_across_duplicate_extracts(self):
        ways = [way(1,[1,2,3],[(-1,0),(0,0),(1,0)]),way(2,[2,4],[(0,0),(0,1)],"residential")]
        built = graph.build_graph(ways + ways, lambda x,y:(x,y))
        self.assertEqual(built["junctions"], [2])
        self.assertEqual(len(built["edges"]), 3)
    def test_degree_two_class_change_not_counted(self):
        built = graph.build_graph([way(1,[1,2],[(0,0),(1,0)]),way(2,[2,3],[(1,0),(2,0)],"tertiary")],lambda x,y:(x,y))
        self.assertEqual(built["junctions"], [])
    def test_private_feeder_excluded(self):
        built=graph.build_graph([way(1,[1,2,3],[(-1,0),(0,0),(1,0)]),way(2,[2,4],[(0,0),(0,1)],"service",access="private")],lambda x,y:(x,y))
        self.assertEqual(built["junctions"], [])
    def test_specific_motor_permission_overrides_generic_access(self):
        self.assertTrue(graph.motor_access({"access":"private","motor_vehicle":"yes"})[0])
        self.assertFalse(graph.motor_access({"access":"yes","motor_vehicle":"no"})[0])
    def test_conditional_access_not_assumed_open(self):
        self.assertFalse(graph.motor_access({"access":"yes","access:conditional":"no @ (Mo-Fr)"})[0])
    def test_oneway_reach_respects_forward_approach_to_place(self):
        built=graph.build_graph([way(1,[1,2],[(0,0),(1000,0)],"residential",oneway="yes")],lambda x,y:(x,y))
        seeds=graph.settlement_seeds("town",built["local_edges"][0],20,0.5)
        distances,_=graph.nearest_places(built["reverse_local"],seeds)
        self.assertEqual(distances[1],520)
        self.assertNotIn(2,distances)
    def test_distance_thresholds_and_snap_cost(self):
        distances,_=graph.nearest_places({3:[(2,1900)],2:[(1,300)]},[(3,100,"town")],maximum_m=2000)
        self.assertEqual(distances[2],2000)
        self.assertNotIn(1,distances)
    def test_major_edges_not_used_as_local_route(self):
        built=graph.build_graph([way(1,[1,2],[(0,0),(1000,0)]),way(2,[2,3],[(1000,0),(1100,0)],"residential")],lambda x,y:(x,y))
        distances,_=graph.nearest_places(built["reverse_local"],[(3,0,"town")])
        self.assertIn(2,distances)
        self.assertNotIn(1,distances)
    def test_seeds_outside_limit_not_returned(self):
        self.assertEqual(graph.nearest_places({},[(1,11000,"town")]),({},{}))
    def test_same_osm_node_with_conflicting_location_rejected(self):
        with self.assertRaisesRegex(ValueError,"Conflicting OSM node"):
            graph.build_graph([way(1,[1,2],[(0,0),(1,0)]),way(2,[2,3],[(2,0),(3,0)],"residential")],lambda x,y:(x,y))
    def test_same_version_way_conflict_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            db=graph.create_store(Path(folder)/"test.sqlite")
            try:
                graph.store_way(db,1,1,{"highway":"primary"},[1,2],[[0,0],[1,0]])
                self.assertFalse(graph.store_way(db,1,1,{"highway":"primary"},[1,2],[[0,0],[1,0]]))
                with self.assertRaisesRegex(ValueError,"Conflicting same-version"):
                    graph.store_way(db,1,1,{"highway":"primary"},[1,2],[[0,0],[2,0]])
            finally: db.close()

class TabularTest(unittest.TestCase):
    def test_gems_distinct_projects_at_same_point_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"gems.csv"
            p.write_text("lead GP:,project:,country:,long:,lat:\nAgriculture,P123456,Kenya,39,3\nAgriculture,P654321,Kenya,39,3\nAgriculture,P123456,Kenya,39,3\nBad,P123456,Kenya,999,3\n")
            rows,projects,quarantine,audit=tabular.read_gems(p,{"official_projects":{},"maps":{}})
        self.assertEqual(len(rows),3)
        self.assertEqual(len(projects),2)
        self.assertEqual(len(quarantine),1)
        self.assertEqual(audit["exact_duplicate_rows"],1)
        self.assertEqual(rows[1]["duplicate_occurrence"],1)
        self.assertEqual(rows[2]["duplicate_occurrence"],2)
    def test_zero_preserved_and_missing_not_zero(self):
        self.assertEqual(tabular.finite_number("0"),0)
        self.assertIsNone(tabular.finite_number(""))
        self.assertIsNone(tabular.finite_number("NaN"))
    def test_real_fews_weight_label_converts_mass_and_no_data_zero_is_excluded(self):
        fields=["border_point_id","dataseries","collection_status","data_usage_policy","period_date","start_date","common_unit_quantity",
                "common_unit","unit_type","reporting_country_code","cpcv2","flow_type","trade_type"]
        base=dict(border_point_id="1",dataseries="10",collection_status="Published",data_usage_policy="Public",period_date="2024-01-31",
                  start_date="2024-01-01",common_unit_quantity="1000",common_unit="kg",unit_type="Weight",reporting_country_code="SS",
                  cpcv2="Maize",flow_type="Import",trade_type="Informal")
        with tempfile.TemporaryDirectory() as folder:
            for name in ("fews_1_monthly.csv","fews_1_daily.csv"):
                with (Path(folder)/name).open("w",newline="") as stream:
                    writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
                    writer.writerows([base,dict(base,collection_status="No Data",common_unit_quantity="0",period_date="2024-02-29")])
            rows,audit=tabular.read_fews(folder,{"fews_points":[{"id":1,"physical_place":"Crossing"}]})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["mass_tonnes"],1.0)
        self.assertEqual(rows[0]["raw_published_records_in_month"],1)
    def test_report_sides_and_livestock_one_physical_place(self):
        c={"fews_points":[{"id":1,"physical_place":"Crossing","country":"A","coordinates":[1,2]},
                          {"id":2,"physical_place":"Crossing","country":"B","coordinates":[1.001,2]},
                          {"id":3,"physical_place":"Bad","quarantine":"review","coordinates":None}]}
        rows=[{"border_point_id":"1","period_date":"2024-01-31","mass_tonnes":None,"unit_type":"Item"},
              {"border_point_id":"2","period_date":"2024-02-29","mass_tonnes":0.0,"unit_type":"Weight"}]
        places=tabular.physical_fews_places(c,rows)
        self.assertEqual(len(places),1)
        self.assertEqual(places[0]["point_ids"],[1,2])
        self.assertEqual(places[0]["mass_series_rows"],1)
        self.assertEqual(places[0]["item_series_rows"],1)

class Stage2BundleTest(unittest.TestCase):
    def test_baseline_integrity_and_exact_published_cell_ids(self):
        c=json.loads((ROOT/"stage2_config.json").read_text())
        raw=(ROOT/"baselines"/(c["baseline_sha256"]+".json.gz")).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),c["baseline_sha256"])
        b=json.loads(gzip.decompress(raw))
        self.assertEqual(sum(len(m["hexes"]["features"]) for m in b["maps"].values()),9500)
        for m in b["maps"].values():
            self.assertEqual(len(m["hexes"]["features"]),len({f["properties"]["cell_id"] for f in m["hexes"]["features"]}))
            self.assertTrue(all(set(f["properties"])=={"cell_id"} for f in m["hexes"]["features"]))
        self.assertNotIn("public_key",b)
    def test_dependency_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"bad.whl"
            with zipfile.ZipFile(p,"w") as z:z.writestr("../escape.py","bad")
            with self.assertRaises(ValueError):unpack_wheel(p,Path(folder)/"unpack")
            self.assertFalse((Path(folder)/"escape.py").exists())
    def test_embedded_stage2_bootstrap_without_file_global(self):
        from build_stage2_launcher import FILES
        s=(ROOT/"START_WB_STAGE2.cmd").read_bytes().decode()
        match=re.search(r"\$pythonPayload = @'\r?\n(.*?)\r?\n'@",s,re.S)
        bootstrap=base64.b64decode(match.group(1)).decode()
        fake=types.ModuleType("qgis_stage2");calls=[];fake.start=lambda:calls.append("started")
        old_path=list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as folder:
                with patch.dict("os.environ",{"WB_STAGE2_RUNTIME":folder}),patch.dict(sys.modules,{"qgis_stage2":fake}):
                    exec(compile(bootstrap,"qgis --code","exec"),{})
                for name in FILES:self.assertEqual((Path(folder)/name).read_bytes(),(ROOT/name).read_bytes())
        finally:sys.path[:]=old_path
        self.assertEqual(calls,["started"])

