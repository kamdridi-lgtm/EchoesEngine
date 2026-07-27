# Echoes local song activity runtime

This package installs the proven Silero VAD song-activity timeline runtime under `D:\A.I\EchoesEngineRuntime` on Windows.

## Install

From an EchoesEngine checkout:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-song-activity-runtime.ps1
```

The installer:

- requires Python 3.10 or 3.11;
- creates an isolated virtual environment;
- installs pinned `numpy`, `onnx`, and `onnxruntime` versions;
- downloads the official Silero VAD 6.2.1 wheel;
- verifies the wheel SHA-256, model SHA-256, model size, and MIT licence evidence;
- installs the runtime under `D:\A.I` by default;
- writes `runtime-manifest.json`.

The production installer blocks non-`D:` roots. `-AllowNonDDrive` exists only for controlled CI or testing.

## Analyze a real Kam Dridi song

WAV input:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\A.I\EchoesEngineRuntime\Analyze-EchoesSong.ps1" `
  -InputPath "D:\Music\War Machines.wav" `
  -DeclareUserSong
```

MP3, FLAC, M4A, AAC, or OGG input requires FFmpeg on `PATH`, or an explicit executable:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\A.I\EchoesEngineRuntime\Analyze-EchoesSong.ps1" `
  -InputPath "D:\Music\War Machines.mp3" `
  -FfmpegPath "D:\A.I\ffmpeg\bin\ffmpeg.exe" `
  -DeclareUserSong
```

Each run creates a unique job directory under `D:\A.I\EchoesEngineRuntime\jobs` containing:

- `analysis-run-manifest.json`;
- `timeline\song-activity-timeline.json`;
- `timeline\song-activity-timeline.csv`;
- analysis and optional FFmpeg logs;
- the normalized WAV when conversion was required.

Use `-ExpectedInputSha256` to require an exact source file. Reusing an existing `JobId` is blocked.

## Truth boundary

The runtime proves local CPU voice-activity analysis and editing timelines. It does not prove vocal isolation, instrumental stems, voice conversion, GPU or TensorRT inference, HP Omen execution, or autonomous execution until those actions are actually run and verified on the target machine.
