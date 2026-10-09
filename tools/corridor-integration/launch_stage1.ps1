# Embedded inside START_WB_STAGE1.cmd by build_launcher.py.
# No administrator privileges, execution-policy changes or new Python install.
$ErrorActionPreference = 'Stop'

function Get-QgisLauncher {
    $roots = New-Object 'System.Collections.Generic.List[string]'
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
        if ($base -and (Test-Path -LiteralPath $base -PathType Container)) {
            $directories = @(Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'QGIS*' } | Sort-Object LastWriteTime -Descending)
            foreach ($directory in $directories) { $roots.Add($directory.FullName) }
        }
    }
    foreach ($root in @($env:OSGEO4W_ROOT, 'C:\OSGeo4W', 'C:\OSGeo4W64')) {
        if ($root -and (Test-Path -LiteralPath $root -PathType Container)) { $roots.Add($root) }
    }
    foreach ($key in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
        $entries = @(Get-ItemProperty -Path $key -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like 'QGIS*' })
        foreach ($entry in $entries) {
            if ($entry.InstallLocation -and (Test-Path -LiteralPath $entry.InstallLocation -PathType Container)) { $roots.Add($entry.InstallLocation) }
        }
    }
    foreach ($root in $roots) {
        foreach ($name in @('qgis-ltr.bat', 'qgis.bat', 'qgis-ltr.cmd', 'qgis.cmd')) {
            $candidate = Join-Path (Join-Path $root 'bin') $name
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    foreach ($name in @('qgis-ltr.bat', 'qgis.bat', 'qgis-ltr.cmd', 'qgis.cmd', 'qgis.exe')) {
        $command = Get-Command -Name $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return $command.Source }
    }
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = 'Select your QGIS launcher (normally bin\qgis-ltr.bat or bin\qgis.bat)'
    $dialog.Filter = 'QGIS launchers|qgis*.bat;qgis*.cmd;qgis*.exe|All files|*.*'
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw 'No QGIS launcher selected.' }
    return $dialog.FileName
}

try {
    $project = 'C:\Users\hughl\OneDrive\Desktop\Public Maps\Public Experiment.qgz'
    if (-not (Test-Path -LiteralPath $project -PathType Leaf)) { throw ('Project not found: ' + $project) }
    $launcher = Get-QgisLauncher
    $runtimeFolder = Join-Path $env:LOCALAPPDATA ('WB_Corridor_Integration\runtime\' + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($runtimeFolder) | Out-Null
    $runtime = Join-Path $runtimeFolder 'stage1_bootstrap.py'
    # This payload contains only the three reviewed Stage 1 files, not downloaded remote code.
    $pythonPayload = @'
__PYTHON_PAYLOAD__
'@
    [IO.File]::WriteAllBytes($runtime, [Convert]::FromBase64String($pythonPayload))
    Write-Host 'WORLD BANK CORRIDORS: STAGE 1 INPUT DOWNLOADS'
    Write-Host ('Opening: ' + $project)
    Write-Host ('Using: ' + $launcher)
    Write-Host 'Downloads total approximately 1.2 GB. Keep the new QGIS window open.'
    Write-Host 'No metrics are calculated and no website update is made by this launcher.'
    $arguments = '--nologo --project "' + $project + '" --code "' + $runtime + '"'
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.WorkingDirectory = Split-Path $launcher -Parent
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    if ([IO.Path]::GetExtension($launcher) -in @('.bat', '.cmd')) {
        $start.FileName = $env:ComSpec
        $start.Arguments = '/d /s /c ""' + $launcher + '" ' + $arguments + '"'
    } else {
        $start.FileName = $launcher
        $start.Arguments = $arguments
    }
    $process = [System.Diagnostics.Process]::Start($start)
    Start-Sleep -Milliseconds 1200
    if ($process.HasExited -and $process.ExitCode -ne 0) { throw ('QGIS launcher exited with error ' + $process.ExitCode) }
    Write-Host 'The Stage 1 progress panel will appear in QGIS. Original project is not saved over.'
    exit 0
} catch {
    Write-Host ('Could not start Stage 1: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
