/**
 * standalone_processor.cpp
 *
 * Hard Rock Vocal Processor — Standalone Application
 *
 * Processes audio files directly (no DAW needed):
 *   EchoesEngine.exe input.wav output.wav [options]
 *
 * Full signal chain:
 *   GainStage → NoiseGate → Compressor → VocalEQ → AIVoiceProcessor → Output
 *
 * Future: real-time ASIO mode via PortAudio / JUCE AudioDeviceManager
 */

#include "EchoesEngine/Engine.h"
#include "EchoesEngine/audio/AudioBuffer.h"

#include <iostream>
#include <fstream>
#include <filesystem>
#include <string>
#include <vector>
#include <chrono>
#include <algorithm>
#include <cstring>
#include <cmath>

namespace fs = std::filesystem;

// ── WAV helpers ───────────────────────────────────────────────────────────────
// Minimal WAV reader/writer — no external dependency

#pragma pack(push,1)
struct WavHeader {
    char     riff[4]      = {'R','I','F','F'};
    uint32_t chunkSize    = 0;
    char     wave[4]      = {'W','A','V','E'};
    char     fmt[4]       = {'f','m','t',' '};
    uint32_t fmtSize      = 16;
    uint16_t audioFormat  = 3;   // 3 = IEEE float
    uint16_t channels     = 2;
    uint32_t sampleRate   = 44100;
    uint32_t byteRate     = 0;
    uint16_t blockAlign   = 0;
    uint16_t bitsPerSample= 32;
    char     data[4]      = {'d','a','t','a'};
    uint32_t dataSize     = 0;
};
#pragma pack(pop)

bool writeWav(const std::string& path, const echoes::AudioBuffer& buf) {
    std::ofstream f(path, std::ios::binary);
    if (!f) return false;

    WavHeader hdr;
    hdr.channels      = static_cast<uint16_t>(buf.channels());
    hdr.sampleRate    = buf.sampleRate();
    hdr.bitsPerSample = 32;
    hdr.audioFormat   = 3;   // IEEE float
    hdr.blockAlign    = static_cast<uint16_t>(buf.channels() * 4);
    hdr.byteRate      = buf.sampleRate() * buf.channels() * 4;
    hdr.dataSize      = static_cast<uint32_t>(buf.totalSamples() * 4);
    hdr.chunkSize     = 36 + hdr.dataSize;

    f.write(reinterpret_cast<const char*>(&hdr), sizeof(hdr));
    f.write(reinterpret_cast<const char*>(buf.data()),
            static_cast<std::streamsize>(buf.totalSamples() * sizeof(float)));
    return true;
}

echoes::AudioBuffer readWavFloat(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("Cannot open: " + path);

    // Find 'data' chunk
    char id[4]; uint32_t sz;
    f.read(id, 4); f.read(reinterpret_cast<char*>(&sz), 4); // RIFF
    f.read(id, 4);  // WAVE

    uint16_t channels = 2, fmt = 3, bps = 32;
    uint32_t sr = 44100;

    while (f.read(id, 4) && f.read(reinterpret_cast<char*>(&sz), 4)) {
        if (std::memcmp(id, "fmt ", 4) == 0) {
            f.read(reinterpret_cast<char*>(&fmt), 2);
            f.read(reinterpret_cast<char*>(&channels), 2);
            f.read(reinterpret_cast<char*>(&sr), 4);
            f.seekg(6, std::ios::cur);
            f.read(reinterpret_cast<char*>(&bps), 2);
            if (sz > 16) f.seekg(sz - 16, std::ios::cur);
        } else if (std::memcmp(id, "data", 4) == 0) {
            uint32_t frames = sz / (channels * (bps / 8));
            echoes::AudioBuffer buf(channels, frames, sr);

            if (fmt == 3 && bps == 32) {
                // IEEE float — read directly
                f.read(reinterpret_cast<char*>(buf.data()),
                       static_cast<std::streamsize>(sz));
            } else if (fmt == 1 && bps == 16) {
                // PCM 16-bit — convert to float
                std::vector<int16_t> pcm(frames * channels);
                f.read(reinterpret_cast<char*>(pcm.data()),
                       static_cast<std::streamsize>(frames * channels * 2));
                for (size_t i = 0; i < pcm.size(); ++i)
                    buf.data()[i] = pcm[i] / 32768.0f;
            } else {
                throw std::runtime_error("Unsupported WAV format (use float32 or PCM16)");
            }
            return buf;
        } else {
            f.seekg(sz, std::ios::cur);
        }
    }
    throw std::runtime_error("No data chunk found in WAV");
}

