"""Stage 2 QGIS controller: checked inputs, new layers/project copy, review ZIP."""
import csv
import hashlib
import json
import os
import queue
import shutil
import threading
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from qgis.core import (QgsCategorizedSymbolRenderer, QgsLineSymbol, QgsMarkerSymbol, QgsProject,
                       QgsRendererCategory, QgsVectorFileWriter, QgsVectorLayer)
from qgis.PyQt.QtCore import QTimer, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QDockWidget, QFileDialog, QLabel, QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget
from download_inputs import Cancelled, check_cancel, download_lock, hashes, save_json, utc_now, validate_csv, validate_pbf
from qgis_stage1 import inventory
from stage2_graph import padded_bounds, parse_extracts
from stage2_runtime import ensure_osmium, load_baseline
from stage2_spatial import compute_map
from stage2_tabular import is_gems_file, physical_fews_places, read_fews, read_gems

CONTROLLER = None
ATTEMPTS = 0
NAMES = {"moyale_borana": "Moyale / Borana", "mandera_triangle": "Mandera Triangle", "karamoja": "Karamoja", "dikhil": "Dikhil"}

def choose_gems(project_path, config, iface):
    folders = [Path.home() / "Downloads", Path(project_path).parent, Path(project_path).parent.parent / "World Bank GIS"]
    candidates = []
    for folder in folders:
        if folder.is_dir():
            candidates.extend(p for p in folder.glob("data*.csv") if p.is_file() and p.stat().st_size <= 100 * 1024 * 1024)
    for path in sorted(set(candidates), key=lambda p: p.stat().st_mtime, reverse=True):
        if is_gems_file(path) and hashes(path)["sha256"] == config["gems_original_sha256"]:
            return path
    selected, unused = QFileDialog.getOpenFileName(iface.mainWindow(), "Select the GEMS CSV you supplied (data.csv)", str(Path.home() / "Downloads"), "CSV files (*.csv)")
    if not selected:
        raise ValueError("No GEMS file selected. No processing or website change made.")
    if not is_gems_file(selected):
        raise ValueError("Selected CSV does not contain required GEMS columns.")
    return Path(selected)

def capture_boundaries(project):
    result = {}
    for code, name in NAMES.items():
        layers = project.mapLayersByName(name)
        if len(layers) != 1 or not layers[0].isValid():
            raise ValueError("Expected one valid QGIS cluster layer: " + name)
        layer = layers[0]
        if layer.crs().authid() != "EPSG:4326":
            raise ValueError("Unexpected CRS for cluster layer: " + name)
        result[code] = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                         "geometry": json.loads(f.geometry().asJson(12))} for f in layer.getFeatures()]}
        if not result[code]["features"]:
            raise ValueError("Empty current cluster boundary: " + name)
    return result

def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

