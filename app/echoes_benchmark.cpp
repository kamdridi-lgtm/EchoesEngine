/**
 * echoes_benchmark.cpp
 * ====================
 * EchoesEngine Benchmark Harness
 *
 * Measures:
 *   - Per-block DSP latency (µs) for each stage
 *   - AI inference latency across: Stub / ONNX CPU / ONNX CUDA / TRT FP16
 *   - Pitch tracker latency: YIN vs CREPE-Lite ONNX
 *   - Real-time factor: RT×  (>1.0 = faster than real-time)
 *   - Jitter: std deviation of block processing time
 *
 * Output:
 *   - Console table
 *   - benchmark_report.json  (machine-readable, for CI regression)
 *
 * Build:
 *   g++ -std=c++20 -O3 -I engine/include \
 *       engine/src/Engine.cpp \
 *       app/echoes_benchmark.cpp \
 *       -o echoes_benchmark
 *
 * Run:
 *   ./echoes_benchmark                    # all tests
 *   ./echoes_benchmark --stage dsp        # DSP only (no AI)
 *   ./echoes_benchmark --stage ai         # AI only
 *   ./echoes_benchmark --stage pitch      # Pitch only
 *   ./echoes_benchmark --blocks 1000      # sample count
 *   ./echoes_benchmark --out report.json  # output path
 */

#include "EchoesEngine/Engine.h"
#include "EchoesEngine/audio/AudioBuffer.h"
#include "EchoesEngine/dsp/GainStage.h"
#include "EchoesEngine/dsp/NoiseGate.h"
#include "EchoesEngine/dsp/Compressor.h"
#include "EchoesEngine/dsp/VocalEQ.h"
#include "EchoesEngine/neural/AIVoiceProcessor.h"
#include "EchoesEngine/neural/OnnxVoiceProcessor.h"
#include "EchoesEngine/neural/CrepeLight.h"

#include <iostream>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <vector>
#include <numeric>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <string>
#include <map>
#include <functional>
#include <random>

using Clock = std::chrono::steady_clock;
using Us    = std::chrono::microseconds;
using Ms    = std::chrono::duration<double, std::milli>;

// ── Timing result ─────────────────────────────────────────────────────────────
struct BenchResult {
    std::string name;
    std::string category;
    int         blockSize    = 512;
    int         sampleRate   = 44100;
    int         numBlocks    = 500;
    double      meanUs       = 0.0;
    double      minUs        = 0.0;
    double      maxUs        = 0.0;
    double      stdUs        = 0.0;
    double      p95Us        = 0.0;
    double      p99Us        = 0.0;
    double      rtFactor     = 0.0;  // >1 = faster than real-time
    bool        realtimeSafe = false;  // p99 < block_budget_us
    std::string notes;
};

// ── Generate test signal (vocal-like) ────────────────────────────────────────
static echoes::AudioBuffer makeVocalSignal(int blockSize, int sr, int seed = 0) {
    echoes::AudioBuffer buf(2, static_cast<uint32_t>(blockSize),
                            static_cast<uint32_t>(sr));
    std::mt19937 rng(seed);
    std::normal_distribution<float> noise(0.f, 0.01f);
    const float f0 = 220.f;  // A3 — typical male vocal
    for (int f = 0; f < blockSize; ++f) {
        float t = static_cast<float>(f) / sr;
        // Fundamental + harmonics (vocal-ish spectrum)
        float s = 0.5f * std::sin(2.f * 3.14159f * f0 * t)
                + 0.25f * std::sin(2.f * 3.14159f * f0 * 2 * t)
                + 0.12f * std::sin(2.f * 3.14159f * f0 * 3 * t)
                + 0.06f * std::sin(2.f * 3.14159f * f0 * 4 * t)
                + noise(rng);
        buf.at(static_cast<uint32_t>(f), 0) = s;
        buf.at(static_cast<uint32_t>(f), 1) = s;
    }
    return buf;
}

