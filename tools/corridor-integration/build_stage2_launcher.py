"""Rebuild double-click launcher, embedding reviewed code and config only."""
import base64
import hashlib
import io
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent
FILES = ("download_inputs.py", "download_manifest.json", "qgis_stage1.py", "qgis_stage2.py", "stage2_config.json",
         "stage2_graph.py", "stage2_runtime.py", "stage2_spatial.py", "stage2_tabular.py")
def build():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 9, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, (ROOT / name).read_bytes())
    payload = buffer.getvalue()
    bootstrap = """import base64, hashlib, io, os, sys, zipfile
from pathlib import Path
payload = base64.b64decode(%r)
if hashlib.sha256(payload).hexdigest() != %r:
    raise RuntimeError('Stage 2 bundle checksum mismatch')
runtime = Path(os.environ['WB_STAGE2_RUNTIME'])
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    if sorted(archive.namelist()) != %r:
        raise RuntimeError('Unexpected Stage 2 bundle file')
    for name in archive.namelist():
        with (runtime / name).open('xb') as output:
            output.write(archive.read(name))
sys.path.insert(0, str(runtime))
from qgis_stage2 import start
start()
""" % (base64.b64encode(payload).decode(), hashlib.sha256(payload).hexdigest(), sorted(FILES))
    encoded = base64.b64encode(bootstrap.encode()).decode()
    shared = (ROOT / "launch_stage1.ps1").read_text(encoding="utf-8").partition("\ntry {")[0]
    template = (ROOT / "launch_stage2.ps1").read_text(encoding="utf-8")
    powershell = shared + "\n" + template.replace("__PYTHON_PAYLOAD__", "\n".join(encoded[i:i + 100] for i in range(0, len(encoded), 100)))
    batch = """@echo off
setlocal
title World Bank corridors - Stage 2
set "WB_STAGE2_LAUNCHER=%~f0"
powershell.exe -NoLogo -NoProfile -STA -Command "$text=[IO.File]::ReadAllText($env:WB_STAGE2_LAUNCHER); $m=[regex]::Match($text,'(?ms)^:__WB_POWERSHELL_BEGIN__\\r?\\n(.*?)^:__WB_POWERSHELL_END__'); if(-not $m.Success) { Write-Host 'Launcher content is incomplete.'; exit 1 }; & ([ScriptBlock]::Create($m.Groups[1].Value))"
set "WB_STAGE2_EXIT=%ERRORLEVEL%"
if not "%WB_STAGE2_EXIT%"=="0" pause
exit /b %WB_STAGE2_EXIT%
:__WB_POWERSHELL_BEGIN__
""" + powershell + "\n:__WB_POWERSHELL_END__\n"
    path = ROOT / "START_WB_STAGE2.cmd"
    path.write_bytes(batch.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    print(path, hashlib.sha256(path.read_bytes()).hexdigest())
    # ZIP delivery downloads as a file instead of displaying batch source in a tab.
    delivery = ROOT / "WB_STAGE2_FIXED.zip"
    info = zipfile.ZipInfo("START_WB_STAGE2_FIXED.cmd", date_time=(2026, 10, 10, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    with zipfile.ZipFile(delivery, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(info, path.read_bytes())
    print(delivery, hashlib.sha256(delivery.read_bytes()).hexdigest())
if __name__ == "__main__":
    build()

