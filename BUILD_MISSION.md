
# BUILD MISSION — ECHOESENGINE
This repository is the canonical source of truth for EchoesEngine.

Mission:
stabilize, build, and harden the real system. Do not produce decorative reports.

## Rules
- Do not mark anything DONE unless it actually compiles or runs.
- Prefer exact build errors over summaries.
- Ignore duplicate or obsolete roots outside this repository.
- Keep versioning coherent across CMake, manifests, engine version strings, and plugin metadata.
- Preserve working runtime paths only if they are still used by the current source tree.

## Priority order
1. Make CMake configure cleanly.
2. Build these targets in order:
   - EchoesEngine library
   - ECHoesEnginePro
   - EchoesStandalone
   - EchoesPlugin
3. Fix missing files, bad include paths, version mismatches, and broken build scripts.
4. Verify runtime pieces:
   - ApiServer
   - JobRegistry
   - OpenCL initialization
   - ffmpeg pathing
   - render output paths
5. Replace scaffolded/commented plugin sections with active compilable code.

## Required outputs
Agents/build tools must report only:
- exact target attempted
- exact command used
- exact first blocking error
- files changed
- what now compiles
- what still fails

## Never do this
- no fake HIGH confidence
- no DONE without compile/run proof
- no repeated scans of obsolete roots
- no vague summaries without technical evidence
