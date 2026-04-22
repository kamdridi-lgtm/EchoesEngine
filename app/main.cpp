#include "EchoesEngine/Engine.h"
#include <iostream>
#include <vector>
#include <algorithm>
#include <cmath>

int main() {
    std::cout << "============================================\n";
    std::cout << "  EchoesEngine -- Hard Rock Vocal Processor\n";
    std::cout << "  Phase 1+2: DSP Chain + AI Interface\n";
    std::cout << "============================================\n\n";

    // Configure
    echoes::EchoesEngine::Config cfg;
    cfg.sampleRate    = 44100.0f;
    cfg.blockSize     = 512;
    cfg.numChannels   = 2;
    cfg.inputGainDb   = +6.0f;   // +6dB input boost
    cfg.gateEnabled   = true;
    cfg.compEnabled   = true;
    cfg.eqEnabled     = true;
    cfg.aiEnabled     = true;    // stub backend (pass-through) until model loaded

    echoes::EchoesEngine engine(cfg);

    if (!engine.init()) {
        std::cerr << "[main] Engine init failed.\n";
        return 1;
    }

    // Register stats callback
    engine.setStatsCallback([](const echoes::EchoesEngine::Stats& s) {
        static int count = 0;
        if (++count % 10 == 0) { // Print every 10 blocks
            std::cout << "[Stats] in:" << s.inputPeakDb << "dB"
                      << " | gate:" << (s.gateOpen ? "OPEN" : "CLOSED")
                      << " | GR:" << s.compressionDb << "dB"
                      << " | out:" << s.outputPeakDb << "dB"
                      << " | AI:[" << s.aiBackend << "]\n";
        }
    });

    // Simulate 100 blocks of hard rock vocal signal
    constexpr int kFrames   = 512;
    constexpr int kChannels = 2;
    constexpr int kSamples  = kFrames * kChannels;
    constexpr float kSr     = 44100.0f;
    constexpr float kFreq   = 196.0f;  // G3 — typical male vocal root

    std::vector<float> buffer(kSamples);

    std::cout << "\n[main] Processing 100 blocks of simulated vocal signal...\n";

    for (int block = 0; block < 100; ++block) {
        float t0 = static_cast<float>(block * kFrames) / kSr;

        for (int f = 0; f < kFrames; ++f) {
            float t = t0 + static_cast<float>(f) / kSr;

            // Simulate a vocal phrase: on for 60 blocks, off for 40 (gate test)
            float env = (block < 60) ? 0.8f : 0.0f;

            float vocal = env * (
                0.6f * std::sin(2.0f * 3.14159f * kFreq * t) +         // fundamental
                0.3f * std::sin(2.0f * 3.14159f * kFreq * 2 * t) +     // 2nd harmonic
                0.1f * std::sin(2.0f * 3.14159f * kFreq * 3 * t)       // 3rd harmonic
            );

            buffer[f * 2 + 0] = vocal;  // L
            buffer[f * 2 + 1] = vocal;  // R
        }

        engine.process(buffer.data(), kSamples, kChannels);
    }

    auto stats = engine.getStats();
    std::cout << "\n[main] Final stats:\n";
    std::cout << "  Input peak  : " << stats.inputPeakDb   << " dB\n";
    std::cout << "  Output peak : " << stats.outputPeakDb  << " dB\n";
    std::cout << "  Gate state  : " << (stats.gateOpen ? "OPEN" : "CLOSED") << "\n";
    std::cout << "  Compression : " << stats.compressionDb  << " dB GR\n";
    std::cout << "  AI backend  : " << stats.aiBackend << "\n";

    engine.shutdown();

    std::cout << "\n[main] Done. Chain: GainStage -> Gate -> Comp -> EQ(5-band) -> AI -> Output\n";
    return 0;
}
