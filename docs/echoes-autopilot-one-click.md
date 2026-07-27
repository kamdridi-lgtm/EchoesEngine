# Echoes Autopilot — one-click Windows package

## Use

1. Extract `Echoes-Autopilot-OneClick-Windows.zip`.
2. Put songs in `SONGS-TO-ANALYZE` or later in `D:\A.I\EchoesInbox`.
3. Double-click `START-ECHOES-AUTOPILOT.cmd`.

The first run installs the locked local runtime, provisions the pinned Silero model, creates the inbox/results/control folders, installs a five-minute scheduled scan when Windows permits it, and immediately analyzes available songs.

## Folders

- `D:\A.I\EchoesAutopilot` — controller and policy.
- `D:\A.I\EchoesEngineRuntime` — isolated Python/ONNX runtime.
- `D:\A.I\EchoesInbox` — source files to analyze.
- `D:\A.I\EchoesResults` — per-song JSON, CSV and manifests.
- `D:\A.I\EchoesControl` — ledger, status, logs and control bundle.

## Control behavior

The controller identifies files by SHA-256, never deletes source audio, never uploads audio, prevents duplicate processing, retries failed analyses with a new attempt number, and creates `Echoes-Control-Bundle-Latest.zip` containing reports and timelines but no source audio.

A constrained policy may be refreshed from the EchoesEngine GitHub repository. The policy may enable/disable processing and set safe scan limits. It cannot authorize uploads, deletion, arbitrary commands, stem separation or GPU execution.

## Windows proof contract

The release workflow assembles a clean ZIP without Git metadata, places two differently named audio files with the same SHA-256 in the seed folder, installs the runtime in paths containing spaces, performs one real ONNX CPU analysis, rejects the duplicate by hash, runs the controller a second time without creating another job, preserves the source files and verifies that the control bundle contains no source audio.

## Current truth boundary

The package and its autonomous loop are proven on GitHub Windows runners only after the associated workflow passes. HP Omen installation, scheduled execution and Kam Dridi song analysis remain unproven until the package runs locally and its control bundle is inspected.
