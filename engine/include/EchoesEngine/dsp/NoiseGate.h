#pragma once
#include "EchoesEngine/audio/AudioBuffer.h"
#include <cmath>
#include <algorithm>

namespace echoes::dsp {

/// Envelope-following noise gate.
/// Kills signal below threshold — essential for hard rock vocal tracks.
///
///   Signal → [Envelope Detector] → [Gain Computer] → [Smoothing] → Output
///
class NoiseGate {
public:
    struct Params {
        float thresholdDb   = -40.0f;  // Open threshold
        float hysteresisDb  =  -6.0f;  // Close threshold = threshold + hysteresis
        float attackMs      =   1.0f;  // Gate open time
        float releaseMs     = 100.0f;  // Gate close time
        float holdMs        =  50.0f;  // Minimum open time after signal drops
        bool  enabled       = true;
    };

    explicit NoiseGate(float sampleRate)
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

        for (uint32_t f = 0; f < buf.frames(); ++f) {
            // ── Compute peak across channels ──────────────────────────────────
            float peak = 0.0f;
            for (uint32_t ch = 0; ch < buf.channels(); ++ch)
                peak = std::max(peak, std::abs(buf.at(f, ch)));

            // ── Envelope follower (peak with attack/release) ───────────────────
            float coef = (peak > m_envelope) ? m_attackCoef : m_releaseCoef;
            m_envelope += coef * (peak - m_envelope);

            // ── Gate state machine ────────────────────────────────────────────
            float envDb = m_envelope > 1e-9f ? 20.0f * std::log10(m_envelope) : -120.0f;

            if (!m_gateOpen) {
                if (envDb >= m_params.thresholdDb) {
                    m_gateOpen = true;
                    m_holdCounter = static_cast<uint32_t>(m_holdMs_samples);
                }
            } else {
                if (envDb < (m_params.thresholdDb + m_params.hysteresisDb)) {
                    if (m_holdCounter == 0) {
                        m_gateOpen = false;
                    } else {
                        --m_holdCounter;
                    }
                } else {
                    m_holdCounter = static_cast<uint32_t>(m_holdMs_samples);
                }
            }

            // ── Smooth gain ───────────────────────────────────────────────────
            float targetGain = m_gateOpen ? 1.0f : 0.0f;
            float gainCoef   = m_gateOpen ? m_attackCoef : m_releaseCoef;
            m_gainSmooth += gainCoef * (targetGain - m_gainSmooth);

            // ── Apply ─────────────────────────────────────────────────────────
            for (uint32_t ch = 0; ch < buf.channels(); ++ch)
                buf.at(f, ch) *= m_gainSmooth;
        }
    }

    [[nodiscard]] bool  isOpen()    const noexcept { return m_gateOpen; }
    [[nodiscard]] float gainLinear()const noexcept { return m_gainSmooth; }

private:
    void updateCoefs() {
        auto msToCoef = [&](float ms) {
            float sec = ms / 1000.0f;
            return sec > 0 ? 1.0f - std::exp(-1.0f / (m_sampleRate * sec)) : 1.0f;
        };
        m_attackCoef      = msToCoef(m_params.attackMs);
        m_releaseCoef     = msToCoef(m_params.releaseMs);
        m_holdMs_samples  = (m_params.holdMs / 1000.0f) * m_sampleRate;
    }

    float    m_sampleRate;
    Params   m_params;
    float    m_attackCoef    = 1.0f;
    float    m_releaseCoef   = 0.001f;
    float    m_holdMs_samples= 0.0f;
    float    m_envelope      = 0.0f;
    float    m_gainSmooth    = 0.0f;
    bool     m_gateOpen      = false;
    uint32_t m_holdCounter   = 0;
};

} // namespace echoes::dsp