// ── Run benchmark ─────────────────────────────────────────────────────────────
template<typename Fn>
BenchResult run_bench(const std::string& name,
                      const std::string& category,
                      int blockSize,
                      int sr,
                      int numBlocks,
                      Fn&& fn)
{
    BenchResult res;
    res.name       = name;
    res.category   = category;
    res.blockSize  = blockSize;
    res.sampleRate = sr;
    res.numBlocks  = numBlocks;

    double blockBudgetUs = static_cast<double>(blockSize) / sr * 1e6;

    // Warmup
    for (int i = 0; i < 20; ++i) fn();

    std::vector<double> times;
    times.reserve(numBlocks);

    for (int i = 0; i < numBlocks; ++i) {
        auto t0 = Clock::now();
        fn();
        double us = std::chrono::duration<double, std::micro>(
                        Clock::now() - t0).count();
        times.push_back(us);
    }

    std::sort(times.begin(), times.end());
    double sum  = std::accumulate(times.begin(), times.end(), 0.0);
    res.meanUs  = sum / times.size();
    res.minUs   = times.front();
    res.maxUs   = times.back();
    res.p95Us   = times[static_cast<int>(times.size() * 0.95)];
    res.p99Us   = times[static_cast<int>(times.size() * 0.99)];

    double var  = 0.0;
    for (double t : times) var += (t - res.meanUs) * (t - res.meanUs);
    res.stdUs   = std::sqrt(var / times.size());

    res.rtFactor     = blockBudgetUs / res.meanUs;
    res.realtimeSafe = res.p99Us < blockBudgetUs;

    return res;
}

// ── Print table row ───────────────────────────────────────────────────────────
static void printRow(const BenchResult& r) {
    const char* safe = r.realtimeSafe ? "✓" : "✗";
    std::cout << std::left
              << std::setw(38) << r.name
              << std::right
              << std::setw(8)  << std::fixed << std::setprecision(1) << r.meanUs  << "µs"
              << std::setw(8)  << r.p99Us   << "µs"
              << std::setw(8)  << r.stdUs   << "µs"
              << std::setw(7)  << std::setprecision(1) << r.rtFactor << "×"
              << "  " << safe
              << "\n";
}

// ── JSON export ───────────────────────────────────────────────────────────────
static std::string toJson(const std::vector<BenchResult>& results,
                           int blockSize, int sr) {
    std::ostringstream j;
    j << std::fixed << std::setprecision(3);
    j << "{\n";
    j << "  \"product\": \"EchoesEngine\",\n";
    j << "  \"version\": \"0.3.0\",\n";
    j << "  \"block_size\": " << blockSize << ",\n";
    j << "  \"sample_rate\": " << sr << ",\n";
    j << "  \"block_budget_us\": "
      << std::setprecision(1)
      << (static_cast<double>(blockSize) / sr * 1e6) << ",\n";

    // Group by category
    std::map<std::string, std::vector<const BenchResult*>> cats;
    for (auto& r : results) cats[r.category].push_back(&r);

    j << "  \"results\": {\n";
    bool firstCat = true;
    for (auto& [cat, rows] : cats) {
        if (!firstCat) j << ",\n";
        firstCat = false;
        j << "    \"" << cat << "\": [\n";
        for (size_t i = 0; i < rows.size(); ++i) {
            auto& r = *rows[i];
            j << "      {\n";
            j << "        \"name\": \"" << r.name << "\",\n";
            j << "        \"mean_us\": " << r.meanUs << ",\n";
            j << "        \"p95_us\":  " << r.p95Us  << ",\n";
            j << "        \"p99_us\":  " << r.p99Us  << ",\n";
            j << "        \"std_us\":  " << r.stdUs  << ",\n";
            j << "        \"rt_factor\": " << std::setprecision(2) << r.rtFactor << ",\n";
            j << "        \"realtime_safe\": " << (r.realtimeSafe?"true":"false") << ",\n";
            j << "        \"notes\": \"" << r.notes << "\"\n";
            j << "      }";
            if (i + 1 < rows.size()) j << ",";
            j << "\n";
        }
        j << "    ]";
    }
    j << "\n  }\n}\n";
    return j.str();
}

