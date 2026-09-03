<#
.SYNOPSIS
  Run Kinder's browser checks against the live box and record the result.

  Two passes: the smoke test (every view paints, no console errors) and the
  full-flow regression (source -> clip -> Studio -> effect -> export -> MP4),
  both through the public URL — the path friends actually use.

  Register it once (adjust the time to taste):

    schtasks /Create /TN "Kinder nightly check" /SC DAILY /ST 03:30 `
      /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\mavin\Downloads\podcast-audiogram-studio\scripts\nightly-check.ps1"

  Results land in runtime\logs\nightly-YYYY-MM-DD.log and a Windows
  notification says so when something fails. Credentials come from
  environment variables so nothing sits in this file: KINDER_URL,
  KINDER_USER, KINDER_PASSWORD (set them for the account the task runs as).
#>
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$frontend = Join-Path $root "frontend"
$logDir = Join-Path $root "runtime\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("nightly-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

$base = if ($env:KINDER_URL) { $env:KINDER_URL } else { "https://kinder.skdcorp.com" }
$user = if ($env:KINDER_USER) { $env:KINDER_USER } else { "mujin" }
$pass = $env:KINDER_PASSWORD
if (-not $pass) {
  "$(Get-Date -Format s) KINDER_PASSWORD is not set; nothing checked" | Tee-Object -FilePath $log -Append
  exit 2
}

$failed = $false
"$(Get-Date -Format s) === smoke against $base ===" | Tee-Object -FilePath $log -Append
Push-Location $frontend
& node smoke.mjs --base-url $base --username $user --password $pass 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { $failed = $true }

# Safari's engine too: it hands pointer moves to the page faster than React
# renders, which is how a drag that passed in Chromium saved only its first
# pixel there (the cover-art crop Afiya reported).
"$(Get-Date -Format s) === smoke (webkit) against $base ===" | Tee-Object -FilePath $log -Append
& node smoke.mjs --engine webkit --base-url $base --username $user --password $pass 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { $failed = $true }

"$(Get-Date -Format s) === mobile smoke against $base ===" | Tee-Object -FilePath $log -Append
& node mobile-smoke.mjs --base-url $base --username $user --password $pass 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { $failed = $true }

"$(Get-Date -Format s) === regression against $base ===" | Tee-Object -FilePath $log -Append
& node regression.mjs --base-url $base --username $user --password $pass --source "Season 4" 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { $failed = $true }
Pop-Location

if ($failed) {
  "$(Get-Date -Format s) RESULT: FAILED" | Tee-Object -FilePath $log -Append
  try {
    # A toast, so a failure is seen without opening a log.
    Add-Type -AssemblyName System.Windows.Forms
    $icon = New-Object System.Windows.Forms.NotifyIcon
    $icon.Icon = [System.Drawing.SystemIcons]::Error
    $icon.Visible = $true
    $icon.ShowBalloonTip(15000, "Kinder nightly check failed", "See $log", [System.Windows.Forms.ToolTipIcon]::Error)
    Start-Sleep -Seconds 16
    $icon.Dispose()
  } catch {}
  exit 1
}
"$(Get-Date -Format s) RESULT: passed" | Tee-Object -FilePath $log -Append
exit 0
