#pragma once
#include "EchoesEngine/audio/AudioBuffer.h"
#include <string>
#include <functional>
#include <memory>

namespace echoes::neural {

/// AI Voice Conversion interface.
///
/// Abstracts over inference backends:
///   - Stub (pass-through) — no dependencies, always available
///   - ONNX Runtime (CPU/GPU) — Phase 2
///   - TensorRT — Phase 3 (NVIDIA RTX)
///
/// All backends must implement this interface.
class AIVoiceProcessor {
public:
    enum class Backend { Stub, OnnxCPU, OnnxGPU, TensorRT };

    struct Params {
        float pitchShiftSemitones = 0.0f;   // -12 to +12
        float timbreDepth         = 0.5f;   // 0 = original, 1 = full conversion
        float breathPreservation  = 0.8f;   // 0 = strip, 1 = keep all breath
        float expressionSensitivity = 0.5f;
        std::string targetVoiceModel;       // Path to .onnx model (empty = bypass)
    };

    struct Result {
        AudioBuffer output;
        bool        gpuAccelerated = false;
        bool        tensorRtUsed   = false;
        float       spectralFidelityPct = 100.0f;
        std::string backendName;
        float       processingMs = 0.0f;
    };

    virtual ~AIVoiceProcessor() = default;

    virtual bool   loadModel(const std::string& path)  = 0;
    virtual void   unloadModel()                        = 0;
    virtual bool   isModelLoaded() const noexcept       = 0;
    virtual Result process(const AudioBuffer& input, const Params& params) = 0;
    virtual Backend backend() const noexcept            = 0;
    virtual std::string backendName() const noexcept    = 0;
};

// ─────────────────────────────────────────────────────────────────────────────
/// Stub backend: identity transform.
/// Used when no model is loaded, or during unit tests.
/// No external dependencies — always compiles.
class StubAIProcessor final : public AIVoiceProcessor {
public:
    bool loadModel(const std::string&) override {
        m_loaded = true;
        return true;
    }
    void unloadModel() override { m_loaded = false; }
    bool isModelLoaded() const noexcept override { return m_loaded; }
    Backend backend() const noexcept override { return Backend::Stub; }
    std::string backendName() const noexcept override { return "Stub (pass-through)"; }

    Result process(const AudioBuffer& input, const Params& params) override {
        Result r;
        r.output            = input;   // Pass-through
        r.backendName       = backendName();
        r.spectralFidelityPct = 100.0f;
        r.processingMs      = 0.0f;
        r.gpuAccelerated    = false;

        // TODO: Wire real ONNX session here in Phase 2
        // OnnxInferenceSession session(params.targetVoiceModel);
        // r.output = session.run(input, params.pitchShiftSemitones, params.timbreDepth);

        (void)params;
        return r;
    }

private:
    bool m_loaded = false;
};

// ─────────────────────────────────────────────────────────────────────────────
/// Factory: returns best available backend for current hardware.
inline std::unique_ptr<AIVoiceProcessor> createBestProcessor() {
    // Phase 2: probe for ONNX GPU / TensorRT here
    // if (HardwareInfo::hasTensorRT()) return std::make_unique<TensorRTProcessor>();
    // if (HardwareInfo::hasCuda())     return std::make_unique<OnnxGpuProcessor>();
    return std::make_unique<StubAIProcessor>();
}

} // namespace echoes::neural