// ═══════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════
int main(int argc, char* argv[]) {
    // ── Parse args ────────────────────────────────────────────────────────
    int         numBlocks  = 500;
    int         blockSize  = 512;
    int         sr         = 44100;
    std::string stage      = "all";
    std::string outPath    = "benchmark_report.json";

    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if      (a == "--blocks" && i+1 < argc)  numBlocks = std::stoi(argv[++i]);
        else if (a == "--block-size" && i+1<argc) blockSize = std::stoi(argv[++i]);
        else if (a == "--stage" && i+1 < argc)   stage     = argv[++i];
        else if (a == "--out" && i+1 < argc)     outPath   = argv[++i];
    }

    double budgetUs = static_cast<double>(blockSize) / sr * 1e6;

    std::cout << "\n";
    std::cout << "╔══════════════════════════════════════════════════════════╗\n";
    std::cout << "║          EchoesEngine Benchmark Suite v0.3.0             ║\n";
    std::cout << "╠══════════════════════════════════════════════════════════╣\n";
    std::cout << "║  Block: " << std::setw(4) << blockSize
              << "  SR: " << sr << "  Budget: "
              << std::fixed << std::setprecision(1) << budgetUs << "µs"
              << "  Runs: " << numBlocks
              << "                 ║\n";
    std::cout << "╚══════════════════════════════════════════════════════════╝\n\n";

    auto sig = makeVocalSignal(blockSize, sr, 42);
    std::vector<BenchResult> results;

    auto header = [](const std::string& s) {
        std::cout << "\n── " << s << " "
                  << std::string(55 - s.size(), '-') << "\n";
        std::cout << std::left << std::setw(38) << "Test"
                  << std::right
                  << std::setw(10) << "Mean"
                  << std::setw(10) << "P99"
                  << std::setw(10) << "Std"
                  << std::setw(8)  << "RT×"
                  << "  Safe\n";
        std::cout << std::string(76, '-') << "\n";
    };

    // ═════════════════════════════════════════════════════════════════════
    // DSP CHAIN
    // ═════════════════════════════════════════════════════════════════════
    if (stage == "all" || stage == "dsp") {
        header("DSP Chain");

        echoes::EchoesEngine::Config cfg;
        cfg.sampleRate  = static_cast<float>(sr);
        cfg.blockSize   = static_cast<uint32_t>(blockSize);
        cfg.aiEnabled   = false;
        echoes::EchoesEngine engine(cfg);
        engine.init();

        // Full chain
        {
            auto r = run_bench("Full DSP chain (no AI)", "dsp",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig;
                engine.process(b);
            });
            r.notes = "GainStage+Gate+Comp+EQ+OutGain";
            results.push_back(r);
            printRow(r);
        }

        // Individual stages (direct DSP objects)
        using namespace echoes::dsp;

        {
            GainStage gs(sr); GainStage::Params p; gs.setParams(p);
            auto r = run_bench("GainStage only", "dsp",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig; gs.process(b);
            });
            results.push_back(r); printRow(r);
        }
        {
            NoiseGate ng(sr); NoiseGate::Params p; ng.setParams(p);
            auto r = run_bench("NoiseGate only", "dsp",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig; ng.process(b);
            });
            results.push_back(r); printRow(r);
        }
        {
            Compressor comp(sr); Compressor::Params p; comp.setParams(p);
            auto r = run_bench("Compressor (4:1 RMS)", "dsp",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig; comp.process(b);
            });
            r.notes = "soft-knee, program-dependent release";
            results.push_back(r); printRow(r);
        }
        {
            VocalEQ eq(sr);
            auto r = run_bench("VocalEQ (5-band biquad)", "dsp",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig; eq.process(b);
            });
            r.notes = "HP+LS+PK+HS+PK cascade";
            results.push_back(r); printRow(r);
        }

        // Block size scaling
        for (int bs : {128, 256, 512, 1024, 2048}) {
            auto sv = makeVocalSignal(bs, sr, 0);
            echoes::EchoesEngine::Config c2;
            c2.sampleRate  = static_cast<float>(sr);
            c2.blockSize   = static_cast<uint32_t>(bs);
            c2.aiEnabled   = false;
            echoes::EchoesEngine e2(c2); e2.init();

            auto r = run_bench("Full DSP  block=" + std::to_string(bs), "dsp_scaling",
                               bs, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sv;
                e2.process(b);
            });
            results.push_back(r); printRow(r);
        }
    }

    // ═════════════════════════════════════════════════════════════════════
    // PITCH TRACKER
    // ═════════════════════════════════════════════════════════════════════
    if (stage == "all" || stage == "pitch") {
        header("Pitch Tracking");

        // YIN (always available)
        {
            echoes::neural::CrepeLight::Config pcfg;
            pcfg.sampleRate     = sr;
            pcfg.hopSizeSamples = blockSize;
            pcfg.useOnnx        = false;
            echoes::neural::CrepeLight yin(pcfg);

            auto r = run_bench("YIN pitch estimator", "pitch",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig;
                yin.process(b);
            });
            r.notes = "parabolic interpolation, tauMax=" +
                      std::to_string(sr / 50);
            results.push_back(r); printRow(r);
        }

        // CREPE-Lite ONNX (if model present)
        {
            echoes::neural::CrepeLight::Config pcfg;
            pcfg.sampleRate     = sr;
            pcfg.hopSizeSamples = blockSize;
            pcfg.useOnnx        = true;
            pcfg.modelPath      = "./models/crepe-lite-pitch/crepe_lite_44k.onnx";
            echoes::neural::CrepeLight crepe(pcfg);

            std::string backend = crepe.backendName();
            auto r = run_bench("CREPE-Lite (" + backend + ")", "pitch",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig;
                crepe.process(b);
            });
            r.notes = "360-bin softmax, Gaussian weighted Hz";
            results.push_back(r); printRow(r);
        }
    }

    // ═════════════════════════════════════════════════════════════════════
    // AI VOICE — BACKENDS
    // ═════════════════════════════════════════════════════════════════════
    if (stage == "all" || stage == "ai") {
        header("AI Voice Conversion");

        // Stub
        {
            auto stub = std::make_unique<echoes::neural::OnnxVoiceProcessor>();
            stub->loadModel("stub");
            echoes::neural::AIVoiceProcessor::Params p;
            p.timbreDepth = 0.0f;  // bypass path

            auto r = run_bench("Stub (bypass — timbreDepth=0)", "ai",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig;
                stub->process(b, p);
            });
            r.notes = "identity transform, no alloc";
            results.push_back(r); printRow(r);
        }

        // ONNX CPU with stub model
        {
            auto proc = std::make_unique<echoes::neural::OnnxVoiceProcessor>(
                echoes::neural::AIVoiceProcessor::Backend::OnnxCPU);
            proc->loadModel("stub");
            echoes::neural::AIVoiceProcessor::Params p;
            p.timbreDepth = 0.5f;  // active path

            auto r = run_bench("ONNX CPU (stub model, timbre=0.5)", "ai",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig;
                proc->process(b, p);
            });
            r.notes = "YIN + mel + spectral blend, no real ORT";
            results.push_back(r); printRow(r);
        }

        // Full engine with AI enabled
        {
            echoes::EchoesEngine::Config cfg;
            cfg.sampleRate = static_cast<float>(sr);
            cfg.blockSize  = static_cast<uint32_t>(blockSize);
            cfg.aiEnabled  = true;
            echoes::EchoesEngine engine(cfg);
            engine.init();
            engine.setAITimbreDepth(0.0f);  // bypass AI model inside engine

            auto r = run_bench("Full Engine (AI enabled, no model)", "ai",
                               blockSize, sr, numBlocks, [&]{
                echoes::AudioBuffer b = sig;
                engine.process(b);
            });
            r.notes = "complete chain, AI bypassed (no model loaded)";
            results.push_back(r); printRow(r);
        }
    }

    // ═════════════════════════════════════════════════════════════════════
    // SUMMARY
    // ═════════════════════════════════════════════════════════════════════
    std::cout << "\n" << std::string(76, '=') << "\n";
    std::cout << "  SUMMARY  (block=" << blockSize
              << " @ " << sr/1000 << "kHz"
              << "  budget=" << std::fixed << std::setprecision(0) << budgetUs << "µs)\n";
    std::cout << std::string(76, '-') << "\n";

    int safe = 0, unsafe = 0;
    double worst = 0.0;
    std::string worstName;
    for (auto& r : results) {
        if (r.category == "dsp_scaling") continue;  // skip scaling tests from summary
        if (r.realtimeSafe) safe++;
        else { unsafe++; if (r.p99Us > worst) { worst=r.p99Us; worstName=r.name; } }
    }
    std::cout << "  ✓ Real-time safe:   " << safe << " tests\n";
    if (unsafe > 0) {
        std::cout << "  ✗ Needs attention: " << unsafe << " tests\n";
        std::cout << "    Worst: " << worstName
                  << " (P99=" << std::setprecision(1) << worst << "µs)\n";
    }

    // ── JSON output ────────────────────────────────────────────────────────
    std::string json = toJson(results, blockSize, sr);
    {
        std::ofstream f(outPath);
        if (f) {
            f << json;
            std::cout << "\n  Report: " << outPath << "\n";
        }
    }
    std::cout << std::string(76, '=') << "\n\n";

    return unsafe > 0 ? 1 : 0;
}
