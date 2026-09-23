# run_weekly_email.ps1
# Sends the weekly subscriber email every Thursday at 7pm.
# Uses the film cache built by the 8am pipeline — no re-scraping.
#
# ONE-TIME TASK SCHEDULER SETUP
# Open PowerShell as Administrator and run:
#
#   $action  = New-ScheduledTaskAction `
#                -Execute "powershell.exe" `
#                -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"C:\Users\TV-watchers\film-email\run_weekly_email.ps1`""
#   $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Thursday -At 7:00PM
#   $settings = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable
#   Register-ScheduledTask -TaskName "WhatsonMovieWeeklyEmail" `
#     -Action $action -Trigger $trigger -Settings $settings `
#     -Description "Send weekly whatson.movie subscriber email every Thursday at 7pm" `
#     -RunLevel Highest
#
# To run manually (bypasses Thursday check): $env:FORCE_EMAIL="1"; .\run_weekly_email.ps1

$RepoDir = $PSScriptRoot
$LogDir  = Join-Path $RepoDir "logs"
$LogFile = Join-Path $LogDir "weekly_email.log"
$EnvFile = Join-Path $RepoDir ".env"
$Python  = "C:\Users\TV-watchers\AppData\Local\Python\bin\python3.12.exe"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

function Write-Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

# Rotate log if > 2 MB
if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 2MB) {
    $archive = $LogFile -replace '\.log$', ("_" + (Get-Date -Format "yyyyMMdd") + ".log")
    Move-Item $LogFile $archive
    Write-Log "Log rotated to $archive"
}

Write-Log "=== Weekly email starting ==="

# Load .env
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | Where-Object { $_ -match '^\s*[^#\s]' -and $_ -match '=' } | ForEach-Object {
        $key, $val = $_ -split '=', 2
        $key = $key.Trim()
        $val = $val.Trim().Trim('"').Trim("'")
        if ($key) { [System.Environment]::SetEnvironmentVariable($key, $val, 'Process') }
    }
} else {
    Write-Log "WARNING: No .env file found at $EnvFile"
}

# Force UTF-8 output so any unicode in log lines doesn't crash
$env:PYTHONIOENCODING = "utf-8"

Set-Location $RepoDir

$output   = & $Python send_weekly_email.py 2>&1
$exitCode = $LASTEXITCODE

$output | ForEach-Object { Write-Log "  $_" }

if ($exitCode -ne 0) {
    Write-Log "ERROR: send_weekly_email.py exited with code $exitCode"
    exit $exitCode
}

Write-Log "=== Weekly email complete ==="
