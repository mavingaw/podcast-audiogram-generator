#Requires -Version 5.1
<#
.SYNOPSIS
Import the licensed music and interface-effect packs into this installation.

.DESCRIPTION
Finds the Audio Asset Archive WAV volumes, its song index, and the JDSherbert UI
SFX pack in a downloads folder, then hands whatever it finds to the importer.
Files are copied into PAS_LIBRARY_DIR (default backend\runtime\data\library),
which is deliberately outside the repository: the packs licence use, not
redistribution. See docs\AUDIO_LIBRARY.md.

.PARAMETER DownloadsDir
Folder holding the extracted pack folders. Defaults to the current user's
Downloads.

.PARAMETER SfxFormat
Which of the formats the effect pack already ships to install. The pack's
licence forbids modification, so nothing is ever transcoded.

.PARAMETER ProbeDurations
Run ffprobe over tracks the song index does not cover. Slower, but fills in the
intro/loop halves and the effect cues.
#>
param(
  [string]$DownloadsDir = "$env:USERPROFILE\Downloads",
  [ValidateSet("ogg", "mp3", "m4a", "wav")]
  [string]$SfxFormat = "ogg",
  [switch]$ProbeDurations
)

$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\..\backend"

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Backend virtualenv not found. Run scripts\init-db.ps1 first."
}

if (-not (Test-Path $DownloadsDir)) {
  throw "Downloads folder not found: $DownloadsDir"
}

$arguments = @("-m", "app.cli.import_library")

$musicDirs = Get-ChildItem -Path $DownloadsDir -Directory -Filter "Audio_Asset_Archive_WAVs_*" |
  Sort-Object Name
foreach ($dir in $musicDirs) {
  $arguments += @("--music-dir", $dir.FullName)
}

# The .rtf keeps its accented characters intact; the .pdf is the fallback.
$songIndex = Join-Path $DownloadsDir "Audio_Asset_Archive_SONG_INDEX.rtf"
if (Test-Path $songIndex) {
  $arguments += @("--song-index", $songIndex)
} else {
  Write-Warning "Song index not found; tracks will be catalogued by filename only."
}

$sfxDir = Get-ChildItem -Path $DownloadsDir -Directory -Filter "*Ultimate UI SFX Pack*" |
  Select-Object -First 1
if ($null -ne $sfxDir) {
  $arguments += @("--sfx-dir", $sfxDir.FullName, "--sfx-format", $SfxFormat)
} else {
  Write-Warning "UI SFX pack not found; the interface will run without sound cues."
}

if ($musicDirs.Count -eq 0 -and $null -eq $sfxDir) {
  throw "No pack folders found under $DownloadsDir. Extract the archives first."
}

if ($ProbeDurations) {
  $arguments += "--probe-durations"
}

Write-Host "Importing $($musicDirs.Count) music volume(s) into the local library..."
$env:PYTHONIOENCODING = "utf-8"
& $python @arguments
if ($LASTEXITCODE -ne 0) {
  throw "Import failed with exit code $LASTEXITCODE."
}
