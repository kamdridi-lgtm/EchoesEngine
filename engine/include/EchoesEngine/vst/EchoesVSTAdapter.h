#pragma once

/**
 * EchoesVSTAdapter.h
 *
 * Thin glue layer between EchoesEngine and any VST3 host (JUCE or raw SDK).
 *
 * Architecture:
 *
 *   DAW Host
 *     │
 *     │  processBlock(float**, int channels, int frames)
 *     │  setParameter(ParamID, float normalized)
 *     ▼
 *   EchoesVSTAdapter          ← this file
 *     │
 *     │  wraps parameter normalization
 *     │  converts raw float** → AudioBuffer
 *     ▼
 *   EchoesEngine               ← full DSP + AI chain
 *     │
 *     ├── GainStage
 *     ├── NoiseGate
 *     ├── Compressor
 *     ├── VocalEQ (5-band)
 *     └── AIVoiceProcessor
 *
 * Phase 3 — JUCE integration:
 *   Replace the class below with:
 *   class EchoesVSTAdapter : public juce::AudioProcessor
 *   and implement createPluginFilter() at the bottom.
 *
 * Phase 3 — Raw Steinberg SDK:
 *   Replace with:
 *   class EchoesVSTAdapter : public Steinberg::Vst::AudioEffect
 */

#include "EchoesEngine/Engine.h"
#include "EchoesEngine/vst/IVSTPlugin.h"
#include "EchoesEngine/audio/AudioBuffer.h"

#include <unordered_map>
#include <cstring>
#include <algorithm>
#include <cmath>

namespace echoes::vst {

class EchoesVSTAdapter final : public IVSTPlugin {
public:
    EchoesVSTAdapter() = default;
    ~EchoesVSTAdapter() override { shutdown(); }

    // ── IVSTPlugin interface ──────────────────────────────────────────────────

    bool initialize(double sampleRate, int32_t maxBlockSize) override {
        EchoesEngine::Config cfg;
        cfg.sampleRate  = static_cast<float>(sampleRate);
        cfg.blockSize   = static_cast<uint32_t>(maxBlockSize);
        cfg.numChannels = 2;

        m_engine = std::make_unique<EchoesEngine>(cfg);
        bool ok  = m_engine->init();

        if (ok) {
            m_blockBuffer.resize(
                static_cast<uint32_t>(cfg.numChannels),
                static_cast<uint32_t>(maxBlockSize),
                static_cast<uint32_t>(sampleRate));
            // Default VST parameter values
            m_params[ParamID::InputGainDb]          = _normalizeDb(0.0f,  -24.0f, +24.0f);
            m_params[ParamID::GateThresholdDb]      = _normalizeDb(-40.0f, -80.0f,   0.0f);
            m_params[ParamID::CompThresholdDb]      = _normalizeDb(-18.0f, -60.0f,   0.0f);
            m_params[ParamID::CompRatio]            = _normalizeLinear(4.0f, 1.0f, 20.0f);
            m_params[ParamID::CompAttackMs]         = _normalizeLinear(5.0f, 0.1f, 200.0f);
            m_params[ParamID::CompReleaseMs]        = _normalizeLinear(80.0f, 10.0f, 2000.0f);
            m_params[ParamID::CompMakeupGainDb]     = _normalizeDb(6.0f, 0.0f, 24.0f);
            m_params[ParamID::AITimbreDepth]        = 0.0f;   // bypass by default
            m_params[ParamID::AIPitchShift]         = 0.5f;   // center = 0 semitones
            m_params[ParamID::AIBreathPreservation] = 0.8f;
            m_params[ParamID::OutputGainDb]         = _normalizeDb(0.0f, -24.0f, +12.0f);
            m_params[ParamID::Bypass]               = 0.0f;

            m_sampleRate  = sampleRate;
            m_maxBlockSize= maxBlockSize;
        }
        return ok;
    }

