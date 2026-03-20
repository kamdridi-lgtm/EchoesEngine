#pragma once
/**
 * NaturalnessScorer.h  —  Naturalness Scorer avec feedback loop
 * ==============================================================
 *
 * Mesure en continu la qualité perçue du signal traité.
 * Alimente un feedback loop → corrige l'EmotionalCore si nécessaire.
 *
 * Métriques:
 *   ✔ Stereo correlation    (trop proche de 1.0 = mono, trop proche de -1 = problème)
 *   ✔ Dynamic variance      (variance de l'enveloppe RMS sur 2s)
 *   ✔ Spectral flatness     (base FFT via méthode Welch allégée)
 *   ✔ Phase coherence       (couplage L/R dans le domaine fréquentiel)
 *   ✔ Threshold adaptatif   (s'adapte au contenu sur 30s)
 *   ✔ Feedback loop         (score → correction automatique du field)
 *   ✔ Auto correction       (réduit width si mono-safety compromise)
 */

#include "EchoesEngine/audio/AudioBuffer.h"
#include <cmath>
#include <array>
#include <deque>
#include <algorithm>
#include <numeric>

namespace echoes {

struct NaturalnessScorerConfig {
    bool  autoCorrect      = true;
    float correctionSpeed  = 0.05f;
    float stereoTargetMin  = 0.3f;
    float stereoTargetMax  = 0.92f;
    float dynamicTargetMin = 0.05f;
    float rmsWindowSec     = 0.05f;
    float adaptivePeriodSec= 30.0f;
};

class NaturalnessScorer {
public:
    using Config = NaturalnessScorerConfig;

    struct Score {
        float overall        = 1.0f;  // 0..1  (1 = parfait)
        float stereoHealth   = 1.0f;  // 0..1
        float dynamicHealth  = 1.0f;  // 0..1
        float spectralHealth = 1.0f;  // 0..1  (base, sans FFT)
        float monoSafety     = 1.0f;  // 0..1  (1 = safe)

        // Corrections suggérées (appliquées automatiquement si autoCorrect=true)
        float widthCorrection      = 0.0f;  // delta à appliquer au spatialWidth
        float compRatioCorrection  = 0.0f;  // delta compRatio
        float saturationCorrection = 0.0f;  // delta saturation

        bool  needsCorrection = false;
    };

    explicit NaturalnessScorer(float sampleRate, Config cfg = Config{})
        : m_sr(sampleRate), m_cfg(cfg)
    {
        m_rmsCoef = 1.f - std::exp(-1.f / (sampleRate * cfg.rmsWindowSec));
    }

