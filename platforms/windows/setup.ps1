param(
    [string] $CodexPath, # Accepted by older install commands.
    [string] $GuiPath
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
if (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $python = & py.exe -3 -c "import sys; assert sys.version_info >= (3,11); print(sys.executable)"
} else {
    $python = & python.exe -c "import sys; assert sys.version_info >= (3,11); print(sys.executable)"
}
if ($LASTEXITCODE -ne 0 -or -not $python) { throw "Python 3.11 or newer is required." }

$locationJson = & $python -B -c "import json, sys; sys.path.insert(0, sys.argv[1]); from runtime_paths import CODEX_HOME, runtime_dir; print(json.dumps([str(runtime_dir()), str(CODEX_HOME)]))" $repo
if ($LASTEXITCODE -ne 0 -or -not $locationJson) { throw "Could not resolve the collector data directory." }
$runtime, $codexHome = ConvertFrom-Json $locationJson
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$launcher = Join-Path $runtime "codex-monitor.exe"
$current = [Environment]::GetEnvironmentVariable("CODEX_CLI_PATH", "User")
if ($current -eq $launcher) {
    $backup = Join-Path $runtime "prior-codex-cli-path.txt"
    $restored = $null
    if (Test-Path -LiteralPath $backup) {
        $saved = [IO.File]::ReadAllText($backup).Trim()
        if ($saved -and $saved -ne $launcher) { $restored = $saved }
    }
    [Environment]::SetEnvironmentVariable("CODEX_CLI_PATH", $restored, "User")
}
if ($env:CODEX_CLI_PATH -eq $launcher) {
    $env:CODEX_CLI_PATH = [Environment]::GetEnvironmentVariable("CODEX_CLI_PATH", "User")
    if (-not $env:CODEX_CLI_PATH) {
        $env:CODEX_CLI_PATH = [Environment]::GetEnvironmentVariable("CODEX_CLI_PATH", "Machine")
    }
}
if ($GuiPath) { $GuiPath = (Resolve-Path $GuiPath).Path }

& $python -B (Join-Path $repo "manage.py") configure
if ($LASTEXITCODE -ne 0) { throw "Could not configure the local collector." }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$taskName = "Codex Session Monitor $($identity.User.Value)"
$collectorPython = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $collectorPython)) { $collectorPython = $python }
$runArgs = '-B "{0}" run --codex-home "{1}" --data-dir "{2}"' -f (Join-Path $repo "manage.py"), $codexHome, $runtime
$action = New-ScheduledTaskAction -Execute $collectorPython -Argument $runArgs -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval ([TimeSpan]::FromMinutes(1))
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq "Running") { Stop-ScheduledTask -TaskName $taskName }
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
& $python -B (Join-Path $repo "manage.py") stop
if ($LASTEXITCODE -ne 0) { throw "Could not replace the manually started collector." }
Start-ScheduledTask -TaskName $taskName
$running = $false
for ($attempt = 0; $attempt -lt 25; $attempt++) {
    $localStatus = & $python -B (Join-Path $repo "manage.py") site-status
    if ($LASTEXITCODE -eq 0 -and ($localStatus | ConvertFrom-Json).collectorRunning) {
        $running = $true
        break
    }
    Start-Sleep -Milliseconds 400
}
if (-not $running) { throw "The logon task was registered. Check the scheduled task and server.log in $runtime." }

Write-Output "Collector configured for Windows-native Codex. Restart the GUI to load it."
Write-Output "Registered and started the logon task $taskName."
if ($GuiPath) { Start-Process -FilePath $GuiPath }
