"""QGIS GUI entry point: open existing project, inventory it, download Stage 1 inputs.

All QGIS inspection happens on the GUI thread. Downloads use a daemon Python
thread and never call QGIS. Original project and map layers are not modified.
"""
import json
import os
import queue
import threading
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from qgis.core import Qgis, QgsProject, QgsProviderRegistry
from qgis.PyQt.QtCore import QTimer, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QDockWidget, QLabel, QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget

from download_inputs import Cancelled, download_all, hashes, save_json, utc_now

CONTROLLER = None
ATTEMPTS = 0


def local_source(layer):
    """Keep local filenames; omit remote URLs, database URIs and credentials."""
    source = layer.source()
    if layer.providerType() == "delimitedtext":
        parsed = urlsplit(source)
        if parsed.scheme == "file":
            path = unquote(parsed.path)
            if len(path) > 2 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            return path
        return "<non-file CSV source omitted>"
    if layer.providerType() not in {"ogr", "gdal"}:
        return "<non-file source omitted>"
    decoded = QgsProviderRegistry.instance().decodeUri(layer.providerType(), source)
    path = str(decoded.get("path", "")) or source.split("|", 1)[0]
    if any(value in path.lower() for value in ("://", "/vsicurl/", "/vsis3/", "authcfg=", "password=")):
        return "<remote or credential-bearing source omitted>"
    if path.startswith("/vsizip/") or Path(path).is_absolute():
        return path
    return "<non-local source omitted>"


def inventory(project):
    rows = []
    for layer_id, layer in project.mapLayers().items():
        rows.append({"id": layer_id, "name": layer.name(), "provider": layer.providerType(),
                     "valid": layer.isValid(), "crs": layer.crs().authid(),
                     "local_source": local_source(layer)})
    return {"captured_at_utc": utc_now(), "qgis_version": Qgis.QGIS_VERSION,
            "project_path": project.fileName(), "project_crs": project.crs().authid(),
            "project_has_unsaved_changes": project.isDirty(), "layers": rows,
            "invalid_layer_count": sum(not row["valid"] for row in rows),
            "source_note": "Remote URLs, query parameters and database connection strings omitted. No feature data exported."}


class Controller:
    def __init__(self, iface, manifest, project):
        self.iface = iface
        self.manifest = manifest
        self.project_path = Path(manifest["project_path"])
        self.root = self.project_path.parent / "WB_Corridor_Integration"
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        self.run_folder = self.root / "runs" / run_id
        self.run_folder.mkdir(parents=True, exist_ok=False)
        self.project_inventory = inventory(project)
        save_json(self.run_folder / "project_inventory.json", self.project_inventory)
        save_json(self.run_folder / "download_manifest.json", manifest)
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.messages = []

        self.dock = QDockWidget("World Bank corridors: Stage 1", iface.mainWindow())
        body = QWidget(self.dock)
        layout = QVBoxLayout(body)
        self.status = QLabel("Downloading inputs. No metrics or website changes are being made.")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.stop = QPushButton("Stop downloads (keep partial files)")
        self.stop.clicked.connect(lambda: self.cancel.set())
        self.open_folder = QPushButton("Open report folder")
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

    def work(self):
        result = {"stage": 1, "started_at_utc": utc_now(), "project_path": str(self.project_path),
                  "processed_data_created": False, "project_saved": False, "website_changed": False,
                  "gems_status": "Not yet inspected on this computer; keep the original GEMS CSV locally."}
        try:
            result["project_sha256_before"] = hashes(self.project_path, self.cancel)["sha256"]
            receipts = download_all(self.manifest, self.root, self.run_folder,
                                    lambda text, percent: self.events.put(("progress", text, percent)), self.cancel)
            result["input_files_verified"] = len(receipts)
            result["project_sha256_after"] = hashes(self.project_path, self.cancel)["sha256"]
            result["project_file_unchanged"] = result["project_sha256_before"] == result["project_sha256_after"]
            if not result["project_file_unchanged"]:
                result["status"] = "REVIEW_REQUIRED_PROJECT_CHANGED"
            elif self.project_inventory["invalid_layer_count"]:
                result["status"] = "INPUTS_READY_PROJECT_HAS_INVALID_LAYERS"
            else:
                result["status"] = "INPUTS_READY_FOR_STAGE2_REVIEW"
            result["next_step"] = "Upload WB_STAGE1_REPORT.zip for the Stage 2 script. Connectivity, GEMS joins and website integration are pending."
        except Cancelled as error:
            result.update(status="STOPPED", error=str(error))
        except Exception as error:
            result.update(status="FAILED", error=str(error), traceback=traceback.format_exc())
        result["finished_at_utc"] = utc_now()
        try:
            save_json(self.run_folder / "stage1_status.json", result)
            archive = self.run_folder / "WB_STAGE1_REPORT.zip"
            with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
                for name in ("stage1_status.json", "project_inventory.json", "download_manifest.json", "download_receipts.json"):
                    path = self.run_folder / name
                    if path.exists():
                        output.write(path, arcname=name)
            self.events.put(("done", result, str(archive)))
        except Exception as error:
            self.events.put(("report_failed", str(error), ""))

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
            else:
                self.timer.stop()
                self.stop.setEnabled(False)
                if kind == "report_failed":
                    self.status.setText("Could not write the report: " + message)
                else:
                    text = message["status"] + "\n" + message.get("error", message.get("next_step", ""))
                    self.status.setText(text)
                    self.log.append(text + "\nReport: " + value)
                    self.progress.setValue(100 if message["status"].startswith("INPUTS_READY") else 0)
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.run_folder)))


def start():
    """Called by the QGIS --code startup file after the GUI initializes."""
    global CONTROLLER, ATTEMPTS
    from qgis.utils import iface
    manifest = json.loads(Path(__file__).with_name("download_manifest.json").read_text(encoding="utf-8"))
    project = QgsProject.instance()
    expected = os.path.normcase(os.path.abspath(manifest["project_path"]))
    actual = os.path.normcase(os.path.abspath(project.fileName())) if project.fileName() else ""
    if iface is None or actual != expected:
        ATTEMPTS += 1
        if ATTEMPTS < 120:
            QTimer.singleShot(500, start)
        else:
            QMessageBox.critical(None, "Stage 1 did not start", "The requested project has not finished opening. Resolve any missing-layer dialog, then rerun the launcher. No processing or website change was made.")
        return
    try:
        CONTROLLER = Controller(iface, manifest, project)
    except Exception as error:
        QMessageBox.critical(iface.mainWindow(), "Stage 1 did not start", str(error))
