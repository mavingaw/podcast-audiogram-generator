# Podcast Audiogram Studio

Self-hosted audiogram studio scaffold based on `deep-research-report.md`.

This first build is a working appliance foundation:

- FastAPI backend with SQLite persistence under `runtime/config`
- React editor workspace with setup, login, uploads, projects, clip controls, GPU assignment, and render queue
- Background job worker for media analysis, placeholder transcription, model setup, and CPU placeholder rendering
- GPU inventory through `nvidia-smi` when NVIDIA devices are visible
- Docker and Unraid release files for the intended single-container deployment path

## Local Development

Backend:

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio\backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8080
```

Frontend:

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio\frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Database and Users

The app stores its local SQLite database in `runtime/config/app.db` by default. Tables are created automatically when the backend starts, and the first browser run shows an admin bootstrap form.

You can also initialize the database and create/reset an admin from PowerShell:

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio
.\scripts\init-db.ps1
.\scripts\create-admin.ps1 -Email admin@example.com -Password "change-this-password"
```

After signing in as an admin, use the **User database** panel in the WebUI to add users, create admins, and disable or re-enable accounts.

## Production Container

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio
docker build -t podcast-audiogram-studio:local .
docker run --rm -p 8080:8080 -v ${PWD}\runtime\config:/config -v ${PWD}\runtime\data:/data podcast-audiogram-studio:local
```

For Unraid with NVIDIA runtime, pass GPU capabilities through the container runtime and mount `/config` and `/data` to persistent shares.

## Current Scope

This is not yet the full Headliner/Adobe-parity implementation. The next engineering milestones are:

- Replace placeholder transcription with Faster-Whisper/CTranslate2 model execution
- Add real waveform peak generation and Wavesurfer-based timeline regions
- Expand scene-driven rendering beyond the current local FFmpeg waveform/caption MP4 renderer
- Add Alembic migrations instead of `create_all`
- Harden RSS ingestion with DNS/IP redirect policy and download limits
- Add Playwright coverage for the editor workflow

