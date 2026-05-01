# EchoesEngine Checkpoint - 2026-04-30

## Canonical local source folder
C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\EchoesEngine

This is the clean source folder the user previously provided. It contains the advanced C++/JUCE source tree and the MSVC 2026 build script.

## Evidence/render archive folder
C:\Users\Administrator\.openclaw\tmp\EchoesEngine-canonical

This folder is not a clean Git working tree, but it contains logs, prior builds, jobs, and a large render archive. At the time of inspection it contained 641 render job directories.

## GitHub repository
https://github.com/kamdridi-lgtm/EchoesEngine.git

GitHub main currently has BUILD_MISSION.md but does not contain all local runtime/daemon scripts. Branch codex-echoesengine-canonical has the advanced build.bat pattern.

## Added operational scripts
- scripts\start-server.bat: builds EchoesEngine and starts the best available executable.
- scripts\daemon.bat: 24/7 restart loop with runtime logs.
- scripts\autojob-agent.ps1: optional local job submitter for continuous generation.
- scripts\install-daemon-task.ps1: installs/starts Windows scheduled task EchoesEngineDaemon.

## Build output target
%USERPROFILE%\echoes-build\echoesengine-canonical-vs2026-release

Expected outputs:
- Release\EchoesEngine.lib
- Release\ECHoesEnginePro.exe
- Release\EchoesEngine.exe
- EchoesPlugin_artefacts\Release\Standalone\EchoesEngine.exe
- optional VST3 artifact

## Next priorities
1. Run scripts\build.bat and confirm compile proof.
2. Run scripts\start-server.bat and verify HTTP API on port 8080.
3. Install scripts\install-daemon-task.ps1 only after build/server success.
4. Push the canonical operational scripts to GitHub once local behavior is proven.
5. Keep the render archive; do not delete the partial clone/archive unless explicitly approved.

## 2026-04-30 verification update
- Build succeeded with Visual Studio 18 2026.
- ECHoesEnginePro.exe started and listened on 127.0.0.1:8080.
- /generate accepted a test job after default WAV files were added.
- FFmpeg 8.1 installed locally at C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\tools\ffmpeg\ffmpeg.exe.
- job-18 reached finished status and produced renders\job-18\video.mp4.
- start-server.bat now sets conservative 24/7 defaults: max 1 concurrent job, auto-job interval 300 seconds, duration 15 seconds.


## 2026-05-01 daemon correction
- Scheduled task initially ran as SYSTEM, so %USERPROFILE% pointed to systemprofile.
- scripts\build.bat was corrected to use fixed build directory C:\Users\Administrator\echoes-build\echoesengine-canonical-vs2026-release.


## Update - 2026-05-01 00:38:13 -04:00

- Recovered disk after a failed full GitHub clone of kamdridi-lgtm/EchoesEngine; the clone failed because the remote repository tracks heavy enders/ and jobs/ artifacts.
- Deleted only the temporary failed clone C:\tmp\EchoesEngine-main and restored roughly 17 GB free disk.
- Restarted EchoesEngineDaemon; server verified listening on 127.0.0.1:8080.
- Added .env.example with local runtime, autojob, FFmpeg, and future cloud/autopilot variables.
- Current blocker for GitHub sync: GitHub app write access returns 403, and normal clone is unsafe until remote heavy artifacts are removed or sparse checkout is used.
