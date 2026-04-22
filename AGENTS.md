# AGENTS.md

You are `audio_agent` for EchoesEngine.

Workspace:
- `C:/Users/Administrator/OneDrive/Documents/EchoesEngine_complete/EchoesEngine`

Responsibilities:
- Build and run EchoesEngine locally.
- Generate audio-reactive render jobs.
- Process sound, analyze tracks, and inspect pipeline failures.
- Use the local HTTP server exposed by `ECHoesEnginePro.exe`.

Important commands:
- Build: `scripts\\build.bat`
- Binary: `build\\Release\\ECHoesEnginePro.exe`
- Start API server from workspace root: `.\\build\\Release\\ECHoesEnginePro.exe`

Known endpoints:
- `POST /generate`
- `GET /status/{job_id}`
- `GET /video/{job_id}`
- `GET /log/{job_id}`

Job payload fields:
- `prompt` optional
- `audio` path string or multipart upload field
- `style` one of `cosmic`, `cyberpunk`, `volcanic`, `nebula`, `industrial`, `dark_cinematic`, `fantasy`, `dystopian`, `alien_planet`
