param(
    [Parameter(Mandatory=$true)][string]$PythonExecutable,
    [Parameter(Mandatory=$true)][string]$CodeDirectory,
    [Parameter(Mandatory=$true)][string]$DatabasePath,
    [Parameter(Mandatory=$true)][string]$BackupDirectory,
    [string]$ApprovedOffserverHost = ''
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $BackupDirectory | Out-Null
$env:AMORINO_DB_PATH = $DatabasePath
$logPath = Join-Path $BackupDirectory 'backup-job.log'
try {
    & $PythonExecutable (Join-Path $CodeDirectory 'scripts/backup_db.py') --db $DatabasePath --dir $BackupDirectory --keep 14 *>> $logPath
    if ($LASTEXITCODE -ne 0) { throw 'SQLite backup failed' }
    & $PythonExecutable (Join-Path $CodeDirectory 'scripts/verify_backup.py') --dir $BackupDirectory --max-age-hours 26 *>> $logPath
    if ($LASTEXITCODE -ne 0) { throw 'Backup validation failed' }
    $planJson = & $PythonExecutable (Join-Path $CodeDirectory 'scripts/offserver_plan.py') --dir $BackupDirectory --host $ApprovedOffserverHost
    if ($LASTEXITCODE -ne 0) { throw 'Off-server receiver/backup validation failed' }
    $plan = $planJson | ConvertFrom-Json
    & scp -o BatchMode=yes $plan.source_path "$($plan.source_path).json" $plan.destination *>> $logPath
    if ($LASTEXITCODE -ne 0) { throw 'Off-server copy failed' }
    # Filename, host and checksum came from strict validation; no user text is
    # interpolated into the remote shell command.
    $remoteCheck = "python3 /opt/amorino-recovery/scripts/verify_backup.py --backup $($plan.remote_path) --expected-sha256 $($plan.expected_sha256) --max-age-hours 26"
    & ssh -o BatchMode=yes $plan.host $remoteCheck *>> $logPath
    if ($LASTEXITCODE -ne 0) { throw 'Off-server verification failed' }
    "$(Get-Date -Format o) off-server verification passed" | Set-Content -LiteralPath (Join-Path $BackupDirectory 'offserver-status')
    $failureMarker = Join-Path $BackupDirectory 'backup-failure'
    if (Test-Path -LiteralPath $failureMarker) { Remove-Item -LiteralPath $failureMarker }
    exit 0
} catch {
    "$(Get-Date -Format o) $($_.Exception.Message)" | Add-Content -LiteralPath $logPath
    "$(Get-Date -Format o) operator action required" | Set-Content -LiteralPath (Join-Path $BackupDirectory 'backup-failure')
    exit 1
}