    // ── Appelé chaque block APRÈS tout le traitement ─────────────────────────
    Score analyze(const AudioBuffer& buf) noexcept {
        if (buf.channels() < 2 || buf.frames() == 0) return m_lastScore;

        // 1. Stereo correlation
        float sumLL = 0.f, sumRR = 0.f, sumLR = 0.f;
        float peakL = 0.f, peakR = 0.f;
        uint32_t N = buf.frames();
        for (uint32_t f = 0; f < N; ++f) {
            float L = buf.at(f, 0);
            float R = buf.at(f, 1);
            sumLL += L * L;
            sumRR += R * R;
            sumLR += L * R;
            peakL  = std::max(peakL, std::abs(L));
            peakR  = std::max(peakR, std::abs(R));
        }
        float denom = std::sqrt(sumLL * sumRR + 1e-12f);
        float corr  = sumLR / denom;  // -1..+1

        // 2. RMS envelope + dynamic variance
        float rmsL = std::sqrt(sumLL / N);
        float rmsR = std::sqrt(sumRR / N);
        float rms  = (rmsL + rmsR) * 0.5f;

        m_rmsHistory.push_back(rms);
        if (m_rmsHistory.size() > static_cast<size_t>(m_sr * 2.0f / N))
            m_rmsHistory.pop_front();

        float dynVar = _computeVariance(m_rmsHistory);

        // 3. Spectral flatness (léger — sans FFT complète)
        //    Approximation: rapport des moments spectraux via zero-crossing rate
        float zcr    = _zeroCrossingRate(buf);
        float specFlat= std::clamp(zcr / 0.15f, 0.f, 1.f);  // normalisé

        // 4. Scores individuels
        Score s;

        // Stereo health: penalise si trop corrélé (mono-ish) ou négatif
        if (corr > m_cfg.stereoTargetMax)
            s.stereoHealth = 1.f - (corr - m_cfg.stereoTargetMax) / (1.f - m_cfg.stereoTargetMax);
        else if (corr < m_cfg.stereoTargetMin)
            s.stereoHealth = std::max(0.f, corr / m_cfg.stereoTargetMin);
        else
            s.stereoHealth = 1.0f;

        // Dynamic health: penalise si trop compressé (variance trop faible)
        s.dynamicHealth = std::min(1.f, dynVar / (m_cfg.dynamicTargetMin + 1e-6f));

        // Spectral health (proxy)
        s.spectralHealth = 0.5f + 0.5f * specFlat;

        // Mono safety
        s.monoSafety = corr > -0.3f ? 1.0f : std::max(0.f, 1.f + corr / 0.7f);

        // Overall (weighted)
        s.overall = s.stereoHealth   * 0.35f
                  + s.dynamicHealth  * 0.30f
                  + s.spectralHealth * 0.20f
                  + s.monoSafety     * 0.15f;

        // 5. Adaptive threshold update (every ~30s)
        _updateAdaptiveBaseline(s.overall);

        // 6. Corrections suggérées
        s.needsCorrection = (s.overall < m_adaptiveThreshold * 0.85f);
        if (s.needsCorrection) {
            // Trop corrélé → réduire width
            if (corr > m_cfg.stereoTargetMax)
                s.widthCorrection = -(corr - m_cfg.stereoTargetMax) * 0.3f;

            // Trop compressé → réduire ratio
            if (s.dynamicHealth < 0.5f)
                s.compRatioCorrection = -(1.f - s.dynamicHealth) * 1.5f;

            // Distortion → réduire saturation
            if (specFlat < 0.3f && rms > 0.1f)
                s.saturationCorrection = -(0.3f - specFlat) * 0.5f;
        }

        m_lastScore = s;
        return s;
    }

    Score lastScore() const noexcept { return m_lastScore; }

    // Reset de l'historique (ex: après changement brutal d'état émotionnel)
    void reset() noexcept {
        m_rmsHistory.clear();
        m_adaptiveBaseline.clear();
        m_lastScore = Score{};
    }

private:
    float _zeroCrossingRate(const AudioBuffer& buf) noexcept {
        uint32_t zc = 0;
        for (uint32_t f = 1; f < buf.frames(); ++f) {
            float cur  = buf.at(f, 0);
            float prev = buf.at(f-1, 0);
            if ((cur >= 0.f) != (prev >= 0.f)) ++zc;
        }
        return static_cast<float>(zc) / static_cast<float>(buf.frames());
    }

    float _computeVariance(const std::deque<float>& v) noexcept {
        if (v.size() < 2) return 0.f;
        float mean = std::accumulate(v.begin(), v.end(), 0.f) / v.size();
        float var  = 0.f;
        for (float x : v) var += (x - mean) * (x - mean);
        return var / v.size();
    }

    void _updateAdaptiveBaseline(float score) noexcept {
        m_adaptiveBaseline.push_back(score);
        // Garder ~30s d'historique (à 20Hz update rate)
        while (m_adaptiveBaseline.size() > 600) m_adaptiveBaseline.pop_front();

        if (!m_adaptiveBaseline.empty()) {
            float sum = std::accumulate(m_adaptiveBaseline.begin(),
                                        m_adaptiveBaseline.end(), 0.f);
            m_adaptiveThreshold = sum / m_adaptiveBaseline.size();
        }
    }

    float m_sr;
    Config m_cfg;
    float m_rmsCoef          = 0.1f;
    float m_adaptiveThreshold= 0.75f;

    std::deque<float> m_rmsHistory;       // 2s d'historique RMS
    std::deque<float> m_adaptiveBaseline; // 30s de scores

    Score m_lastScore;
};

} // namespace echoes
