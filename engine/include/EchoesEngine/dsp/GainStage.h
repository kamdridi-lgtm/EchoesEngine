#pragma once
#include "EchoesEngine/audio/AudioBuffer.h"
#include <cmath>
#include <algorithm>

namespace echoes::dsp {

/// Input gain stage with smooth gain ramping and hard clip protection.
/// First real DSP in the signal chain.
class GainStage {
public:
    struct Params {
        float gainDb        = 0.0f;   // Input gain in dB  (-60 to +24)
        float clipThreshold = 0.98f;  // Hard clip ceiling (linear)
        bool  enabled       = true;
    };

    explicit GainStage(float sampleRate)
        : m_sampleRate(sampleRate)
        , m_currentGain(1.0f)
    {}

    void setParams(const Params& p) {
        m_params = p;
        m_targetGain = std::pow(10.0f, p.gainDb / 20.0f);
    }

    /// Process in-place. Applies smoothed gain + clip.
    void process(AudioBuffer& buf) {
        if (!m_params.enabled) return;

        const float smoothCoef = computeSmoothCoef(0.010f); // 10ms ramp
        const float clipT      = m_params.clipThreshold;

        for (uint32_t f = 0; f < buf.frames(); ++f) {
            // Smooth gain toward target
            m_currentGain += smoothCoef * (m_targetGain - m_currentGain);

            for (uint32_t ch = 0; ch < buf.channels(); ++ch) {
                float s = buf.at(f, ch) * m_currentGain;
                // Hard clip
                s = std::clamp(s, -clipT, clipT);
                buf.at(f, ch) = s;
            }
        }
    }

    [[nodiscard]] float currentGainDb() const noexcept {
        return m_currentGain > 1e-9f ? 20.0f * std::log10(m_currentGain) : -120.0f;
    }

private:
    float computeSmoothCoef(float rampSeconds) const noexcept {
        return 1.0f - std::exp(-1.0f / (m_sampleRate * rampSeconds));
    }

    float  m_sampleRate;
    float  m_currentGain;
    float  m_targetGain = 1.0f;
    Params m_params;
};

} // namespace echoes::dsp
