#pragma once
#include "EchoesEngine/audio/AudioBuffer.h"
#include <memory>
#include <string>
#include <functional>

namespace echoes {

// Forward-declare so EchoesEngine can reference it before full definition
struct ExtendedStats;

class EchoesEngine {
public:
    struct Config {
        float       sampleRate    = 44100.0f;
        uint32_t    blockSize     = 512;
        uint32_t    numChannels   = 2;
        float       inputGainDb   = 0.0f;
        float       outputGainDb  = 0.0f;
        bool        gateEnabled   = true;
        bool        compEnabled   = true;
        bool        eqEnabled     = true;
        bool        aiEnabled     = false;
        std::string voiceModelPath;
    };

    struct Stats {
        float       inputPeakDb    = -120.0f;
        float       outputPeakDb   = -120.0f;
        float       gateGainLinear =    1.0f;
        float       compressionDb  =    0.0f;
        bool        gateOpen       = true;
        bool        aiActive       = false;
        std::string aiBackend;
    };

    using StatsCallback = std::function<void(const Stats&)>;

    explicit EchoesEngine(const Config& config);
    ~EchoesEngine();
    EchoesEngine(const EchoesEngine&)            = delete;
    EchoesEngine& operator=(const EchoesEngine&) = delete;

    bool init();
    void shutdown();
    bool isRunning() const noexcept;

    void process(AudioBuffer& buffer);
    void process(float* interleavedData, int numSamples, int numChannels = 2);

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

    // Emotional API
    void setEmotionalState(int state, float intensity);  // state: 0=Neutral..7=Power
    void setEmotionalIntensity(float intensity);         // 0..1
    void setEmotionalBlend(int s1, float w1, int s2, float w2);
    void setEmotionalEnabled(bool enabled);
    void setBPM(float bpm);

    Stats         getStats()         const noexcept;
    ExtendedStats getExtendedStats() const noexcept;
    void          setStatsCallback(StatsCallback cb);
    static std::string version() noexcept;
    const Config&      getConfig() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> pImpl;
};

struct ExtendedStats : EchoesEngine::Stats {
    float       pitchHz         =   0.f;
    float       pitchConfidence =   0.f;
    bool        pitchVoiced     = false;
    std::string pitchBackend;
    float       lastBlockMs     =   0.f;
    float       aiInferMs       =   0.f;
    float       pitchInferMs    =   0.f;
    float       trtBuilding     =  -1.f;
    double      perfMs          =   0.0;    // moving average block time (ms)

    // Emotional
    int         emotionalState      = 0;      // EmotionalState enum
    float       emotionalIntensity  = 0.f;
    float       emotionalFatigue    = 0.f;    // 0..1 fatigue cumulative
    float       narrativeInfluence  = 0.f;    // -1..+1 trajectoire long-terme

    // Naturalness
    float       naturalnessScore    = 1.f;    // 0..1 (1=parfait)
    float       stereoHealth        = 1.f;
    bool        monoSafe            = true;
    float       stereoCorrelation   = 1.f;
};

} // namespace echoes
