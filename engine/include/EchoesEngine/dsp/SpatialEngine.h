#pragma once
/**
 * SpatialEngine.h  —  Spatial Audio Engine (Propriétaire)
 * =========================================================
 *
 * Implémentation complète:
 *   ✔ ITD réel (délai interaural)
 *   ✔ ITD dépendant fréquence (low-freq plus large, high-freq plus étroit)
 *   ✔ Early reflections (4 taps)
 *   ✔ HF decorrelation via all-pass
 *   ✔ Mono safety (sum+cancel check)
 *   ✔ Emotional sync (piloté par EmotionalField)
 *   ✔ Micro drift intégré
 *
 * Usage:
 *   SpatialEngine spatial(44100.f);
 *   spatial.applyField(field);    // depuis EmotionalCore
 *   spatial.process(buffer);      // in-place stéréo
 */

#include "EchoesEngine/audio/AudioBuffer.h"
#include "EchoesEngine/emotional/EmotionalCore.h"
#include <array>
#include <vector>
#include <cmath>
#include <algorithm>

namespace echoes::dsp {

// ─────────────────────────────────────────────────────────────────────────────
// All-pass filter pour HF decorrelation
// ─────────────────────────────────────────────────────────────────────────────
class AllPassFilter {
public:
    AllPassFilter(float delay = 0.0f, float coeff = 0.5f)
        : m_coeff(coeff), m_buf(0.f) {}

    float process(float x) noexcept {
        float y = -m_coeff * x + m_buf;
        m_buf   =  m_coeff * y + x;
        return y;
    }
    void reset() noexcept { m_buf = 0.f; }

private:
    float m_coeff;
    float m_buf;
};

// ─────────────────────────────────────────────────────────────────────────────
// Délai fractionnaire (interpolation linéaire)
// ─────────────────────────────────────────────────────────────────────────────
class FractionalDelay {
public:
    explicit FractionalDelay(int maxDelaySamples = 128)
        : m_buf(maxDelaySamples, 0.f), m_writeIdx(0) {}

    void setDelaySamples(float d) noexcept {
        m_delaySamples = std::clamp(d, 0.f, static_cast<float>(m_buf.size()-2));
    }

    float process(float x) noexcept {
        m_buf[m_writeIdx] = x;
        m_writeIdx = (m_writeIdx + 1) % m_buf.size();

        float di   = m_delaySamples;
        int   id0  = (m_writeIdx - static_cast<int>(di) - 1 + m_buf.size()) % m_buf.size();
        int   id1  = (id0 - 1 + m_buf.size()) % m_buf.size();
        float frac = di - std::floor(di);
        return m_buf[id0] * (1.f - frac) + m_buf[id1] * frac;
    }

    void reset() noexcept {
        std::fill(m_buf.begin(), m_buf.end(), 0.f);
    }

private:
    std::vector<float> m_buf;
    int   m_writeIdx    = 0;
    float m_delaySamples = 0.f;
};

// ─────────────────────────────────────────────────────────────────────────────
// SPATIAL ENGINE
// ─────────────────────────────────────────────────────────────────────────────
class SpatialEngine {
public:
    struct Params {
        float width       = 1.0f;   // 0.0 = mono, 1.0 = neutre, 2.0 = extra-wide
        float depth       = 0.3f;   // 0..1 (early reflections level)
        float decorrelation = 0.3f; // 0..1 (HF decorrelation L/R)
        float itdMs       = 0.3f;   // 0..1.5ms ITD
        float driftWidth  = 0.0f;   // micro drift
        float driftDecorr = 0.0f;
    };

    explicit SpatialEngine(float sampleRate)
        : m_sr(sampleRate)
        , m_itdDelayL(128), m_itdDelayR(128)
    {
        // Early reflection tap delays (Schroeder-style)
        // ms → samples
        m_reflTapsL = { 7.f, 17.f, 33.f, 57.f };   // ms
        m_reflTapsR = { 11.f, 21.f, 39.f, 63.f };
        m_reflGains = { 0.45f, 0.30f, 0.18f, 0.10f };

        for (auto ms : m_reflTapsL)
            m_reflDelaysL.emplace_back(static_cast<int>(ms / 1000.f * sampleRate * 1.5f));
        for (auto ms : m_reflTapsR)
            m_reflDelaysR.emplace_back(static_cast<int>(ms / 1000.f * sampleRate * 1.5f));

        // Init tap delays
        for (size_t i = 0; i < m_reflTapsL.size(); ++i) {
            m_reflDelaysL[i].setDelaySamples(m_reflTapsL[i] / 1000.f * sampleRate);
            m_reflDelaysR[i].setDelaySamples(m_reflTapsR[i] / 1000.f * sampleRate);
        }

        // All-pass de decorrelation (coefficients légèrement différents L/R)
        m_apL = AllPassFilter(0.f, 0.48f);
        m_apR = AllPassFilter(0.f, 0.52f);

        setParams(m_params);
    }

    // ── Applique directement depuis l'EmotionalField ──────────────────────────
    void applyField(const emotional::EmotionalField& f) noexcept {
        Params p;
        p.width         = std::clamp(f.spatialWidth + f.driftWidth, 0.2f, 2.2f);
        p.depth         = std::clamp(f.spatialDepth, 0.f, 1.f);
        p.decorrelation = std::clamp(f.decorrelation + f.driftDecorr, 0.f, 0.95f);
        p.itdMs         = std::clamp(f.itdMs, 0.f, 1.5f);
        p.driftWidth    = f.driftWidth;
        p.driftDecorr   = f.driftDecorr;
        setParams(p);
    }

