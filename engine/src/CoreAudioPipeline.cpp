#include "EchoesEngine/CoreAudioPipeline.h"

#include "EchoesEngine/NaturalnessScorer.h"
#include "EchoesEngine/dsp/Compressor.h"
#include "EchoesEngine/dsp/GainStage.h"
#include "EchoesEngine/dsp/MixBus.h"
#include "EchoesEngine/dsp/NoiseGate.h"
#include "EchoesEngine/dsp/SpatialEngine.h"
#include "EchoesEngine/dsp/VocalEQ.h"
#include "EchoesEngine/emotional/EmotionalCore.h"
#include "EchoesEngine/neural/AIVoiceProcessor.h"
#include "EchoesEngine/neural/CrepeLight.h"
#include "EchoesEngine/neural/TensorRTEngine.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <utility>

namespace echoes {

using Clock = std::chrono::steady_clock;

struct CoreAudioPipeline::Impl {
    EchoesEngine::Config config;
    bool running = false;

    std::unique_ptr<dsp::GainStage> inputGain;
    std::unique_ptr<dsp::NoiseGate> gate;
    std::unique_ptr<dsp::Compressor> comp;
    std::unique_ptr<dsp::VocalEQ> eq;
    std::unique_ptr<dsp::GainStage> outputGain;

    std::unique_ptr<emotional::EmotionalCore> emotional;
    bool emotionalEnabled = true;

    std::unique_ptr<dsp::SpatialEngine> spatial;
    std::unique_ptr<dsp::MixBus> mixBus;
    std::unique_ptr<NaturalnessScorer> naturalness;

    std::unique_ptr<neural::AIVoiceProcessor> ai;
    neural::AIVoiceProcessor::Params aiParams;
    std::unique_ptr<neural::CrepeLight> pitch;

    std::unique_ptr<trt::TensorRTEngine> trtEncoder;
    std::unique_ptr<trt::TensorRTEngine> trtDecoder;
    std::atomic<float> trtBuildPct{-1.0f};

    std::atomic<uint32_t> statsWriteIndex{0};
    ExtendedStats statsBuffer[2];
    EchoesEngine::StatsCallback statsCallback;
    float avgBlockMs = 0.0f;

