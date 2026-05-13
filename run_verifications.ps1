# run_verifications.ps1
# Finds unverified subscribers in Supabase and sends them a verification email.
# Designed to run every 15 minutes via Windows Task Scheduler.
#
# ONE-TIME TASK SCHEDULER SETUP
# Open PowerShell as Administrator and run:
#
#   $action  = New-ScheduledTaskAction `
#                -Execute "powershell.exe" `
#                -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"C:\Users\TV-watchers\film-email\run_verifications.ps1`""
#   $trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) -Once -At (Get-Date)
#   $settings = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable
#   Register-ScheduledTask -TaskName "WhatsonMovieVerifications" `
#     -Action $action -Trigger $trigger -Settings $settings `
#     -Description "Send verification emails to new whatson.movie subscribers every 15 min" `
#     -RunLevel Highest
#
# To run manually at any time: .\run_verifications.ps1

$RepoDir = $PSScriptRoot
$LogDir  = Join-Path $RepoDir "logs"
$LogFile = Join-Path $LogDir "verifications.log"
$EnvFile = Join-Path $RepoDir ".env"
$Python  = "C:\Users\TV-watchers\AppData\Local\Python\bin\python3.12.exe"

# Ensure logs dir exists
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

function Write-Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

# Rotate log if it exceeds 2 MB
if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 2MB) {
    $archive = $LogFile -replace '\.log$', ("_" + (Get-Date -Format "yyyyMMdd") + ".log")
    Move-Item $LogFile $archive
    Write-Log "Log rotated to $archive"
}

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
} else {
    Write-Log "WARNING: No .env file found at $EnvFile"
}

Set-Location $RepoDir

# Force UTF-8 output so emoji in print() doesn't crash on Windows cp1252
$env:PYTHONIOENCODING = "utf-8"

$output   = & $Python send_verifications.py 2>&1
$exitCode = $LASTEXITCODE

$output | ForEach-Object { Write-Log "  $_" }

if ($exitCode -ne 0) {
    Write-Log "ERROR: send_verifications.py exited with code $exitCode"
    exit $exitCode
}