    void setParams(const Params& p) noexcept {
        m_params = p;
        // ITD: wider → plus de délai
        float itdSamples = p.itdMs / 1000.f * m_sr;
        m_itdDelayL.setDelaySamples(itdSamples * 0.5f);
        m_itdDelayR.setDelaySamples(0.f);  // R est la référence
    }

    // ── Traitement in-place (stéréo obligatoire) ─────────────────────────────
    void process(AudioBuffer& buf) noexcept {
        if (buf.channels() < 2) return;

        const float W     = m_params.width;
        const float D     = m_params.depth;
        const float decor = m_params.decorrelation;

        // Fréquence de coupure pour ITD dépendant fréquence
        // (simplifié: 1 seul filtre passe-bas pour la composante large)
        const float lpAlpha = computeLpAlpha(800.f);  // 800Hz pivot

        for (uint32_t f = 0; f < buf.frames(); ++f) {
            float L = buf.at(f, 0);
            float R = buf.at(f, 1);

            // ── Mid/Side decomposition ────────────────────────────────────────
            float M = (L + R) * 0.5f;
            float S = (L - R) * 0.5f;

            // ── Width (Mid/Side processing) ───────────────────────────────────
            float Mw = M;
            float Sw = S * W;  // élargir/rétrécir le side

            // ── ITD basse fréquence (filtre LP sur le side élargi) ────────────
            m_lpStateL += lpAlpha * (Sw - m_lpStateL);
            m_lpStateR += lpAlpha * (Sw - m_lpStateR);
            // LF: ITD réel via délai
            float SL_lf = m_itdDelayL.process(m_lpStateL);
            float SR_lf = m_lpStateR;
            // HF: décorrélation all-pass
            float SL_hf = m_apL.process(Sw - m_lpStateL);
            float SR_hf = m_apR.process(Sw - m_lpStateR);

            float SL = SL_lf + SL_hf;
            float SR = SR_lf + SR_hf;

            // ── Décorrélation HF ──────────────────────────────────────────────
            // Blend entre side original et side all-passé
            float SL_final = SL * (1.f - decor) + SL_hf * decor;
            float SR_final = SR * (1.f - decor) + SR_hf * decor;

            // ── Early reflections ─────────────────────────────────────────────
            float reflL = 0.f, reflR = 0.f;
            for (size_t t = 0; t < m_reflDelaysL.size(); ++t) {
                reflL += m_reflDelaysL[t].process(M) * m_reflGains[t];
                reflR += m_reflDelaysR[t].process(M) * m_reflGains[t];
            }

            // ── Reconstruction L/R ────────────────────────────────────────────
            float Lout = Mw + SL_final + reflL * D;
            float Rout = Mw - SR_final + reflR * D;

            // ── Mono safety ───────────────────────────────────────────────────
            // Vérifie que la somme mono reste cohérente (pas de phase cancel >-6dB)
            float mono     = (Lout + Rout) * 0.5f;
            float monoRaw  = M;
            if (std::abs(monoRaw) > 1e-6f) {
                float cancel = std::abs(mono) / std::abs(monoRaw);
                if (cancel < 0.5f) {
                    // Phase cancel détectée — réduire width
                    float blend = std::min(1.f, cancel * 2.f);
                    Lout = L * (1.f - blend) + Lout * blend;
                    Rout = R * (1.f - blend) + Rout * blend;
                }
            }

            buf.at(f, 0) = Lout;
            buf.at(f, 1) = Rout;
        }

        // Mise à jour stats mono safety
        _updateMonoStats(buf);
    }

    // ── Naturalness check (correlation stéréo) ───────────────────────────────
    float stereoCorrelation() const noexcept { return m_stereoCorr; }
    bool  monoSafe()          const noexcept { return m_monoSafe; }
    const Params& getParams() const noexcept { return m_params; }

private:
    float computeLpAlpha(float cutoffHz) const noexcept {
        float w = 2.f * 3.14159f * cutoffHz / m_sr;
        return w / (w + 1.f);
    }

    void _updateMonoStats(const AudioBuffer& buf) noexcept {
        float sumLL = 0.f, sumRR = 0.f, sumLR = 0.f;
        int N = std::min(static_cast<int>(buf.frames()), 256);
        for (int f = 0; f < N; ++f) {
            float L = buf.at(f, 0);
            float R = buf.at(f, 1);
            sumLL += L*L;
            sumRR += R*R;
            sumLR += L*R;
        }
        float denom = std::sqrt(sumLL * sumRR + 1e-12f);
        m_stereoCorr = sumLR / denom;
        m_monoSafe   = (m_stereoCorr > -0.3f);
    }

    float m_sr;
    Params m_params;

    // ITD delays
    FractionalDelay m_itdDelayL, m_itdDelayR;

    // Early reflections
    std::vector<float>          m_reflTapsL, m_reflTapsR, m_reflGains;
    std::vector<FractionalDelay> m_reflDelaysL, m_reflDelaysR;

    // All-pass decorrelation
    AllPassFilter m_apL, m_apR;

    // LP state pour ITD freq-dependent
    float m_lpStateL = 0.f;
    float m_lpStateR = 0.f;

    // Stats
    float m_stereoCorr = 1.f;
    bool  m_monoSafe   = true;
};

} // namespace echoes::dsp