    explicit Impl(const EchoesEngine::Config& cfg) : config(cfg) {}
};

CoreAudioPipeline::CoreAudioPipeline(const EchoesEngine::Config& config)
    : pImpl(std::make_unique<Impl>(config)) {}

CoreAudioPipeline::~CoreAudioPipeline() {
    if (pImpl && pImpl->running) {
        shutdown();
    }
}

bool CoreAudioPipeline::init() {
    if (pImpl->running) {
        return true;
    }

    const float sr = pImpl->config.sampleRate;

    std::cout << "\n";
    std::cout << "==============================================\n";
    std::cout << "  " << EchoesEngine::version() << "\n";
    std::cout << "  SR:" << std::setw(7) << sr
              << " Hz  Block:" << std::setw(5) << pImpl->config.blockSize
              << "  Ch:" << pImpl->config.numChannels << "\n";
    std::cout << "==============================================\n\n";

    try {
        pImpl->emotional = std::make_unique<emotional::EmotionalCore>(sr);
        std::cout << "[Pipeline] Emotional: EmotionalCore online\n";

        pImpl->inputGain = std::make_unique<dsp::GainStage>(sr);
        {
            dsp::GainStage::Params params;
            params.gainDb = pImpl->config.inputGainDb;
            pImpl->inputGain->setParams(params);
        }

        pImpl->gate = std::make_unique<dsp::NoiseGate>(sr);
        pImpl->comp = std::make_unique<dsp::Compressor>(sr);
        pImpl->eq = std::make_unique<dsp::VocalEQ>(sr);

        pImpl->outputGain = std::make_unique<dsp::GainStage>(sr);
        {
            dsp::GainStage::Params params;
            params.gainDb = pImpl->config.outputGainDb;
            pImpl->outputGain->setParams(params);
        }

        std::cout << "[Pipeline] DSP: Gain -> Gate -> Comp -> EQ(5)\n";

        pImpl->spatial = std::make_unique<dsp::SpatialEngine>(sr);
        pImpl->mixBus = std::make_unique<dsp::MixBus>(sr);
        pImpl->naturalness = std::make_unique<NaturalnessScorer>(sr);
        std::cout << "[Pipeline] Spatial: ITD + EarlyRefl + HF-Decorr\n";
        std::cout << "[Pipeline] MixBus: Saturation + Glue + Limiter\n";
        std::cout << "[Pipeline] Scorer: NaturalnessScorer online\n";

        pImpl->ai = neural::createBestProcessor();
        std::cout << "[Pipeline] AI: " << pImpl->ai->backendName() << "\n";

        if (!pImpl->config.voiceModelPath.empty()) {
            const bool ok = pImpl->ai->loadModel(pImpl->config.voiceModelPath);
            std::cout << "[Pipeline] Model: " << (ok ? "loaded " : "failed ")
                      << pImpl->config.voiceModelPath << "\n";

            if (ok && trt::TensorRTEngine::isCUDAAvailable()) {
                const std::string modelPath = pImpl->config.voiceModelPath;
                std::thread([this, modelPath]() {
                    pImpl->trtBuildPct.store(0.0f);
                    pImpl->trtEncoder = std::make_unique<trt::TensorRTEngine>(0);
                    pImpl->trtDecoder = std::make_unique<trt::TensorRTEngine>(0);
                    auto callback = [this](int pct, const std::string&) {
                        pImpl->trtBuildPct.store(static_cast<float>(pct));
                    };
                    pImpl->trtEncoder->setBuildProgressCallback(callback);
                    pImpl->trtEncoder->init(modelPath + "_encoder.onnx", trt::Precision::FP16);
                    pImpl->trtDecoder->init(modelPath + "_decoder.onnx", trt::Precision::FP16);
                    pImpl->trtBuildPct.store(-1.0f);
                    std::cout << "[Pipeline] TRT FP16 engines ready\n";
                }).detach();
            }
        }

        neural::CrepeLight::Config pitchConfig;
        pitchConfig.sampleRate = sr;
        pitchConfig.hopSizeSamples = static_cast<int>(sr * 0.010f);
        pitchConfig.useOnnx = true;
        for (const std::string& candidate : {
                 std::string("./models/crepe-lite-pitch/crepe_lite_44k.onnx"),
                 std::string("./models/crepe_lite_44k.onnx")}) {
            if (std::filesystem::exists(candidate)) {
                pitchConfig.modelPath = candidate;
                break;
            }
        }
        pImpl->pitch = std::make_unique<neural::CrepeLight>(pitchConfig);
        std::cout << "[Pipeline] Pitch: " << pImpl->pitch->backendName() << "\n";

        pImpl->running = true;
        std::cout << "\n[Pipeline] Full chain online:\n";
        std::cout << "  EmotionalCore -> DSP -> AI -> Spatial -> MixBus -> Scorer\n\n";
        return true;
    } catch (const std::exception& ex) {
        std::cerr << "[Pipeline] Init failed: " << ex.what() << "\n";
        return false;
    }
}

void CoreAudioPipeline::shutdown() {
    if (!pImpl->running) {
        return;
    }

    std::cout << "[Pipeline] Shutting down...\n";
    if (pImpl->ai) {
        pImpl->ai->unloadModel();
    }
    if (pImpl->trtEncoder) {
        pImpl->trtEncoder->destroy();
    }
    if (pImpl->trtDecoder) {
        pImpl->trtDecoder->destroy();
    }

    pImpl->emotional.reset();
    pImpl->spatial.reset();
    pImpl->mixBus.reset();
    pImpl->naturalness.reset();
    pImpl->inputGain.reset();
    pImpl->gate.reset();
    pImpl->comp.reset();
    pImpl->eq.reset();
    pImpl->outputGain.reset();
    pImpl->ai.reset();
    pImpl->pitch.reset();
    pImpl->running = false;
    std::cout << "[Pipeline] Shutdown complete.\n";
}

bool CoreAudioPipeline::isRunning() const noexcept {
    return pImpl && pImpl->running;
}

void CoreAudioPipeline::process(AudioBuffer& buf) {
    if (!pImpl->running) {
        throw std::runtime_error("process() called before init()");
    }

    const auto t0 = Clock::now();

    emotional::EmotionalField field;
    if (pImpl->emotional && pImpl->emotionalEnabled) {
        pImpl->emotional->tick(static_cast<int>(buf.frames()));
        field = pImpl->emotional->getField();
    }

    if (pImpl->emotionalEnabled) {
        {
            dsp::NoiseGate::Params params;
            params.thresholdDb = field.gateThreshDb;
            params.enabled = pImpl->config.gateEnabled;
            pImpl->gate->setParams(params);
        }

        {
            dsp::Compressor::Params params;
            params.thresholdDb = field.compThreshDb;
            params.ratio = field.compRatio;
            params.enabled = pImpl->config.compEnabled;
            pImpl->comp->setParams(params);
        }

        {
            auto band = pImpl->eq->getBand(2).filter;
            band.gainDb = field.presenceGainDb;
            pImpl->eq->setBand(2, band);
        }
        {
            auto band = pImpl->eq->getBand(3).filter;
            band.gainDb = field.airGainDb;
            pImpl->eq->setBand(3, band);
        }
        {
            auto band = pImpl->eq->getBand(1).filter;
            band.gainDb = field.bodyGainDb;
            pImpl->eq->setBand(1, band);
        }

        pImpl->aiParams.timbreDepth = field.timbreDepth;
        pImpl->aiParams.breathPreservation = field.breathPreservation;
        pImpl->aiParams.pitchShiftSemitones =
            field.pitchShift + field.driftPitch / 100.0f;

        pImpl->spatial->applyField(field);
        pImpl->mixBus->applyField(field);
    }

    const float inputPeak = buf.peakLinear();

    pImpl->inputGain->process(buf);
    pImpl->gate->process(buf);
    pImpl->comp->process(buf);
    pImpl->eq->process(buf);

    float pitchHz = 0.0f;
    float pitchConf = 0.0f;
    float pitchMs = 0.0f;
    bool pitchVoiced = false;
    if (pImpl->pitch) {
        const auto pitchStart = Clock::now();
        const auto track = pImpl->pitch->process(buf);
        pitchMs = std::chrono::duration<float, std::milli>(Clock::now() - pitchStart).count();
        if (!track.frames.empty()) {
            const auto& frame = track.frames.back();
            pitchHz = frame.hz;
            pitchConf = frame.confidence;
            pitchVoiced = frame.voiced;
        }
    }

    float aiMs = 0.0f;
    if (pImpl->config.aiEnabled && pImpl->ai && pImpl->ai->isModelLoaded()) {
        const auto aiStart = Clock::now();
        auto result = pImpl->ai->process(buf, pImpl->aiParams);
        aiMs = std::chrono::duration<float, std::milli>(Clock::now() - aiStart).count();
        if (result.output.frames() == buf.frames() &&
            result.output.channels() == buf.channels()) {
            std::memcpy(buf.data(), result.output.data(),
                        buf.totalSamples() * sizeof(float));
        }
    }

    if (pImpl->spatial) {
        pImpl->spatial->process(buf);
    }
    if (pImpl->mixBus) {
        pImpl->mixBus->process(buf);
    }
    pImpl->outputGain->process(buf);

    NaturalnessScorer::Score naturalnessScore;
    if (pImpl->naturalness) {
        naturalnessScore = pImpl->naturalness->analyze(buf);
        if (naturalnessScore.needsCorrection && pImpl->emotional && pImpl->emotionalEnabled) {
            if (naturalnessScore.widthCorrection != 0.0f) {
                const float currentIntensity = pImpl->emotional->getProfile().intensity;
                pImpl->emotional->setIntensity(
                    currentIntensity + naturalnessScore.widthCorrection * 0.1f);
            }
        }
    }

    const float blockMs =
        std::chrono::duration<float, std::milli>(Clock::now() - t0).count();
    pImpl->avgBlockMs = pImpl->avgBlockMs * 0.9f + blockMs * 0.1f;

    const uint32_t idx = pImpl->statsWriteIndex.load(std::memory_order_relaxed);
    ExtendedStats& stats = pImpl->statsBuffer[idx];
    stats.inputPeakDb = inputPeak > 1e-9f ? 20.0f * std::log10(inputPeak) : -120.0f;
    stats.outputPeakDb = buf.peakDb();
    stats.gateOpen = pImpl->gate->isOpen();
    stats.gateGainLinear = pImpl->gate->gainLinear();
    stats.compressionDb = pImpl->comp->gainReductionDb();
    stats.aiActive = pImpl->config.aiEnabled && pImpl->ai->isModelLoaded();
    stats.aiBackend = pImpl->ai->backendName();
    stats.pitchHz = pitchHz;
    stats.pitchConfidence = pitchConf;
    stats.pitchVoiced = pitchVoiced;
    stats.pitchBackend = pImpl->pitch ? pImpl->pitch->backendName() : "none";
    stats.lastBlockMs = blockMs;
    stats.perfMs = static_cast<double>(pImpl->avgBlockMs);
    stats.aiInferMs = aiMs;
    stats.pitchInferMs = pitchMs;
    stats.trtBuilding = pImpl->trtBuildPct.load();

    if (pImpl->emotional) {
        const auto profile = pImpl->emotional->getProfile();
        stats.emotionalState = static_cast<int>(profile.state);
        stats.emotionalIntensity = profile.intensity;
        stats.emotionalFatigue = pImpl->emotional->fatigue();
        stats.narrativeInfluence = pImpl->emotional->narrativeInfluence();
    }

    stats.naturalnessScore = naturalnessScore.overall;
    stats.stereoHealth = naturalnessScore.stereoHealth;
    stats.monoSafe = naturalnessScore.monoSafety > 0.5f;
    if (pImpl->spatial) {
        stats.stereoCorrelation = pImpl->spatial->stereoCorrelation();
    }

    pImpl->statsWriteIndex.store(idx ^ 1U, std::memory_order_release);

    if (pImpl->statsCallback) {
        pImpl->statsCallback(getStats());
    }
}

void CoreAudioPipeline::setInputGainDb(float db) {
    dsp::GainStage::Params params;
    params.gainDb = db;
    pImpl->inputGain->setParams(params);
}

void CoreAudioPipeline::setOutputGainDb(float db) {
    dsp::GainStage::Params params;
    params.gainDb = db;
    pImpl->outputGain->setParams(params);
}

void CoreAudioPipeline::setGateEnabled(bool enabled) {
    pImpl->config.gateEnabled = enabled;
    dsp::NoiseGate::Params params;
    params.enabled = enabled;
    pImpl->gate->setParams(params);
}

void CoreAudioPipeline::setGateThreshold(float thresholdDb) {
    dsp::NoiseGate::Params params;
    params.thresholdDb = thresholdDb;
    pImpl->gate->setParams(params);
}

void CoreAudioPipeline::setCompEnabled(bool enabled) {
    pImpl->config.compEnabled = enabled;
    dsp::Compressor::Params params;
    params.enabled = enabled;
    pImpl->comp->setParams(params);
}

void CoreAudioPipeline::setCompThreshold(float thresholdDb) {
    dsp::Compressor::Params params;
    params.thresholdDb = thresholdDb;
    pImpl->comp->setParams(params);
}

void CoreAudioPipeline::setCompRatio(float ratio) {
    dsp::Compressor::Params params;
    params.ratio = ratio;
    pImpl->comp->setParams(params);
}

void CoreAudioPipeline::setEQEnabled(bool enabled) {
    pImpl->config.eqEnabled = enabled;
    for (int i = 0; i < dsp::VocalEQ::kNumBands; ++i) {
        auto band = pImpl->eq->getBand(i).filter;
        band.enabled = enabled;
        pImpl->eq->setBand(i, band);
    }
}

void CoreAudioPipeline::setAIEnabled(bool enabled) {
    pImpl->config.aiEnabled = enabled;
}

bool CoreAudioPipeline::loadVoiceModel(const std::string& path) {
    return pImpl->ai && pImpl->ai->loadModel(path);
}

void CoreAudioPipeline::setAIPitchShift(float semitones) {
    pImpl->aiParams.pitchShiftSemitones = semitones;
}

void CoreAudioPipeline::setAITimbreDepth(float depth) {
    pImpl->aiParams.timbreDepth = depth;
}

void CoreAudioPipeline::setAIBreathPreservation(float amount) {
    pImpl->aiParams.breathPreservation = amount;
}

void CoreAudioPipeline::setEmotionalState(int state, float intensity) {
    if (!pImpl->emotional) {
        return;
    }
    const auto clamped = static_cast<emotional::EmotionalState>(
        std::clamp(state, 0, static_cast<int>(emotional::EmotionalState::COUNT) - 1));
    pImpl->emotional->setState(clamped, intensity);
}

void CoreAudioPipeline::setEmotionalIntensity(float intensity) {
    if (pImpl->emotional) {
        pImpl->emotional->setIntensity(intensity);
    }
}

void CoreAudioPipeline::setEmotionalBlend(int s1, float w1, int s2, float w2) {
    if (!pImpl->emotional) {
        return;
    }
    pImpl->emotional->setBlend(
        static_cast<emotional::EmotionalState>(s1), w1,
        static_cast<emotional::EmotionalState>(s2), w2);
}

void CoreAudioPipeline::setEmotionalEnabled(bool enabled) {
    pImpl->emotionalEnabled = enabled;
}

void CoreAudioPipeline::setBPM(float bpm) {
    if (pImpl->emotional) {
        pImpl->emotional->setBPM(bpm);
    }
}

EchoesEngine::Stats CoreAudioPipeline::getStats() const noexcept {
    const uint32_t idx = pImpl->statsWriteIndex.load(std::memory_order_acquire) ^ 1U;
    return static_cast<EchoesEngine::Stats>(pImpl->statsBuffer[idx]);
}

ExtendedStats CoreAudioPipeline::getExtendedStats() const noexcept {
    const uint32_t idx = pImpl->statsWriteIndex.load(std::memory_order_acquire) ^ 1U;
    return pImpl->statsBuffer[idx];
}

void CoreAudioPipeline::setStatsCallback(EchoesEngine::StatsCallback cb) {
    pImpl->statsCallback = std::move(cb);
}

const EchoesEngine::Config& CoreAudioPipeline::getConfig() const noexcept {
    return pImpl->config;
}

} // namespace echoes
