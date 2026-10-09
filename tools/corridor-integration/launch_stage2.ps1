# Get-QgisLauncher is copied from the verified Stage 1 launcher by the builder.
try {
    $project = 'C:\Users\hughl\OneDrive\Desktop\Public Maps\Public Experiment.qgz'
    if (-not (Test-Path -LiteralPath $project -PathType Leaf)) { throw ('Project not found: ' + $project) }
    $launcher = Get-QgisLauncher
    $runtimeFolder = Join-Path $env:LOCALAPPDATA ('WB_Corridor_Integration\runtime\' + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($runtimeFolder) | Out-Null
    $runtime = Join-Path $runtimeFolder 'stage2_bootstrap.py'
    $pythonPayload = @'
__PYTHON_PAYLOAD__
'@
    [IO.File]::WriteAllBytes($runtime, [Convert]::FromBase64String($pythonPayload))
    Write-Host 'WORLD BANK CORRIDORS: STAGE 2 PROCESSING'
    Write-Host ('Opening: ' + $project)
    Write-Host 'Existing downloads are reused. A verified baseline and OSM parser may download automatically.'
    Write-Host 'Keep the new QGIS window open. A separate project copy and report will be created.'
    $arguments = '--nologo --project "' + $project + '" --code "' + $runtime + '"'
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.WorkingDirectory = Split-Path $launcher -Parent
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.EnvironmentVariables['WB_STAGE2_RUNTIME'] = $runtimeFolder
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
    Write-Host 'The Stage 2 panel will appear in QGIS. No website publication occurs on this computer.'
    exit 0
} catch {
    Write-Host ('Could not start Stage 2: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

