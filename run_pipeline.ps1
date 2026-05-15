# run_pipeline.ps1
# Valencia cinema listings - daily update script.
# Runs pipeline.py, then commits and pushes the updated docs/ to GitHub.
#
# ONE-TIME TASK SCHEDULER SETUP
# Open PowerShell as Administrator and run:
#
#   $action  = New-ScheduledTaskAction `
#                -Execute "powershell.exe" `
#                -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"C:\Users\TV-watchers\film-email\run_pipeline.ps1`""
#   $trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM
#   $settings = New-ScheduledTaskSettingsSet -WakeToRun -RunOnlyIfNetworkAvailable
#   Register-ScheduledTask -TaskName "WhatsonMoviePipeline" `
#     -Action $action -Trigger $trigger -Settings $settings `
#     -Description "Daily Valencia cinema listings update" `
#     -RunLevel Highest
#
# To run manually at any time: .\run_pipeline.ps1

$RepoDir = $PSScriptRoot          # directory where this script lives
$LogDir  = Join-Path $RepoDir "logs"
$LogFile = Join-Path $LogDir "pipeline.log"
$EnvFile = Join-Path $RepoDir ".env"
$Python  = "C:\Users\TV-watchers\AppData\Local\Python\bin\python3.12.exe"

# Logging
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

function Write-Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

# Rotate log if it exceeds 5 MB
if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 5MB) {
    $archive = $LogFile -replace '\.log$', ("_" + (Get-Date -Format "yyyyMMdd") + ".log")
    Move-Item $LogFile $archive
    Write-Log "Log rotated to $archive"
}

Write-Log "=== Pipeline starting ==="

# Load .env
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | Where-Object { $_ -match '^\s*[^#\s]' -and $_ -match '=' } | ForEach-Object {
        $key, $val = $_ -split '=', 2
        $key = $key.Trim()
        $val = $val.Trim().Trim('"').Trim("'")
        if ($key) {
            [System.Environment]::SetEnvironmentVariable($key, $val, 'Process')
        }
    }
    Write-Log "Loaded secrets from .env"
} else {
    Write-Log "WARNING: No .env file found at $EnvFile - API keys may be missing"
}

# Run pipeline
Set-Location $RepoDir
Write-Log "Running pipeline.py ..."

# Write pipeline output directly to log as it runs (not buffered)
$pyLog = Join-Path $LogDir "pipeline_py.log"
& $Python -u pipeline.py 2>&1 | Tee-Object -FilePath $pyLog -Append | ForEach-Object { Write-Log "  $_" }
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Log "ERROR: pipeline.py exited with code $exitCode - aborting git push"
    exit $exitCode
}

Write-Log "Pipeline succeeded."

# Commit and push docs/
git -C $RepoDir add docs/listings/ docs/data/ 2>&1 | ForEach-Object { Write-Log "git add: $_" }

# Check if there are staged changes
$staged = git -C $RepoDir diff --cached --name-only
if (-not $staged) {
    Write-Log "No changes in docs/ - nothing to commit."
    Write-Log "=== Done (no-op) ==="
    exit 0
}

$dateStr    = Get-Date -Format "yyyy-MM-dd"
$commitMsg  = "chore: update listings $dateStr [auto]"
git -C $RepoDir commit -m $commitMsg 2>&1 | ForEach-Object { Write-Log "git commit: $_" }

if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: git commit failed (code $LASTEXITCODE)"
    exit $LASTEXITCODE
}

git -C $RepoDir push 2>&1 | ForEach-Object { Write-Log "git push: $_" }

if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: git push failed (code $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Log "Pushed to GitHub - Pages will redeploy automatically."
Write-Log "=== Pipeline complete ==="