// ── CLI ───────────────────────────────────────────────────────────────────────
struct CliArgs {
    std::string inputPath;
    std::string outputPath;
    float   inputGainDb      =  0.0f;
    float   outputGainDb     =  0.0f;
    float   gateThresholdDb  = -40.0f;
    float   compThresholdDb  = -18.0f;
    float   compRatio        =  4.0f;
    bool    gateEnabled      = true;
    bool    compEnabled      = true;
    bool    eqEnabled        = true;
    bool    aiEnabled        = false;  // off by default (needs model)
    std::string aiModelPath;
    bool    verbose          = false;
};

CliArgs parseArgs(int argc, char* argv[]) {
    CliArgs a;
    if (argc < 3) {
        std::cout << "\nUsage: EchoesEngine <input.wav> <output.wav> [options]\n\n"
                     "Options:\n"
                     "  --input-gain  <dB>    Input gain (-60 to +24)    default: 0\n"
                     "  --output-gain <dB>    Output gain                default: 0\n"
                     "  --gate-thresh <dB>    Noise gate threshold       default: -40\n"
                     "  --comp-thresh <dB>    Compressor threshold       default: -18\n"
                     "  --comp-ratio  <x>     Compression ratio          default: 4.0\n"
                     "  --no-gate             Disable noise gate\n"
                     "  --no-comp             Disable compressor\n"
                     "  --no-eq               Disable 5-band EQ\n"
                     "  --ai <model.onnx>     Enable AI voice (stub now)\n"
                     "  -v / --verbose        Verbose logging\n\n"
                     "Example (Hard Rock preset):\n"
                     "  EchoesEngine vocal_dry.wav vocal_processed.wav \\\n"
                     "    --input-gain 6 --gate-thresh -35 --comp-thresh -20\n\n";
        throw std::runtime_error("Usage error");
    }
    a.inputPath  = argv[1];
    a.outputPath = argv[2];
    for (int i = 3; i < argc; ++i) {
        std::string arg = argv[i];
        if      (arg == "--input-gain"  && i+1<argc) a.inputGainDb     = std::stof(argv[++i]);
        else if (arg == "--output-gain" && i+1<argc) a.outputGainDb    = std::stof(argv[++i]);
        else if (arg == "--gate-thresh" && i+1<argc) a.gateThresholdDb = std::stof(argv[++i]);
        else if (arg == "--comp-thresh" && i+1<argc) a.compThresholdDb = std::stof(argv[++i]);
        else if (arg == "--comp-ratio"  && i+1<argc) a.compRatio       = std::stof(argv[++i]);
        else if (arg == "--no-gate")  a.gateEnabled = false;
        else if (arg == "--no-comp")  a.compEnabled = false;
        else if (arg == "--no-eq")    a.eqEnabled   = false;
        else if (arg == "--ai" && i+1<argc) { a.aiEnabled = true; a.aiModelPath = argv[++i]; }
        else if (arg == "-v" || arg == "--verbose") a.verbose = true;
    }
    return a;
}

