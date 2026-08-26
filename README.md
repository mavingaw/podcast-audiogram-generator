# Podcast Audiogram Studio

Self-hosted podcast video and audiogram studio for local creative work on Unraid or a desktop LAN.

This first build is a working appliance foundation:

- FastAPI backend with SQLite persistence under `runtime/config`
- React creator workspace with Home, Quick Create, Projects, Templates, Exports, and Studio Editor views
- Guided destination, source, transcript clip, and template workflow that creates the same editable Studio project
- Direct-manipulation canvas with draggable layers, safe-zone guide, layer visibility, property controls, and timeline playhead
- Background job worker for media analysis, local transcript fixture generation, and real FFmpeg audiogram rendering
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

## Current Scope and Limitations

The core local workflow is functional, but this is not yet full Headliner/Adobe parity. Current limitations are:

- Transcription currently uses a deterministic local fixture; Faster-Whisper/CTranslate2 model execution is the next AI milestone.
- Browser waveform bars are generated UI data; server-side peaks and a mature waveform library are the next timeline milestone.
- The renderer currently exports a real FFmpeg MP4 plus SRT/VTT/manifest from the canonical project scene; arbitrary canvas layers are not yet rendered server-side.
- SQLite tables are created automatically; Alembic migrations, richer RSS persistence, brand kits, variants, and cancellation remain follow-up work.

The app does not require a cloud AI API. The existing GPU panel stores UUID-based assignments and reports visible NVIDIA devices; the local development renderer remains CPU-first until the NVIDIA production image is enabled.