class Controller:
    def __init__(self, iface, project, manifest, config):
        if project.isDirty():
            raise ValueError("The new QGIS window has unsaved changes. Resolve them before running Stage 2.")
        self.iface, self.manifest, self.config = iface, manifest, config
        self.original = Path(manifest["project_path"])
        self.root = self.original.parent / "WB_Corridor_Integration"
        self.gems_path = choose_gems(self.original, config, iface)
        self.boundaries = capture_boundaries(project)
        self.original_inventory = inventory(project)
        run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        self.run_folder = self.root / "runs_stage2" / run
        self.run_folder.mkdir(parents=True, exist_ok=False)
        self.web = self.run_folder / "web_outputs"
        self.web.mkdir()
        save_json(self.run_folder / "original_project_inventory.json", self.original_inventory)
        save_json(self.run_folder / "stage2_config.json", config)
        self.events, self.cancel = queue.Queue(), threading.Event()
        self.dock = QDockWidget("World Bank corridors: Stage 2", iface.mainWindow())
        body = QWidget(self.dock)
        layout = QVBoxLayout(body)
        self.status = QLabel("Building major-road access and source-evidence layers. Keep this new QGIS window open.")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(800)
        self.stop = QPushButton("Stop processing")
        self.stop.clicked.connect(lambda: self.cancel.set())
        self.open_folder = QPushButton("Open output/report folder")
        self.open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.run_folder))))
        for widget in (self.status, self.progress, self.log, self.stop, self.open_folder):
            layout.addWidget(widget)
        self.dock.setWidget(body)
        area = Qt.RightDockWidgetArea if hasattr(Qt, "RightDockWidgetArea") else Qt.DockWidgetArea.RightDockWidgetArea
        iface.addDockWidget(area, self.dock)
        self.dock.show()
        self.timer = QTimer(self.dock)
        self.timer.timeout.connect(self.poll)
        self.timer.start(300)
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()

    def emit(self, text, percent=0):
        self.events.put(("progress", text, percent))

    def work(self):
        result = {"stage": 2, "started_at_utc": utc_now(), "website_changed": False, "project_saved_over_original": False,
                  "maps": [], "publication_status": "REVIEW_REQUIRED", "metric_version": self.config["metric_version"],
                  "metric_definitions": {"junctions": self.config["junction_definition"], "major_roads": self.config["major_road_definition"],
                      "urban": "OSM city/town nodes; villages counted within all mapped places.",
                      "paths": "Directed local graph excluding major edges; distance includes straight-line place snap up to 250 m.",
                      "length": "Mapped major-road node-pair kilometres in local AEQD CRS; not a merged corridor centreline.",
                      "limitations": ["Functional interchanges are not merged; divided roads can add multiple nodes/carriageway lengths.",
                                      "Turn restrictions, dynamic border closures and surface passability are not modelled.",
                                      "OSM completeness and missing place nodes limit measured connectivity.",
                                      "No population-access score, traffic estimate, movement route or economic-impact claim is generated."]}}
        try:
            result["original_project_sha256_before"] = hashes(self.original, self.cancel)["sha256"]
            if shutil.disk_usage(self.root).free < 2 * 1024 ** 3:
                raise RuntimeError("Stage 2 needs at least 2 GiB free working space before starting.")
            inputs = self.root / "inputs"
            with download_lock(inputs):
                for entry in self.manifest["osm_files"]:
                    self.emit("Rechecking source integrity: " + entry["filename"])
                    validate_pbf(inputs / entry["filename"], entry, self.cancel)
                for point in self.config["fews_points"]:
                    for schedule in ("Monthly", "Daily"):
                        path = inputs / ("fews_" + str(point["id"]) + "_" + schedule.lower() + ".csv")
                        validate_csv(path, point["id"], schedule)
                        receipt = json.loads(path.with_name(path.name + ".receipt.json").read_text(encoding="utf-8"))
                        if hashes(path, self.cancel)["sha256"] != receipt["sha256"]:
                            raise ValueError("FEWS source changed since Stage 1: " + path.name)
            baseline = load_baseline(self.config, self.root, self.emit, self.cancel)
            result.update(baseline_source_commit=baseline["source_commit"], baseline_sha256=self.config["baseline_sha256"])
            gems, projects, invalid_gems, gems_audit = read_gems(self.gems_path, baseline)
            result["gems_audit"] = gems_audit
            result["gems_matches_original_upload"] = gems_audit["source_sha256"] == self.config["gems_original_sha256"]
            save_json(self.web / "gems_projects.json", projects)
            save_json(self.run_folder / "gems_invalid_rows.json", invalid_gems)
            observations, fews_audit = read_fews(inputs, self.config)
            save_json(self.run_folder / "fews_audit.json", fews_audit)
            write_csv(self.web / "fews_monthly_observations.csv", observations)
            result["fews_audit"] = fews_audit
            places = physical_fews_places(self.config, observations)
            osmium, dependency = ensure_osmium(self.config, self.root, self.emit, self.cancel)
            result["parser_dependency"] = dependency
            import stage2_graph
            cache_key = hashlib.sha256(json.dumps({"baseline": self.config["baseline_sha256"],
                "parser_source_sha256": hashes(stage2_graph.__file__)["sha256"],
                "pbf": [(e["filename"], e["md5"]) for e in self.manifest["osm_files"]],
                "metric_version": self.config["metric_version"], "halo": self.config["network_halo_km"]}, sort_keys=True).encode()).hexdigest()
            cache = self.root / "network_cache" / cache_key
            cache.mkdir(parents=True, exist_ok=True)
            database, marker = cache / "topology.sqlite", cache / "complete.json"
            with download_lock(cache):
                if marker.exists():
                    cached = json.loads(marker.read_text(encoding="utf-8"))
                    filename = cached.get("database_filename", "topology.sqlite")
                    if Path(filename).name != filename or not filename.endswith(".sqlite"):
                        raise ValueError("Unsafe topology cache filename")
                    database = cache / filename
                    if not database.exists() or cached["cache_key"] != cache_key or hashes(database, self.cancel)["sha256"] != cached["database_sha256"]:
                        raise ValueError("Completed topology cache differs from its verified receipt")
                    result["topology_audit"] = cached["audit"]
                    self.emit("Reusing verified topology cache")
                else:
                    if database.exists():
                        database = cache / ("topology_" + uuid.uuid4().hex + ".sqlite")
                        result["topology_cache_note"] = "Retained earlier interrupted cache database"
                    boxes = [padded_bounds(d["bounds"], self.config["network_halo_km"]) for d in baseline["maps"].values()]
                    topology = parse_extracts(osmium, self.manifest["osm_files"], inputs, database, boxes, self.emit, self.cancel)
                    save_json(marker, {"cache_key": cache_key, "database_filename": database.name,
                                       "database_sha256": hashes(database, self.cancel)["sha256"], "audit": topology})
                    result["topology_audit"] = topology
            for code, data in baseline["maps"].items():
                check_cancel(self.cancel)
                result["maps"].append(compute_map(code, data, database, gems, projects, places, self.config,
                                                  self.web, self.emit, self.cancel, self.boundaries[code]))
                save_json(self.run_folder / "stage2_status.json", result)
            result["original_project_sha256_after_processing"] = hashes(self.original, self.cancel)["sha256"]
            if result["original_project_sha256_after_processing"] != result["original_project_sha256_before"]:
                raise ValueError("Original project changed during processing; stopped before project-copy creation")
            result["status"] = "PROCESSED_WAITING_PROJECT_COPY"
            self.events.put(("processed", result, None))
        except Cancelled as error:
            result.update(status="STOPPED", error=str(error))
            self.events.put(("failed", result, None))
        except Exception as error:
            result.update(status="FAILED", error=str(error), traceback=traceback.format_exc())
            self.events.put(("failed", result, None))

    def save_project_copy(self, result):
        self.status.setText("Saving a separate GeoPackage and QGIS project copy")
        project = QgsProject()
        if not project.read(str(self.original)):
            raise ValueError("Could not read original into a separate project object")
        group = project.layerTreeRoot().insertGroup(0, "Stage 2: major-road access and source evidence (review)")
        geopackage = self.run_folder / "WB_Corridor_Stage2.gpkg"
        labels = {"connectivity_hexes": "Major-road access: published 10 km grid", "junction_nodes": "Major-road junction nodes",
                  "major_roads": "Mapped major roads (8 Oct 2026)", "mapped_places": "OSM mapped place nodes",
                  "gems_locations": "GEMS source locations (undated)", "gems_review": "GEMS locations requiring review", "fews_places": "FEWS observed trade: reference places"}
        loaded = []
        for summary in result["maps"]:
            sub = group.addGroup(summary["title"])
            for kind, path in summary["paths"].items():
                if not json.loads(Path(path).read_text(encoding="utf-8"))["features"]:
                    continue
                source = QgsVectorLayer(path, labels[kind], "ogr")
                if not source.isValid():
                    raise ValueError("Processed GeoJSON did not load: " + kind)
                name = summary["code"] + "_" + kind
                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName, options.layerName, options.fileEncoding = "GPKG", name, "UTF-8"
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer if geopackage.exists() else QgsVectorFileWriter.CreateOrOverwriteFile
                written = QgsVectorFileWriter.writeAsVectorFormatV3(source, str(geopackage), project.transformContext(), options)
                if written[0] != QgsVectorFileWriter.NoError:
                    raise ValueError("Could not save processed layer: " + str(written))
                layer = QgsVectorLayer(str(geopackage) + "|layername=" + name, labels[kind], "ogr")
                if not layer.isValid():
                    raise ValueError("Saved GeoPackage layer did not load: " + name)
                if kind == "junction_nodes":
                    categories = [QgsRendererCategory(1, QgsMarkerSymbol.createSimple({"name": "circle", "color": "15,115,100", "size": "2.0"}), "Mapped town path within 5 km"),
                                  QgsRendererCategory(0, QgsMarkerSymbol.createSimple({"name": "circle", "color": "195,126,35", "size": "2.0"}), "No mapped town path within 5 km found")]
                    layer.setRenderer(QgsCategorizedSymbolRenderer("urban_5km", categories))
                elif kind == "major_roads":
                    layer.renderer().setSymbol(QgsLineSymbol.createSimple({"line_color": "60,70,85", "line_width": "0.5"}))
                elif kind in {"gems_locations", "gems_review", "fews_places"}:
                    color = "115,70,140" if kind == "gems_locations" else "190,70,55" if kind == "gems_review" else "35,100,155"
                    layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "circle", "color": color, "size": "2.5"}))
                project.addMapLayer(layer, False)
                node = sub.addLayer(layer)
                node.setItemVisibilityChecked(summary["code"] == "karamoja" and kind == "junction_nodes")
                loaded.append(name)
        copy = self.run_folder / "Public_Experiment_WB_Stage2.qgz"
        if not project.write(str(copy)):
            raise ValueError("Could not save separate QGIS project copy")
        result.update(output_project=str(copy), output_geopackage=str(geopackage), processed_layers_saved=loaded,
                      original_project_sha256_after_copy=hashes(self.original)["sha256"])
        if result["original_project_sha256_after_copy"] != result["original_project_sha256_before"]:
            raise ValueError("Original project changed; detected after saving separate copy")
        result["original_project_file_unchanged"] = True
        current = QgsProject.instance()
        same = os.path.normcase(os.path.abspath(current.fileName())) == os.path.normcase(os.path.abspath(str(self.original)))
        if same and not current.isDirty():
            result["output_project_opened"] = current.read(str(copy))
        else:
            result.update(output_project_opened=False, open_note="GUI project edited/switched during processing; output saved separately without replacing edits.")
        result["status"] = "PROCESSED_READY_FOR_REVIEW"

    def finish(self, result):
        result.update(finished_at_utc=utc_now(), next_step="Upload WB_STAGE2_REPORT.zip. It contains processed review outputs, not raw PBFs or full original GEMS CSV. Website integration remains pending.")
        save_json(self.run_folder / "stage2_status.json", result)
        archive = self.run_folder / "WB_STAGE2_REPORT.zip"
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
            for name in ("stage2_status.json", "stage2_config.json", "original_project_inventory.json", "fews_audit.json", "gems_invalid_rows.json"):
                path = self.run_folder / name
                if path.exists():
                    output.write(path, arcname=name)
            for path in sorted(self.web.glob("*")):
                if path.is_file():
                    output.write(path, arcname="web_outputs/" + path.name)
        self.stop.setEnabled(False)
        self.status.setText(result["status"] + "\n" + result.get("error", result["next_step"]))
        self.log.append(self.status.text() + "\nReport: " + str(archive))
        self.progress.setValue(100 if result["status"] == "PROCESSED_READY_FOR_REVIEW" else 0)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.run_folder)))

    def poll(self):
        while True:
            try:
                kind, message, value = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "progress":
                self.status.setText(message)
                self.progress.setValue(value)
                self.log.append(message)
                continue
            self.timer.stop()
            if kind == "processed":
                try:
                    self.save_project_copy(message)
                except Exception as error:
                    message.update(status="FAILED_PROJECT_COPY", error=str(error), traceback=traceback.format_exc())
            try:
                self.finish(message)
            except Exception as error:
                self.status.setText("Report could not be written: " + str(error))
                self.stop.setEnabled(False)

def start():
    global CONTROLLER, ATTEMPTS
    from qgis.utils import iface
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / "download_manifest.json").read_text(encoding="utf-8"))
    config = json.loads((here / "stage2_config.json").read_text(encoding="utf-8"))
    project = QgsProject.instance()
    expected = os.path.normcase(os.path.abspath(manifest["project_path"]))
    actual = os.path.normcase(os.path.abspath(project.fileName())) if project.fileName() else ""
    if iface is None or actual != expected:
        ATTEMPTS += 1
        if ATTEMPTS < 120:
            QTimer.singleShot(500, start)
        else:
            QMessageBox.critical(None, "Stage 2 did not start", "The requested project has not finished opening. Resolve missing-layer dialogs, then rerun the launcher.")
        return
    try:
        CONTROLLER = Controller(iface, project, manifest, config)
    except Exception as error:
        QMessageBox.critical(iface.mainWindow(), "Stage 2 did not start", str(error))

