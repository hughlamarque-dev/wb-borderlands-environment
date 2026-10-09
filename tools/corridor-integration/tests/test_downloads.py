import ast
import base64
import csv
import hashlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import download_inputs as downloads


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}

    def getcode(self):
        return self.status

    def geturl(self):
        return "https://example.org/input"


def csv_bytes(point="143949", schedule="Monthly", value="0", extra_rows=()):
    headers = ["border_point_id", "collection_status", "start_date", "period_date", "dataseries",
               "source_organization", "unit_type", "unit_name", "value", "common_unit_quantity",
               "data_usage_policy", "collection_schedule"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    row = dict(zip(headers, [point, "Published", "2024-09-01", "2024-09-30", "123",
                             "FEWS NET", "Weight", "Kilogram", value, value, "Public", schedule]))
    writer.writerow(row)
    for overrides in extra_rows:
        writer.writerow(dict(row, **overrides))
    return buffer.getvalue().encode("utf-8-sig")


class DownloadsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cancel = threading.Event()
        self.emit = lambda text, percent: None
        self.body = b"\x00\x00\x00\x10OSMHeader\x00test fixture only"
        self.pbf = {"kind": "osm_pbf", "filename": "test.osm.pbf", "url": "https://example.org/input",
                    "bytes": len(self.body), "md5": hashlib.md5(self.body).hexdigest()}
        self.csv = {"kind": "fews_csv", "filename": "fews.csv", "url": "https://example.org/input",
                    "point_id": 143949, "schedule": "Monthly"}

    def tearDown(self):
        self.temporary.cleanup()

    def fetch(self, entry, opener):
        return downloads.fetch(entry, self.root, self.emit, self.cancel, opener=opener)

    def track_partial(self, entry, data):
        (self.root / (entry["filename"] + ".part")).write_bytes(data)
        downloads.save_json(self.root / (entry["filename"] + ".part.json"),
                            {"requested_url": entry["url"], "md5": entry.get("md5"), "bytes": entry.get("bytes")})

    def test_download_verified_pbf_and_receipt(self):
        receipt = self.fetch(self.pbf, lambda *args, **kwargs: Response(self.body))
        self.assertEqual(receipt["sha256"], hashlib.sha256(self.body).hexdigest())
        self.assertEqual((self.root / "test.osm.pbf").read_bytes(), self.body)
        self.assertFalse((self.root / "test.osm.pbf.part").exists())
        self.assertTrue((self.root / "test.osm.pbf.receipt.json").exists())

    def test_resume_pbf_checks_range_and_checksum(self):
        offset = 11
        self.track_partial(self.pbf, self.body[:offset])

        def opener(request, **kwargs):
            self.assertEqual(request.get_header("Range"), "bytes=11-")
            return Response(self.body[offset:], 206, {"Content-Range": "bytes 11-" + str(len(self.body) - 1) + "/" + str(len(self.body))})

        self.fetch(self.pbf, opener)
        self.assertEqual((self.root / "test.osm.pbf").read_bytes(), self.body)

    def test_ignored_range_restarts_owned_partial(self):
        self.track_partial(self.pbf, self.body[:11])
        self.fetch(self.pbf, lambda *args, **kwargs: Response(self.body))
        self.assertEqual((self.root / "test.osm.pbf").read_bytes(), self.body)

    def test_wrong_resume_range_is_rejected(self):
        self.track_partial(self.pbf, self.body[:11])
        with self.assertRaisesRegex(ValueError, "resume range"):
            self.fetch(self.pbf, lambda *args, **kwargs: Response(self.body[11:], 206, {"Content-Range": "bytes 10-30/31"}))
        self.assertEqual((self.root / "test.osm.pbf.part").read_bytes(), self.body[:11])

    def test_complete_partial_finishes_without_network(self):
        self.track_partial(self.pbf, self.body)
        self.fetch(self.pbf, lambda *args, **kwargs: self.fail("Network should not be called"))
        self.assertTrue((self.root / "test.osm.pbf").exists())

    def test_checksum_mismatch_never_promoted(self):
        corrupted = self.body[:-1] + b"X"
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.fetch(self.pbf, lambda *args, **kwargs: Response(corrupted))
        self.assertFalse((self.root / "test.osm.pbf").exists())
        self.assertEqual((self.root / "test.osm.pbf.part").read_bytes(), corrupted)

    def test_unknown_existing_input_preserved(self):
        path = self.root / "test.osm.pbf"
        path.write_bytes(b"user-owned content")
        with self.assertRaises(ValueError):
            self.fetch(self.pbf, lambda *args, **kwargs: self.fail("Network should not be called"))
        self.assertEqual(path.read_bytes(), b"user-owned content")

    def test_unknown_partial_preserved(self):
        path = self.root / "test.osm.pbf.part"
        path.write_bytes(b"unknown content")
        with self.assertRaisesRegex(ValueError, "Untracked partial"):
            self.fetch(self.pbf, lambda *args, **kwargs: self.fail("Network should not be called"))
        self.assertEqual(path.read_bytes(), b"unknown content")

    def test_cancel_stops_before_network(self):
        self.cancel.set()
        with self.assertRaises(downloads.Cancelled):
            self.fetch(self.pbf, lambda *args, **kwargs: self.fail("Network should not be called"))

    def test_access_denied_not_retried(self):
        calls = []

        def opener(request, **kwargs):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

        with self.assertRaisesRegex(RuntimeError, "access denied"):
            self.fetch(self.pbf, opener)
        self.assertEqual(len(calls), 1)

    def test_wrong_crossing_rejected(self):
        with self.assertRaisesRegex(ValueError, "wrong crossing"):
            self.fetch(self.csv, lambda *args, **kwargs: Response(csv_bytes(point="143953")))
        self.assertFalse((self.root / "fews.csv").exists())

    def test_html_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing columns"):
            self.fetch(self.csv, lambda *args, **kwargs: Response(b"<html>Access denied</html>"))

    def test_zero_and_missing_values_not_converted(self):
        body = csv_bytes(extra_rows=({"value": "", "common_unit_quantity": "", "collection_status": "NoData"},))
        receipt = self.fetch(self.csv, lambda *args, **kwargs: Response(body))
        self.assertEqual(receipt["rows"], 2)
        self.assertEqual(receipt["published_rows"], 1)
        self.assertEqual((self.root / "fews.csv").read_bytes(), body)

    def test_verified_csv_is_reused_and_tampering_rejected(self):
        body = csv_bytes()
        self.fetch(self.csv, lambda *args, **kwargs: Response(body))
        receipt = self.fetch(self.csv, lambda *args, **kwargs: self.fail("Network should not be called"))
        self.assertTrue(receipt["reused"])
        (self.root / "fews.csv").write_bytes(csv_bytes(value="7"))
        with self.assertRaisesRegex(ValueError, "differs from its download receipt"):
            self.fetch(self.csv, lambda *args, **kwargs: self.fail("Network should not be called"))

    def test_mutable_csv_partial_restarts(self):
        self.track_partial(self.csv, b"interrupted old response")

        def opener(request, **kwargs):
            self.assertIsNone(request.get_header("Range"))
            return Response(csv_bytes())

        self.fetch(self.csv, opener)
        self.assertEqual((self.root / "fews.csv").read_bytes(), csv_bytes())

    def test_path_traversal_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsafe input filename"):
            self.fetch(dict(self.pbf, filename="../unrelated.pbf"), lambda *args, **kwargs: self.fail())

    def test_lock_blocks_concurrent_download(self):
        with downloads.download_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "Another Stage 1"):
                with downloads.download_lock(self.root):
                    self.fail("Second lock should fail")
        with downloads.download_lock(self.root):
            pass

    def test_low_disk_space_stops_before_network(self):
        manifest = {"osm_files": [self.pbf], "fews_points": [], "fews_fields": ""}
        with patch.object(downloads.shutil, "disk_usage", return_value=type("Disk", (), {"free": 1})()):
            with self.assertRaisesRegex(RuntimeError, "Insufficient"):
                downloads.download_all(manifest, self.root, self.root / "run")


