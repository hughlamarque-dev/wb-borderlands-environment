"""Verified, resumable Stage 1 downloads. Standard library only; no QGIS imports.

Never edits the project, uploads inputs, computes connectivity, or publishes maps.
"""
import csv
import hashlib
import json
import os
import shutil
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Cancelled(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled("Stopped. Verified inputs are retained; partial PBF downloads can resume.")


def save_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def hashes(path, cancel=None):
    md5 = hashlib.md5()
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            if cancel is not None:
                check_cancel(cancel)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            md5.update(chunk)
            sha.update(chunk)
    return {"md5": md5.hexdigest(), "sha256": sha.hexdigest()}


def validate_pbf(path, entry, cancel=None):
    if Path(path).stat().st_size != entry["bytes"]:
        raise ValueError("Wrong file size: " + entry["filename"])
    with Path(path).open("rb") as stream:
        if b"OSMHeader" not in stream.read(1024):
            raise ValueError("Not an OSM PBF header: " + entry["filename"])
    digests = hashes(path, cancel)
    if digests["md5"] != entry["md5"]:
        raise ValueError("Geofabrik checksum mismatch: " + entry["filename"])
    return digests


def validate_csv(path, point_id, schedule):
    required = {"border_point_id", "collection_status", "start_date", "period_date",
                "dataseries", "source_organization", "unit_type", "unit_name",
                "value", "common_unit_quantity", "data_usage_policy", "collection_schedule"}
    count = 0
    published = 0
    dates = []
    unit_types = set()
    policies = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("FEWS CSV missing columns: " + ", ".join(sorted(required - set(reader.fieldnames or []))))
        for row in reader:
            if row.get("border_point_id") != str(point_id):
                raise ValueError("FEWS API returned the wrong crossing ID")
            if row.get("collection_schedule") != schedule:
                raise ValueError("Unexpected FEWS export schedule")
            count += 1
            unit_types.add(row.get("unit_type", ""))
            policies.add(row.get("data_usage_policy", ""))
            if row.get("collection_status") == "Published":
                published += 1
                dates.append(row.get("period_date", ""))
    return {"rows": count, "published_rows": published,
            "first_published_period": min(dates, default=None),
            "last_published_period": max(dates, default=None),
            "unit_types": sorted(unit_types), "data_usage_policies": sorted(policies),
            "export_schedule": schedule,
            "schedule_note": "Requested API representation; not proof of original collection frequency or complete observation coverage."}


@contextmanager
def download_lock(folder):
    """OS releases this lock on process exit; no stale lock-file removal needed."""
    path = Path(folder) / ".download.lock"
    stream = path.open("a+b")
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            raise RuntimeError("Another Stage 1 download is running in this folder.") from error
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def fetch(entry, folder, emit, cancel, opener=urllib.request.urlopen):
    """Checksum-pinned PBFs resume. Mutable CSV exports restart after interruption."""
    name = entry["filename"]
    if Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("Unsafe input filename")
    url = entry["url"]
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("Source must use HTTPS")
    target = Path(folder) / name
    receipt_path = target.with_name(name + ".receipt.json")
    pinned = entry["kind"] == "osm_pbf"
    if target.exists():
        emit("Checking existing " + name, 0)
        if pinned:
            summary = validate_pbf(target, entry, cancel)
        else:
            summary = validate_csv(target, entry["point_id"], entry["schedule"])
            summary.update(hashes(target, cancel))
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("sha256") != summary["sha256"] or receipt.get("requested_url") != url:
                raise ValueError("Existing file differs from its download receipt; preserved: " + name)
            return dict(receipt, reused=True)
        if not pinned:
            raise ValueError("Untracked CSV already exists; preserved: " + name)
        receipt = dict(summary, filename=name, requested_url=url,
                       bytes=target.stat().st_size, verified_at_utc=utc_now(),
                       downloaded_at_utc=None, provenance="pre-existing checksum-matched pinned file", reused=True)
        save_json(receipt_path, receipt)
        return receipt

    partial = target.with_name(name + ".part")
    state_path = target.with_name(name + ".part.json")
    identity = {"requested_url": url, "md5": entry.get("md5"), "bytes": entry.get("bytes")}
    if partial.exists():
        if not state_path.exists() or json.loads(state_path.read_text(encoding="utf-8")) != identity:
            raise ValueError("Untracked partial file exists; preserved: " + partial.name)
    save_json(state_path, identity)
    metadata = {}
    for attempt in range(3):
        check_cancel(cancel)
        try:
            offset = partial.stat().st_size if pinned and partial.exists() else 0
            if pinned and offset > entry["bytes"]:
                raise ValueError("Oversized partial file; preserved: " + name)
            if not (pinned and offset == entry["bytes"]):
                headers = {"User-Agent": "WB-Corridor-Integration/Stage1", "Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = "bytes=" + str(offset) + "-"
                request = urllib.request.Request(url, headers=headers)
                emit("Downloading " + name, int(offset * 100 / entry["bytes"]) if pinned else 0)
                with opener(request, timeout=60) as response:
                    status = response.getcode()
                    if offset and status == 206:
                        if not response.headers.get("Content-Range", "").startswith("bytes " + str(offset) + "-"):
                            raise ValueError("Unexpected HTTP resume range")
                        mode = "ab"
                    elif status == 200:
                        mode = "wb"
                        offset = 0
                    else:
                        raise ValueError("Unexpected download status: " + str(status))
                    metadata = {"resolved_url": response.geturl(), "last_modified": response.headers.get("Last-Modified"),
                                "etag": response.headers.get("ETag"), "started_at_utc": utc_now()}
                    if urllib.parse.urlsplit(metadata["resolved_url"]).scheme != "https":
                        raise ValueError("Source redirected away from HTTPS")
                    length_header = response.headers.get("Content-Length")
                    expected_transfer = int(length_header) if length_header else None
                    transferred = 0
                    total = offset
                    last_emit = 0.0
                    with partial.open(mode) as output:
                        while True:
                            check_cancel(cancel)
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            transferred += len(chunk)
                            total += len(chunk)
                            if total > (entry["bytes"] if pinned else 50 * 1024 * 1024):
                                raise ValueError("Download exceeds expected size")
                            output.write(chunk)
                            if time.monotonic() - last_emit > 0.5:
                                percent = int(total * 100 / entry["bytes"]) if pinned else 0
                                emit(name + ": " + str(round(total / 1048576, 1)) + " MiB", percent)
                                last_emit = time.monotonic()
                    if expected_transfer is not None and transferred != expected_transfer:
                        raise IOError("Incomplete HTTP response")
            check_cancel(cancel)
            emit("Validating " + name, 100)
            if pinned:
                summary = validate_pbf(partial, entry, cancel)
            else:
                summary = validate_csv(partial, entry["point_id"], entry["schedule"])
                summary.update(hashes(partial, cancel))
            receipt = dict(summary, **metadata, filename=name, requested_url=url,
                           bytes=partial.stat().st_size, downloaded_at_utc=utc_now(), reused=False)
            if target.exists():
                raise FileExistsError("Target appeared during download; preserved: " + name)
            # Save provenance first. A crash before rename leaves a recoverable partial.
            save_json(receipt_path, receipt)
            partial.rename(target)
            state_path.unlink()
            emit("Verified " + name, 100)
            return receipt
        except (Cancelled, ValueError, FileExistsError):
            raise
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 404, 410):
                raise RuntimeError("Source unavailable or access denied (HTTP " + str(error.code) + "): " + url) from error
            if attempt == 2:
                raise
            emit("HTTP interruption; retrying " + name, 0)
            for unused in range((attempt + 1) * 20):
                check_cancel(cancel)
                time.sleep(0.1)
        except Exception:
            if attempt == 2:
                raise
            emit("Network interruption; retrying " + name, 0)
            for unused in range((attempt + 1) * 20):
                check_cancel(cancel)
                time.sleep(0.1)
    raise RuntimeError("Download did not finish")


def entries_from_manifest(manifest):
    for entry in manifest["osm_files"]:
        yield dict(entry, kind="osm_pbf")
    for point in manifest["fews_points"]:
        for schedule in ("Monthly", "Daily"):
            query = urllib.parse.urlencode({"border_point": point["id"], "schedule": schedule,
                                           "fields": manifest["fews_fields"]})
            yield {"kind": "fews_csv", "point_id": point["id"], "schedule": schedule,
                   "filename": "fews_" + str(point["id"]) + "_" + schedule.lower() + ".csv",
                   "url": "https://fdw.fews.net/api/tradeflowquantityvalue.csv?" + query}


def download_all(manifest, root, run_folder, emit=lambda message, percent: None, cancel=None):
    cancel = cancel or threading.Event()
    root, run_folder = Path(root), Path(run_folder)
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    run_folder.mkdir(parents=True, exist_ok=True)
    receipts = []
    with download_lock(inputs):
        required = sum(e["bytes"] for e in manifest["osm_files"] if not (inputs / e["filename"]).exists())
        if shutil.disk_usage(inputs).free < required + 512 * 1024 * 1024:
            raise RuntimeError("Insufficient free disk space for downloads plus a 512 MiB buffer.")
        for entry in entries_from_manifest(manifest):
            receipt = fetch(entry, inputs, emit, cancel)
            receipts.append(receipt)
            save_json(run_folder / "download_receipts.json", receipts)
    return receipts


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--manifest", default=str(Path(__file__).with_name("download_manifest.json")))
    options = parser.parse_args()
    manifest = json.loads(Path(options.manifest).read_text(encoding="utf-8"))
    run = Path(options.root) / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8])
    download_all(manifest, options.root, run, emit=lambda message, percent: print(message, flush=True))
