"""Optional independent GEOS tests for precision loss in boundary repair."""
import ast
import hashlib
import json
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    from shapely import make_valid, wkt
    from shapely.geometry import Polygon, MultiPolygon, shape, mapping
except ImportError:
    make_valid = None

def rounded(value, precision):
    if isinstance(value, dict):
        return {k: rounded(v, precision) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(v, precision) for v in value]
    return round(value, precision) if isinstance(value, float) else value

class GeometryAdapter:
    """Independent GEOS operations; does not validate QGIS bindings or GUI."""
    def __init__(self, value):
        self.value = value.value if isinstance(value, GeometryAdapter) else value
    @staticmethod
    def fromWkt(text):
        return GeometryAdapter(wkt.loads(text))
    def isNull(self):
        return self.value is None
    def isEmpty(self):
        return self.value.is_empty
    def isGeosValid(self):
        return self.value.is_valid
    def area(self):
        return self.value.area
    def makeValid(self):
        return GeometryAdapter(make_valid(self.value))
    def lastError(self):
        return ""
    def type(self):
        return 2 if self.value.geom_type in {"Polygon", "MultiPolygon"} else 4
    def convertGeometryCollectionToSubclass(self, wanted):
        if self.value.geom_type != "GeometryCollection":
            return False
        parts = []
        for part in self.value.geoms:
            if part.geom_type == "Polygon":
                parts.append(part)
            elif part.geom_type == "MultiPolygon":
                parts.extend(part.geoms)
        self.value = MultiPolygon(parts)
        return True
    def asJson(self, precision=17):
        return json.dumps(rounded(mapping(self.value), precision))
    def asWkt(self, precision=17):
        return wkt.dumps(self.value, rounding_precision=precision)

def load_repair(path):
    tree = ast.parse(Path(path).read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"components", "geometry", "valid_polygon"}]
    namespace = {"json": json, "hashlib": hashlib, "QgsGeometry": GeometryAdapter,
                 "Qgis": types.SimpleNamespace(GeometryType=types.SimpleNamespace(Polygon=2))}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["valid_polygon"]

@unittest.skipIf(make_valid is None, "Optional geometry regression tests require Shapely")
class PrecisionRepairTest(unittest.TestCase):
    def test_rounding_does_not_destroy_tiny_repaired_polygon(self):
        d = 2e-13
        original = Polygon([(39, 3), (40, 3), (40, 4), (39, 4), (39+d, 4+d), (39-d, 4+d), (39, 4), (39, 3)])
        native = make_valid(original)
        self.assertFalse(original.is_valid)
        self.assertTrue(native.is_valid)
        self.assertFalse(shape(rounded(mapping(native), 12)).is_valid)
        audit = []
        fixed = load_repair(ROOT / "stage2_spatial.py")(GeometryAdapter(original), "precision fixture", audit)
        self.assertTrue(fixed.isGeosValid())
        self.assertEqual(fixed.value.wkb, native.wkb)
        self.assertFalse(audit[0]["rounded_serialization_used_in_repair"])
        self.assertFalse(audit[0]["original_saved_over"])

    def test_repair_keeps_polygon_and_filters_collapsed_line_in_collection(self):
        original = Polygon([(0,0),(2,0),(2,2),(1,2),(1,3),(1,2),(0,2),(0,0)])
        native = make_valid(original)
        self.assertEqual(native.geom_type, "GeometryCollection")
        audit = []
        fixed = load_repair(ROOT / "stage2_spatial.py")(GeometryAdapter(original), "collapsed spike", audit)
        self.assertTrue(fixed.isGeosValid())
        self.assertEqual(fixed.type(), 2)
        self.assertAlmostEqual(fixed.area(), 4.0)
        self.assertTrue(original.equals(Polygon([(0,0),(2,0),(2,2),(1,2),(1,3),(1,2),(0,2),(0,0)])))