class BundleTest(unittest.TestCase):
    def test_manifest_all_verified_inputs_have_unique_names(self):
        manifest = json.loads((ROOT / "download_manifest.json").read_text())
        entries = list(downloads.entries_from_manifest(manifest))
        self.assertEqual(len(entries), 22)
        self.assertEqual(len({e["filename"] for e in entries}), 22)
        self.assertTrue(all(e["url"].startswith("https://") for e in entries))
        self.assertEqual(sum(e["bytes"] for e in manifest["osm_files"]), 1173184208)

    def test_embedded_bundle_matches_reviewed_sources(self):
        launcher = (ROOT / "START_WB_STAGE1.cmd").read_bytes().decode("utf-8")
        match = re.search(r"\$pythonPayload = @'\r?\n(.*?)\r?\n'@", launcher, re.S)
        self.assertIsNotNone(match)
        bootstrap = base64.b64decode(match.group(1)).decode("utf-8")
        tree = ast.parse(bootstrap)
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign) and node.targets[0].id == "payload")
        payload = base64.b64decode(ast.literal_eval(assignment.value.args[0]))
        checksum = next(node for node in tree.body if isinstance(node, ast.If)).test.comparators[0].value
        self.assertEqual(hashlib.sha256(payload).hexdigest(), checksum)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(sorted(archive.namelist()), ["download_inputs.py", "download_manifest.json", "qgis_stage1.py"])
            for name in archive.namelist():
                self.assertEqual(archive.read(name), (ROOT / name).read_bytes())
        self.assertNotIn("ExecutionPolicy", launcher)
        self.assertNotIn("__PYTHON_PAYLOAD__", launcher)

    def test_qgis_bootstrap_works_without_file_global(self):
        launcher = (ROOT / "START_WB_STAGE1.cmd").read_bytes().decode("utf-8")
        match = re.search(r"\$pythonPayload = @'\r?\n(.*?)\r?\n'@", launcher, re.S)
        bootstrap = base64.b64decode(match.group(1)).decode("utf-8")
        calls = []
        fake = types.ModuleType("qgis_stage1")
        fake.start = lambda: calls.append("started")
        previous_path = list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as folder:
                with patch.dict("os.environ", {"WB_STAGE1_RUNTIME": folder}), patch.dict(sys.modules, {"qgis_stage1": fake}):
                    exec(compile(bootstrap, "<QGIS --code>", "exec"), {"__name__": "__main__"})
                self.assertEqual(calls, ["started"])
                for name in ("download_inputs.py", "download_manifest.json", "qgis_stage1.py"):
                    self.assertEqual((Path(folder) / name).read_bytes(), (ROOT / name).read_bytes())
        finally:
            sys.path[:] = previous_path


if __name__ == "__main__":
    unittest.main()
