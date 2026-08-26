$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\..\backend"
if (-not (Test-Path ".venv")) {
  python -m venv .venv
}
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8080

