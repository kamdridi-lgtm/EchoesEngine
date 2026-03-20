#include "EchoesEngine/Engine.h"

#include "EchoesEngine/CoreAudioPipeline.h"

#include <memory>
#include <utility>

namespace echoes {

struct EchoesEngine::Impl {
    Config config;
    AudioBuffer interleavedScratch;
    std::unique_ptr<CoreAudioPipeline> pipeline;

    explicit Impl(const Config& cfg)
        : config(cfg)
        , interleavedScratch(cfg.numChannels, cfg.blockSize, static_cast<uint32_t>(cfg.sampleRate))
        , pipeline(std::make_unique<CoreAudioPipeline>(cfg)) {}
};

EchoesEngine::EchoesEngine(const Config& config)
    : pImpl(std::make_unique<Impl>(config)) {}

EchoesEngine::~EchoesEngine() {
    if (pImpl && pImpl->pipeline) {
        pImpl->pipeline->shutdown();
    }
}

bool EchoesEngine::init() {
    return pImpl->pipeline->init();
}

void EchoesEngine::shutdown() {
    pImpl->pipeline->shutdown();
}

bool EchoesEngine::isRunning() const noexcept {
    return pImpl && pImpl->pipeline && pImpl->pipeline->isRunning();
}

void EchoesEngine::process(AudioBuffer& buffer) {
    pImpl->pipeline->process(buffer);
}

void EchoesEngine::process(float* data, int numSamples, int numChannels) {
    if (!data || numSamples <= 0 || numChannels <= 0) {
        return;
    }

    const uint32_t frames = static_cast<uint32_t>(numSamples / numChannels);
    pImpl->interleavedScratch.resize(
        static_cast<uint32_t>(numChannels),
        frames,
        static_cast<uint32_t>(pImpl->config.sampleRate));
    pImpl->interleavedScratch.copyFromInterleaved(data, static_cast<size_t>(numSamples));
    pImpl->pipeline->process(pImpl->interleavedScratch);
    pImpl->interleavedScratch.copyToInterleaved(data, static_cast<size_t>(numSamples));
}

void EchoesEngine::setInputGainDb(float db) { pImpl->pipeline->setInputGainDb(db); }
void EchoesEngine::setOutputGainDb(float db) { pImpl->pipeline->setOutputGainDb(db); }
void EchoesEngine::setGateEnabled(bool enabled) { pImpl->pipeline->setGateEnabled(enabled); }
void EchoesEngine::setGateThreshold(float thresholdDb) { pImpl->pipeline->setGateThreshold(thresholdDb); }
void EchoesEngine::setCompEnabled(bool enabled) { pImpl->pipeline->setCompEnabled(enabled); }
void EchoesEngine::setCompThreshold(float thresholdDb) { pImpl->pipeline->setCompThreshold(thresholdDb); }
void EchoesEngine::setCompRatio(float ratio) { pImpl->pipeline->setCompRatio(ratio); }
void EchoesEngine::setEQEnabled(bool enabled) { pImpl->pipeline->setEQEnabled(enabled); }
void EchoesEngine::setAIEnabled(bool enabled) { pImpl->pipeline->setAIEnabled(enabled); }
bool EchoesEngine::loadVoiceModel(const std::string& path) { return pImpl->pipeline->loadVoiceModel(path); }
void EchoesEngine::setAIPitchShift(float semitones) { pImpl->pipeline->setAIPitchShift(semitones); }
void EchoesEngine::setAITimbreDepth(float depth) { pImpl->pipeline->setAITimbreDepth(depth); }
void EchoesEngine::setAIBreathPreservation(float amount) { pImpl->pipeline->setAIBreathPreservation(amount); }
void EchoesEngine::setEmotionalState(int state, float intensity) { pImpl->pipeline->setEmotionalState(state, intensity); }
void EchoesEngine::setEmotionalIntensity(float intensity) { pImpl->pipeline->setEmotionalIntensity(intensity); }
void EchoesEngine::setEmotionalBlend(int s1, float w1, int s2, float w2) { pImpl->pipeline->setEmotionalBlend(s1, w1, s2, w2); }
void EchoesEngine::setEmotionalEnabled(bool enabled) { pImpl->pipeline->setEmotionalEnabled(enabled); }
void EchoesEngine::setBPM(float bpm) { pImpl->pipeline->setBPM(bpm); }
EchoesEngine::Stats EchoesEngine::getStats() const noexcept { return pImpl->pipeline->getStats(); }
ExtendedStats EchoesEngine::getExtendedStats() const noexcept { return pImpl->pipeline->getExtendedStats(); }
void EchoesEngine::setStatsCallback(StatsCallback cb) { pImpl->pipeline->setStatsCallback(std::move(cb)); }

std::string EchoesEngine::version() noexcept {
    return "EchoesEngine v0.5.0 [Hardened Realtime]";
}

const EchoesEngine::Config& EchoesEngine::getConfig() const noexcept {
    return pImpl->pipeline->getConfig();
}

} // namespace echoes
