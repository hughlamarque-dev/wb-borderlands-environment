"""Fetch checksum-pinned baseline/parser; avoid modifying installed QGIS."""
import gzip
import hashlib
import importlib
import json
import os
import platform
import struct
import sys
import sysconfig
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from download_inputs import check_cancel, hashes, save_json

def pinned_download(url, path, sha256, emit, cancel, expected_bytes=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not url.startswith("https://"):
        raise ValueError("Setup source must use HTTPS")
    if path.exists():
        if hashes(path, cancel)["sha256"] != sha256:
            raise ValueError("Checksum mismatch in existing setup input; preserved: " + path.name)
        return path
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    emit("Downloading setup input: " + path.name, 0)
    try:
        digest, total = hashlib.sha256(), 0
        request = urllib.request.Request(url, headers={"User-Agent": "WB-Corridor-Integration/Stage2"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("xb") as output:
            if not response.geturl().startswith("https://"):
                raise ValueError("Setup input redirected away from HTTPS")
            while True:
                check_cancel(cancel)
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 50 * 1024 * 1024:
                    raise ValueError("Setup input exceeds 50 MiB limit")
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != sha256 or (expected_bytes is not None and total != expected_bytes):
            raise ValueError("Downloaded setup input failed integrity checks")
        if path.exists():
            raise FileExistsError("Setup destination appeared during download")
        temporary.rename(path)
        return path
    finally:
        if temporary.exists():
            temporary.unlink()

def load_baseline(config, root, emit, cancel):
    path = Path(root) / "inputs" / ("published_baseline_" + config["baseline_sha256"] + ".json.gz")
    pinned_download(config["baseline_url"], path, config["baseline_sha256"], emit, cancel, config["baseline_bytes"])
    data = json.loads(gzip.decompress(path.read_bytes()))
    if data.get("format") != "wb-corridor-public-baseline-v1" or data.get("source_access") != "public" or data.get("source_commit") != config["baseline_source_commit"]:
        raise ValueError("Unexpected public baseline provenance")
    return data

def unpack_wheel(wheel, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(wheel) as archive:
        if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError("Unexpected dependency archive size")
        for entry in archive.infolist():
            name = PurePosixPath(entry.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in entry.filename or ":" in entry.filename:
                raise ValueError("Unsafe dependency archive filename")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Dependency archive contains a symlink")
            target = destination.joinpath(*name.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(archive.read(entry))

def ensure_osmium(config, root, emit, cancel):
    if "osmium" in sys.modules:
        module = sys.modules["osmium"]
        return module, {"source": "already loaded in QGIS", "module_path": module.__file__}
    tag = "cp" + str(sys.version_info.major) + str(sys.version_info.minor)
    if os.name != "nt" or struct.calcsize("P") != 8 or sysconfig.get_config_var("Py_GIL_DISABLED") or tag not in config["osmium_wheels"]:
        raise RuntimeError("No verified parser wheel for this QGIS Python/architecture: " + sys.version)
    wheel = config["osmium_wheels"][tag]
    cache = Path(root) / "dependencies"
    downloaded = pinned_download(wheel["url"], cache / wheel["filename"], wheel["sha256"], emit, cancel)
    marker = cache / ("osmium_" + tag + "_" + config["osmium_version"] + ".json")
    if marker.exists():
        info = json.loads(marker.read_text(encoding="utf-8"))
        if Path(info["directory"]).name != info["directory"]:
            raise ValueError("Unsafe parser setup directory")
        target = cache / info["directory"]
        if not target.is_dir() or info.get("wheel_sha256") != wheel["sha256"]:
            raise ValueError("Existing parser setup does not match its receipt")
    else:
        target = cache / ("osmium_" + tag + "_" + uuid.uuid4().hex)
        unpack_wheel(downloaded, target)
        info = {"directory": target.name, "wheel_sha256": wheel["sha256"]}
        save_json(marker, info)
    sys.path.insert(0, str(target))
    importlib.invalidate_caches()
    module = importlib.import_module("osmium")
    return module, {"source": "checksum-pinned PyPI wheel, task-local directory", "version": config["osmium_version"],
                    "wheel_sha256": wheel["sha256"], "python_version": sys.version, "platform": platform.platform()}

