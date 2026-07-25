#include "EchoesEngine/NaturalnessScorer.h"
#include "EchoesEngine/audio/AudioBuffer.h"

#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

constexpr float kPi = 3.14159265358979323846f;
constexpr uint32_t kSampleRate = 44100;
constexpr uint32_t kFrames = 4410; // 100 ms; exactly 22 cycles at 220 Hz.

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

echoes::AudioBuffer makeStereo(float amplitude, float phaseRadians, float frequencyHz = 220.0f) {
    echoes::AudioBuffer buffer(2, kFrames, kSampleRate);
    for (uint32_t frame = 0; frame < kFrames; ++frame) {
        const float t = static_cast<float>(frame) / static_cast<float>(kSampleRate);
        const float angle = 2.0f * kPi * frequencyHz * t;
        buffer.at(frame, 0) = amplitude * std::sin(angle);
        buffer.at(frame, 1) = amplitude * std::sin(angle + phaseRadians);
    }
    return buffer;
}

void primeHealthyBaseline(echoes::NaturalnessScorer& scorer) {
    for (int index = 0; index < 24; ++index) {
        const float amplitude = (index % 2 == 0) ? 0.12f : 0.50f;
        auto buffer = makeStereo(amplitude, kPi / 3.0f); // correlation ~= 0.5
        scorer.analyze(buffer);
    }
}

} // namespace

int main() {
    try {
        echoes::NaturalnessScorer healthyScorer(static_cast<float>(kSampleRate));
        primeHealthyBaseline(healthyScorer);
        const auto healthy = healthyScorer.lastScore();
        require(healthy.stereoHealth > 0.95f, "healthy stereo was not rated healthy");
        require(healthy.monoSafety > 0.99f, "healthy stereo was not mono safe");
        require(healthy.overall > 0.60f, "healthy dynamic stereo overall score was unexpectedly low");

        echoes::NaturalnessScorer monoScorer(static_cast<float>(kSampleRate));
        const auto mono = monoScorer.analyze(makeStereo(0.35f, 0.0f));
        require(mono.stereoHealth < 0.05f, "mono signal was not identified as overly correlated");
        require(mono.monoSafety > 0.99f, "mono signal was incorrectly declared mono unsafe");

        echoes::NaturalnessScorer wideScorer(static_cast<float>(kSampleRate));
        const auto wide = wideScorer.analyze(makeStereo(0.35f, 80.0f * kPi / 180.0f));
        require(wide.stereoHealth > 0.40f && wide.stereoHealth < 0.80f,
                "very wide signal did not receive the expected partial stereo-health penalty");
        require(wide.monoSafety > 0.99f, "wide but positive-correlation signal was incorrectly mono unsafe");

        echoes::NaturalnessScorer antiPhaseScorer(static_cast<float>(kSampleRate));
        primeHealthyBaseline(antiPhaseScorer);
        const auto antiPhase = antiPhaseScorer.analyze(makeStereo(0.35f, kPi));
        require(antiPhase.stereoHealth < 0.01f, "anti-phase signal was not rejected by stereo health");
        require(antiPhase.monoSafety < 0.01f, "anti-phase signal was not declared mono unsafe");
        require(antiPhase.needsCorrection, "anti-phase regression did not request correction after a healthy baseline");
        require(antiPhase.widthCorrection == 0.0f,
                "negative-correlation correction must not use the mono-like width rule");

        std::cout << std::fixed << std::setprecision(6)
                  << "NaturalnessMonoSafety PASS"
                  << " healthyOverall=" << healthy.overall
                  << " healthyStereo=" << healthy.stereoHealth
                  << " monoStereo=" << mono.stereoHealth
                  << " monoSafety=" << mono.monoSafety
                  << " wideStereo=" << wide.stereoHealth
                  << " wideMonoSafety=" << wide.monoSafety
                  << " antiStereo=" << antiPhase.stereoHealth
                  << " antiMonoSafety=" << antiPhase.monoSafety
                  << " antiCorrection=" << (antiPhase.needsCorrection ? "true" : "false")
                  << '\n';
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "NaturalnessMonoSafety BLOCKED error=" << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
