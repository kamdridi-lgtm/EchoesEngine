#pragma once

#include "EchoesEngine/Engine.h"

#include <memory>

namespace echoes {

class CoreAudioPipeline {
public:
    explicit CoreAudioPipeline(const EchoesEngine::Config& config);
    ~CoreAudioPipeline();

    CoreAudioPipeline(const CoreAudioPipeline&) = delete;
    CoreAudioPipeline& operator=(const CoreAudioPipeline&) = delete;

    bool init();
    void shutdown();
    bool isRunning() const noexcept;

    void process(AudioBuffer& buffer);

    void setInputGainDb(float db);
    void setOutputGainDb(float db);
    void setGateEnabled(bool enabled);
    void setGateThreshold(float thresholdDb);
    void setCompEnabled(bool enabled);
    void setCompThreshold(float thresholdDb);
    void setCompRatio(float ratio);
    void setEQEnabled(bool enabled);
    void setAIEnabled(bool enabled);
    bool loadVoiceModel(const std::string& path);
    void setAIPitchShift(float semitones);
    void setAITimbreDepth(float depth);
    void setAIBreathPreservation(float amount);

    void setEmotionalState(int state, float intensity);
    void setEmotionalIntensity(float intensity);
    void setEmotionalBlend(int s1, float w1, int s2, float w2);
    void setEmotionalEnabled(bool enabled);
    void setBPM(float bpm);

    EchoesEngine::Stats getStats() const noexcept;
    ExtendedStats getExtendedStats() const noexcept;
    void setStatsCallback(EchoesEngine::StatsCallback cb);
    const EchoesEngine::Config& getConfig() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> pImpl;
};

} // namespace echoes
