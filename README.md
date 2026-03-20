# Echoes Engine™ — Scaffold

> Hard Rock Vocal Processing Engine — C++20 / MSVC / Windows 11

---

## Architecture

```
EchoesEngine/
├── CMakeLists.txt          ← Root build config (C++20, MSVC x64)
├── engine/
│   ├── CMakeLists.txt      ← Compiles as STATIC LIBRARY
│   ├── include/
│   │   └── EchoesEngine/
│   │       └── Engine.h    ← Public API (init / process / shutdown)
│   └── src/
│       └── Engine.cpp      ← Implementation (pImpl pattern)
├── app/
│   ├── CMakeLists.txt      ← Links engine, compiles EchoesEngine.exe
│   └── main.cpp            ← Test harness: sine wave → process()
├── bridge/                 ← (future) N-API Node.js addon
├── scripts/
│   └── build.bat           ← One-click build for Windows
└── README.md
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Windows 11 (64-bit) | — |
| Visual Studio 2022 | Community+ (Desktop C++ workload) |
| CMake | 3.20+ |

No external dependencies required for this scaffold.

---

## Build (Windows)

### Option A — One-click script
```bat
scripts\build.bat
```

### Option B — Manual
```bat
mkdir build
cd build
cmake -G "Visual Studio 17 2022" -A x64 ..
cmake --build . --config Release
```

**Output:** `build\bin\Release\EchoesEngine.exe`

---

## Run
```bat
build\bin\Release\EchoesEngine.exe
```

Expected output:
```
========================================
  Echoes Engine — Scaffold Test Harness
========================================

[EchoesEngine] Initializing EchoesEngine v0.1.0-scaffold...
[EchoesEngine] Initialized successfully.
[main] Simulated buffer: 1024 frames x 2 channels
[main] Peak sample (pre-process):  1.0
[main] Peak sample (post-process): 1.0
[EchoesEngine] Shutting down...
[EchoesEngine] Shutdown complete.

[main] Test complete. ✓
```

---

## Roadmap — What to wire in next

| Phase | Module | Notes |
|-------|--------|-------|
| 1 | `AudioPreprocessor` | Load WAV/FLAC, normalize, noise gate |
| 2 | `PitchContourExtractor` | PYIN algorithm, no GPU needed |
| 3 | `NeuralReplacementEngine` | ONNX Runtime (add dependency then) |
| 4 | `TensorRTAccelerator` | GPU inference via TensorRT |
| 5 | `MasteringSafeExporter` | True-peak limiter, FLAC/WAV export |
| 6 | Electron bridge | N-API addon in `/bridge` |

---

## Design Principles

- **pImpl pattern** — implementation details hidden from public headers
- **Static library** — engine is linked at compile time, no DLL hell
- **No heap allocations in hot path** (future: pre-allocated buffers)
- **C++20** — concepts, ranges, `std::jthread` when needed