// ── Main ─────────────────────────────────────────────────────────────────────
int main(int argc, char* argv[]) {
    std::cout << "\n╔════════════════════════════════════════╗\n"
                 "║  EchoesEngine™ — Standalone Processor  ║\n"
                 "╚════════════════════════════════════════╝\n\n";

    CliArgs args;
    try {
        args = parseArgs(argc, argv);
    } catch (...) {
        return 1;
    }

    const auto t0 = std::chrono::steady_clock::now();

    std::cout << "[1/4] Loading: " << args.inputPath << "\n";
    echoes::AudioBuffer buffer;
    try {
        buffer = readWavFloat(args.inputPath);
    } catch (const std::exception& e) {
        std::cerr << "[ERROR] " << e.what() << "\n";
        std::cerr << "  Tip: Convert to 32-bit float WAV:\n";
        std::cerr << "       ffmpeg -i input.mp3 -c:a pcm_f32le output.wav\n";
        return 3;
    }

    std::cout << "  " << buffer.channels() << "ch | "
              << buffer.sampleRate() << " Hz | "
              << buffer.durationSeconds() << "s | "
              << "Peak: " << buffer.peakDb() << " dBFS\n";

    // ── Configure engine ──────────────────────────────────────────────────────
    echoes::EchoesEngine::Config cfg;
    cfg.sampleRate    = static_cast<float>(buffer.sampleRate());
    cfg.blockSize     = 512;
    cfg.numChannels   = buffer.channels();
    cfg.inputGainDb   = args.inputGainDb;
    cfg.outputGainDb  = args.outputGainDb;
    cfg.gateEnabled   = args.gateEnabled;
    cfg.compEnabled   = args.compEnabled;
    cfg.eqEnabled     = args.eqEnabled;
    cfg.aiEnabled     = args.aiEnabled;
    cfg.voiceModelPath= args.aiModelPath;

    echoes::EchoesEngine engine(cfg);

    std::cout << "\n[2/4] Initializing signal chain...\n";
    if (!engine.init()) {
        std::cerr << "[ERROR] Engine initialization failed.\n";
        return 2;
    }
    engine.setGateThreshold(args.gateThresholdDb);
    engine.setCompThreshold(args.compThresholdDb);
    engine.setCompRatio(args.compRatio);

    std::cout << "  Chain: GainStage → NoiseGate → Compressor → VocalEQ(5b) → AI → Output\n";
    std::cout << "  Source format: " << buffer.sampleRate() << " Hz | "
              << buffer.channels() << "ch\n";
    std::cout << "  Input gain: "   << args.inputGainDb   << " dB"
              << "  | Gate: "      << args.gateThresholdDb << " dB"
              << "  | Comp: "      << args.compThresholdDb << "dB " << args.compRatio << ":1\n";

    // ── Load audio ────────────────────────────────────────────────────────────

    // ── Process in blocks ─────────────────────────────────────────────────────
    std::cout << "\n[3/4] Processing " << buffer.frames() << " frames...\n";

    const uint32_t blockSize = cfg.blockSize;
    const uint32_t totalFrames = buffer.frames();
    uint32_t processed = 0;
    echoes::AudioBuffer block(buffer.channels(), blockSize, buffer.sampleRate());

    // Process in blockSize chunks
    for (uint32_t offset = 0; offset < totalFrames; offset += blockSize) {
        uint32_t count = std::min(blockSize, totalFrames - offset);

        // Create a slice view (copy for now — zero-copy slice is Phase 4)
        block.resize(buffer.channels(), count, buffer.sampleRate());
        for (uint32_t f = 0; f < count; ++f)
            for (uint32_t ch = 0; ch < buffer.channels(); ++ch)
                block.at(f, ch) = buffer.at(offset + f, ch);

        engine.process(block);

        // Write back
        for (uint32_t f = 0; f < count; ++f)
            for (uint32_t ch = 0; ch < buffer.channels(); ++ch)
                buffer.at(offset + f, ch) = block.at(f, ch);

        processed += count;

        // Progress bar
        int pct = static_cast<int>((processed * 100) / totalFrames);
        if (args.verbose || processed % (blockSize * 100) < blockSize) {
            std::cout << "\r  [";
            int fill = pct / 5;
            for (int i = 0; i < 20; ++i) std::cout << (i < fill ? "█" : "░");
            std::cout << "] " << pct << "%  " << std::flush;
        }
    }
    std::cout << "\r  [████████████████████] 100%  \n";

    // ── Final stats ───────────────────────────────────────────────────────────
    auto stats = engine.getStats();
    std::cout << "\n  Output peak    : " << stats.outputPeakDb  << " dBFS\n";
    std::cout << "  Compression GR : " << stats.compressionDb << " dB\n";
    std::cout << "  Gate state     : " << (stats.gateOpen ? "OPEN" : "CLOSED") << "\n";
    std::cout << "  AI backend     : " << stats.aiBackend << "\n";

    // ── Export ────────────────────────────────────────────────────────────────
    std::cout << "\n[4/4] Writing: " << args.outputPath << "\n";
    if (!writeWav(args.outputPath, buffer)) {
        std::cerr << "[ERROR] Failed to write output WAV\n";
        return 4;
    }

    auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - t0).count();
    float rt = (buffer.durationSeconds() * 1000.0f) / static_cast<float>(elapsed);

    std::cout << "\n  ✓ Done in " << elapsed / 1000.0f << "s"
              << " (RT factor: " << rt << "x)\n"
              << "  Output: " << args.outputPath << "\n\n";

    engine.shutdown();
    return 0;
}
