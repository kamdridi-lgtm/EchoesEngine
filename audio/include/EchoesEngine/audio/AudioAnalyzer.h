#pragma once

#include <span>
#include <cstddef>
#include <cstdint>

namespace echoes::audio {

struct AnalyzerConfig {
    std::size_t fftSize         = 2048;
    std::size_t hopSize         = 512;
    std::size_t energyHistory    = 43;
    float       beatSensitivity  = 1.35f;
    float       beatCooldownSecs = 0.2f;
    float       tempoSmoothing   = 0.3f;
};

struct RealtimeFeatures {
    float bass        = 0.0f;
    float mid         = 0.0f;
    float treble      = 0.0f;
    float energy      = 0.0f;
    bool  beat        = false;
    float beatConfidence = 0.0f;
    float tempo       = 0.0f;
};

class AudioAnalyzer {
public:
    explicit AudioAnalyzer(AnalyzerConfig config = {});
    AudioAnalyzer(const AudioAnalyzer&) = delete;
    AudioAnalyzer& operator=(const AudioAnalyzer&) = delete;
    ~AudioAnalyzer();

    void reset() noexcept;
    void processBlock(std::span<const float> block, double sampleRate);

    [[nodiscard]] RealtimeFeatures features() const noexcept;

private:
    struct Impl;
    Impl* impl_;
};

} // namespace echoes::audio
