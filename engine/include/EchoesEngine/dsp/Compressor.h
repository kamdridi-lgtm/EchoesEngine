#pragma once
#include "EchoesEngine/audio/AudioBuffer.h"
#include <cmath>
#include <algorithm>

namespace echoes::dsp {

/// RMS feed-forward compressor with program-dependent release.
/// Designed for Hard Rock vocals: fast attack, musical release.
///
///   Signal → [RMS Detector] → [Gain Computer] → [Gain Smoother] → Output
///
class Compressor {
public:
    struct Params {
        float thresholdDb  = -18.0f; // Compression starts here
        float ratio        =   4.0f; // 4:1 typical for rock vocals
        float attackMs     =   5.0f; // 5ms — catch transients
        float releaseMs    =  80.0f; // 80ms — musical release
        float kneeDb       =   3.0f; // Soft knee width
        float makeupGainDb =   6.0f; // Compensate for gain reduction
        bool  enabled      = true;
    };

    explicit Compressor(float sampleRate)
        : m_sampleRate(sampleRate)
    {
        updateCoefs();
    }

    void setParams(const Params& p) {
        m_params = p;
        updateCoefs();
    }

    void process(AudioBuffer& buf) {
        if (!m_params.enabled) return;

        const float makeupGain = std::pow(10.0f, m_params.makeupGainDb / 20.0f);

        for (uint32_t f = 0; f < buf.frames(); ++f) {
            // ── RMS estimation (single pole, ~10ms window) ─────────────────
            float sumSq = 0.0f;
            for (uint32_t ch = 0; ch < buf.channels(); ++ch) {
                float s = buf.at(f, ch);
                sumSq += s * s;
            }
            float instPower = sumSq / static_cast<float>(buf.channels());
            m_rmsState += m_rmsCoef * (instPower - m_rmsState);

            float rmsDb = m_rmsState > 1e-10f
                ? 10.0f * std::log10(m_rmsState)
                : -100.0f;

            // ── Compute gain reduction (soft-knee) ────────────────────────
            float gr = computeGainReductionDb(rmsDb);

            // ── Smooth gain reduction ─────────────────────────────────────
            float coef = (gr < m_smoothedGR) ? m_attackCoef : m_releaseCoef;
            m_smoothedGR += coef * (gr - m_smoothedGR);

            // ── Apply gain ────────────────────────────────────────────────
            float totalGain = std::pow(10.0f, m_smoothedGR / 20.0f) * makeupGain;
            for (uint32_t ch = 0; ch < buf.channels(); ++ch)
                buf.at(f, ch) *= totalGain;
        }
    }

    [[nodiscard]] float gainReductionDb() const noexcept { return m_smoothedGR; }

private:
    float computeGainReductionDb(float inputDb) const noexcept {
        float T = m_params.thresholdDb;
        float R = m_params.ratio;
        float W = m_params.kneeDb;
        float x = inputDb - T;

        if (2.0f * x < -W) {
            return 0.0f; // below knee: no compression
        } else if (2.0f * std::abs(x) <= W) {
            // In soft knee
            float kx = x + W / 2.0f;
            return (1.0f / R - 1.0f) * (kx * kx) / (2.0f * W);
        } else {
            return (1.0f / R - 1.0f) * x; // above knee: full ratio
        }
    }

    void updateCoefs() {
        auto msToCoef = [&](float ms) {
            return 1.0f - std::exp(-1.0f / (m_sampleRate * ms / 1000.0f));
        };
        m_attackCoef  = msToCoef(m_params.attackMs);
        m_releaseCoef = msToCoef(m_params.releaseMs);
        m_rmsCoef     = msToCoef(10.0f); // 10ms RMS window
    }

    float  m_sampleRate;
    Params m_params;
    float  m_attackCoef  = 1.0f;
    float  m_releaseCoef = 0.01f;
    float  m_rmsCoef     = 0.1f;
    float  m_rmsState    = 0.0f;
    float  m_smoothedGR  = 0.0f;   // smoothed gain reduction (dB, <= 0)
};

} // namespace echoes::dsp