    void setParameter(ParamID id, float norm) override {
        m_params[id] = std::clamp(norm, 0.0f, 1.0f);
        if (!m_engine || !m_engine->isRunning()) return;

        // Denormalize and apply to engine
        switch (id) {
            case ParamID::InputGainDb:
                m_engine->setInputGainDb(_denormDb(norm, -24.0f, +24.0f));
                break;
            case ParamID::OutputGainDb:
                m_engine->setOutputGainDb(_denormDb(norm, -24.0f, +12.0f));
                break;
            case ParamID::GateThresholdDb:
                m_engine->setGateThreshold(_denormDb(norm, -80.0f, 0.0f));
                break;
            case ParamID::GateReleaseMs:
                // TODO: expose release setter on engine
                break;
            case ParamID::CompThresholdDb:
                m_engine->setCompThreshold(_denormDb(norm, -60.0f, 0.0f));
                break;
            case ParamID::CompRatio:
                m_engine->setCompRatio(_denormLinear(norm, 1.0f, 20.0f));
                break;
            case ParamID::AITimbreDepth:
                m_engine->setAIEnabled(norm > 0.01f);
                break;
            case ParamID::Bypass:
                m_bypassed = norm > 0.5f;
                break;
            default:
                break;
        }
    }

    float getParameter(ParamID id) const override {
        auto it = m_params.find(id);
        return it != m_params.end() ? it->second : 0.0f;
    }

    void processBlock(
        float**            channelData,
        int32_t            numChannels,
        int32_t            numFrames,
        const ProcessContext& ctx) override
    {
        if (!m_engine || !m_engine->isRunning()) return;
        if (m_bypassed) return;   // hard bypass — no processing

        // Convert float** → interleaved AudioBuffer
        m_blockBuffer.resize(
            static_cast<uint32_t>(numChannels),
            static_cast<uint32_t>(numFrames),
            static_cast<uint32_t>(m_sampleRate)
        );

        for (int32_t ch = 0; ch < numChannels; ++ch)
            for (int32_t f = 0; f < numFrames; ++f)
                m_blockBuffer.at(static_cast<uint32_t>(f),
                                 static_cast<uint32_t>(ch)) = channelData[ch][f];

        // Process through full chain
        m_engine->process(m_blockBuffer);

        // Write back to DAW buffer
        for (int32_t ch = 0; ch < numChannels; ++ch)
            for (int32_t f = 0; f < numFrames; ++f)
                channelData[ch][f] = m_blockBuffer.at(
                    static_cast<uint32_t>(f),
                    static_cast<uint32_t>(ch));
    }

    void shutdown() override {
        if (m_engine) m_engine->shutdown();
        m_engine.reset();
    }

    const PluginInfo& info() const override {
        static const PluginInfo kInfo;
        return kInfo;
    }

    // ── Metering (read from UI thread) ────────────────────────────────────────
    EchoesEngine::Stats getStats() const noexcept {
        return m_engine ? m_engine->getStats() : EchoesEngine::Stats{};
    }

    bool isLoaded() const noexcept {
        return m_engine && m_engine->isRunning();
    }

private:
    std::unique_ptr<EchoesEngine> m_engine;
    std::unordered_map<ParamID, float> m_params;
    AudioBuffer m_blockBuffer;
    double  m_sampleRate   = 44100.0;
    int32_t m_maxBlockSize = 512;
    bool    m_bypassed     = false;

    // ── Normalization helpers ──────────────────────────────────────────────
    static float _normalizeDb(float val, float minDb, float maxDb) noexcept {
        return (val - minDb) / (maxDb - minDb);
    }
    static float _denormDb(float norm, float minDb, float maxDb) noexcept {
        return minDb + norm * (maxDb - minDb);
    }
    static float _normalizeLinear(float val, float lo, float hi) noexcept {
        return (val - lo) / (hi - lo);
    }
    static float _denormLinear(float norm, float lo, float hi) noexcept {
        return lo + norm * (hi - lo);
    }
};

} // namespace echoes::vst
