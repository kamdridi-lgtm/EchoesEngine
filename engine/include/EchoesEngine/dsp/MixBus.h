#pragma once
/**
 * MixBus.h  —  MixBus Intelligent
 * =================================
 *
 * Dernière étape avant sortie:
 *   ✔ Headroom control (pilotable par emotional field)
 *   ✔ Soft saturation (tape-style, asymétrique)
 *   ✔ Glue compression légère (RMS, ratio 1.5:1)
 *   ✔ Sync émotion (intensité → saturation + glue)
 *   ✔ Output limiter (pas de clip)
 */

#include "EchoesEngine/audio/AudioBuffer.h"
#include "EchoesEngine/emotional/EmotionalCore.h"
#include <cmath>
#include <algorithm>

namespace echoes::dsp {

class MixBus {
public:
    struct Params {
        float headroomDb  =  0.0f;   // -6..0   target ceiling
        float saturation  =  0.0f;   // 0..1    tape drive
        float glue        =  0.5f;   // 0..1    glue compression
        float outputGainDb=  0.0f;
        bool  limiterOn   = true;
    };

    explicit MixBus(float sampleRate)
        : m_sr(sampleRate)
    {
        updateCoefs();
    }

    void applyField(const emotional::EmotionalField& f) noexcept {
        Params p;
        p.headroomDb  = std::clamp(f.headroom, -6.f, 0.f);
        p.saturation  = std::clamp(f.saturation * 0.6f, 0.f, 0.6f); // cap à 0.6
        p.glue        = std::clamp(f.glue, 0.f, 1.f);
        p.outputGainDb= 0.f;
        p.limiterOn   = true;
        setParams(p);
    }

    void setParams(const Params& p) noexcept {
        m_params = p;
        updateCoefs();
    }

    void process(AudioBuffer& buf) noexcept {
        const float ceiling = std::pow(10.f, m_params.headroomDb / 20.f);
        const float outGain = std::pow(10.f, m_params.outputGainDb / 20.f);

        for (uint32_t f = 0; f < buf.frames(); ++f) {
            for (uint32_t ch = 0; ch < buf.channels(); ++ch) {
                float x = buf.at(f, ch);

                // ── Glue compression (RMS feed-forward, ratio 1.5:1) ──────────
                float rms = _updateRMS(ch, x);
                float grDb = 0.f;
                if (m_params.glue > 0.01f) {
                    float thresh = -12.f * m_params.glue;
                    float rmsDb  = rms > 1e-10f ? 20.f * std::log10(rms) : -120.f;
                    if (rmsDb > thresh)
                        grDb = -(rmsDb - thresh) * (1.f - 1.f / 1.5f) * m_params.glue;
                    float gr = std::pow(10.f, grDb / 20.f);
                    x *= gr;
                }

                // ── Soft saturation (tape-style asymétrique) ──────────────────
                if (m_params.saturation > 0.01f) {
                    float drive = 1.f + m_params.saturation * 4.f;
                    x = _softclip(x * drive) / drive;
                }

                // ── Headroom scaling ──────────────────────────────────────────
                x *= ceiling * outGain;

                // ── Output limiter ────────────────────────────────────────────
                if (m_params.limiterOn)
                    x = _limiter(x, ch);

                buf.at(f, ch) = x;
            }
        }
    }

    float gainReductionDb() const noexcept { return m_limiterGR; }

private:
    // Tape-style asymétrique: positif sature légèrement plus que négatif
    static float _softclip(float x) noexcept {
        constexpr float k = 1.5f;
        if (x >  1.f) return  (3.f - (k - x) * (k - x)) / 3.f;
        if (x < -1.f) return -(3.f - (k + x) * (k + x)) / 3.f;
        return x - x*x*x / 3.f;
    }

    float _limiter(float x, uint32_t ch) noexcept {
        // Lookahead-less peak limiter (brick-wall à -0.1dBFS)
        constexpr float kCeiling = 0.9886f; // -0.1 dBFS
        float peak = std::abs(x);
        if (peak > kCeiling) {
            float gr = kCeiling / peak;
            m_limiterGR = 20.f * std::log10(gr);
            x *= gr;
        } else {
            m_limiterGR *= m_limiterRelease;
        }
        return x;
    }

    float _updateRMS(uint32_t ch, float x) noexcept {
        if (ch >= 2) return 0.f;
        m_rmsState[ch] += m_rmsCoef * (x*x - m_rmsState[ch]);
        return std::sqrt(m_rmsState[ch]);
    }

    void updateCoefs() noexcept {
        m_rmsCoef       = 1.f - std::exp(-1.f / (m_sr * 0.050f)); // 50ms window
        m_limiterRelease= std::exp(-1.f / (m_sr * 0.050f));       // 50ms release
    }

    float m_sr;
    Params m_params;
    float  m_rmsState[2]   = {0.f, 0.f};
    float  m_rmsCoef       = 0.1f;
    float  m_limiterGR     = 0.f;
    float  m_limiterRelease= 0.999f;
};

} // namespace echoes::dsp
