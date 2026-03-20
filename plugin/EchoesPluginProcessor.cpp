/**
 * EchoesPluginProcessor.cpp
 *
 * JUCE AudioProcessor — the DSP heart of the EchoesEngine VST3 plugin.
 *
 * This is the file that runs on the audio thread in your DAW.
 * Every processBlock() call goes: DAW buffer → EchoesVSTAdapter → DSP chain → back.
 *
 * Parameters exposed to the DAW (automation-ready):
 *   InputGain · OutputGain · GateThreshold · GateRelease
 *   CompThreshold · CompRatio · CompAttack · CompRelease · CompMakeup
 *   EQ Band 1-5 Gain · AITimbre · AIPitch · AIBreath · Bypass
 */

// Uncomment when building with JUCE:
// #include <JuceHeader.h>
// #include "EchoesPluginProcessor.h"

#include "EchoesEngine/Engine.h"
#include "EchoesEngine/vst/EchoesVSTAdapter.h"
#include "EchoesEngine/vst/IVSTPlugin.h"

#include <atomic>
#include <memory>
#include <array>

using namespace echoes;
using namespace echoes::vst;

// ─── Parameter IDs (match automation lane names in DAW) ───────────────────────
namespace Params {
  static const char* kInputGain    = "input_gain";
  static const char* kOutputGain   = "output_gain";
  static const char* kGateThresh   = "gate_threshold";
  static const char* kGateRelease  = "gate_release";
  static const char* kCompThresh   = "comp_threshold";
  static const char* kCompRatio    = "comp_ratio";
  static const char* kCompAttack   = "comp_attack";
  static const char* kCompRelease  = "comp_release";
  static const char* kCompMakeup   = "comp_makeup";
  static const char* kEQ80        = "eq_80hz";
  static const char* kEQ200       = "eq_200hz";
  static const char* kEQ500       = "eq_500hz";
  static const char* kEQ3k        = "eq_3khz";
  static const char* kEQ10k       = "eq_10khz";
  static const char* kAITimbre    = "ai_timbre";
  static const char* kAIPitch     = "ai_pitch";
  static const char* kAIBreath    = "ai_breath";
  static const char* kBypass      = "bypass";
}

/**
 * EchoesPluginProcessor
 *
 * When building with JUCE, change to:
 *   class EchoesPluginProcessor : public juce::AudioProcessor {
 *
 * Then implement createPluginFilter() at the bottom.
 */
class EchoesPluginProcessor /* : public juce::AudioProcessor */ {
public:
    EchoesPluginProcessor()
    /* : AudioProcessor(
        BusesProperties()
          .withInput ("Input",  juce::AudioChannelSet::stereo(), true)
          .withOutput("Output", juce::AudioChannelSet::stereo(), true)
      ) */
    {}

    ~EchoesPluginProcessor() override { releaseResources(); }

    // ── JUCE: Name ────────────────────────────────────────────────────────────
    // const juce::String getName() const override { return "EchoesEngine"; }
    // bool  acceptsMidi()  const override { return false; }
    // bool  producesMidi() const override { return false; }
    // double getTailLengthSeconds() const override { return 0.5; }

    // ── Prepare ───────────────────────────────────────────────────────────────
    void prepareToPlay(double sampleRate, int samplesPerBlock) {
        EchoesEngine::Config cfg;
        cfg.sampleRate  = static_cast<float>(sampleRate);
        cfg.blockSize   = static_cast<uint32_t>(samplesPerBlock);
        cfg.numChannels = 2;

        m_adapter = std::make_unique<EchoesVSTAdapter>();
        m_adapter->initialize(sampleRate, samplesPerBlock);

        // Apply default parameters
        m_adapter->setParameter(ParamID::InputGainDb,      toNorm(0.0f,   -24.f, 24.f));
        m_adapter->setParameter(ParamID::OutputGainDb,     toNorm(0.0f,   -24.f, 12.f));
        m_adapter->setParameter(ParamID::GateThresholdDb,  toNorm(-40.f,  -80.f,  0.f));
        m_adapter->setParameter(ParamID::CompThresholdDb,  toNorm(-18.f,  -60.f,  0.f));
        m_adapter->setParameter(ParamID::CompRatio,        toNorm(4.0f,     1.f, 20.f));
        m_adapter->setParameter(ParamID::CompAttackMs,     toNorm(5.0f,   0.1f, 200.f));
        m_adapter->setParameter(ParamID::CompReleaseMs,    toNorm(80.f,    10.f, 2000.f));
        m_adapter->setParameter(ParamID::CompMakeupGainDb, toNorm(6.0f,    0.f,  24.f));
    }

    // ── Release ───────────────────────────────────────────────────────────────
    void releaseResources() {
        if (m_adapter) m_adapter->shutdown();
        m_adapter.reset();
    }

    // ── PROCESS BLOCK ─────────────────────────────────────────────────────────
    /**
     * Called by the DAW at block rate on the AUDIO THREAD.
     * This is the hot path — must be real-time safe (no allocations, no locks).
     *
     * With JUCE, uncomment and adapt:
     *
     * void processBlock(juce::AudioBuffer<float>& buffer,
     *                   juce::MidiBuffer& midiMessages) override
     * {
     *     juce::ScopedNoDenormals noDenormals;
     *     if (!m_adapter) return;
     *
     *     const int numCh     = buffer.getNumChannels();
     *     const int numFrames = buffer.getNumSamples();
     *
     *     float* channels[32];
     *     for (int ch = 0; ch < std::min(numCh, 32); ++ch)
     *         channels[ch] = buffer.getWritePointer(ch);
     *
     *     ProcessContext ctx;
     *     ctx.sampleRate = getSampleRate();
     *     if (auto pos = getPlayHead()->getPosition()) {
     *         ctx.tempo    = pos->getBpm().orFallback(120.0);
     *         ctx.isPlaying= pos->getIsPlaying();
     *     }
     *
     *     m_adapter->processBlock(channels, numCh, numFrames, ctx);
     *
     *     // Feed metering to UI thread (lock-free)
     *     auto stats = m_adapter->getStats();
     *     m_meterIn .store(stats.inputPeakDb,  std::memory_order_relaxed);
     *     m_meterOut.store(stats.outputPeakDb, std::memory_order_relaxed);
     *     m_compGR  .store(stats.compressionDb,std::memory_order_relaxed);
     *     m_gateOpen.store(stats.gateOpen,     std::memory_order_relaxed);
     * }
     */

    // ── PARAMETER HANDLING ────────────────────────────────────────────────────
    /**
     * With JUCE AudioProcessorValueTreeState, add to constructor:
     *
     *   m_apvts(*this, nullptr, "State", createParameterLayout())
     *
     * Then in processBlock, read params:
     *   float gainDb = *m_apvts.getRawParameterValue(Params::kInputGain);
     *   m_adapter->setParameter(ParamID::InputGainDb, toNorm(gainDb, -24, 24));
     */

    // Parameter layout for APVTS:
    /*
    static juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout() {
        std::vector<std::unique_ptr<juce::RangedAudioParameter>> params;

        auto addDb = [&](const char* id, const char* name, float def, float lo, float hi) {
            params.push_back(std::make_unique<juce::AudioParameterFloat>(
                id, name,
                juce::NormalisableRange<float>(lo, hi, 0.01f),
                def,
                juce::AudioParameterFloatAttributes()
                    .withStringFromValueFunction([](float v, int) {
                        return juce::String(v, 1) + " dB"; })
            ));
        };

        addDb(Params::kInputGain,   "Input Gain",        0.f, -24.f, 24.f);
        addDb(Params::kOutputGain,  "Output Gain",       0.f, -24.f, 12.f);
        addDb(Params::kGateThresh,  "Gate Threshold",  -40.f, -80.f,  0.f);
        addDb(Params::kCompThresh,  "Comp Threshold",  -18.f, -60.f,  0.f);
        addDb(Params::kCompMakeup,  "Comp Makeup",       6.f,   0.f, 24.f);
        addDb(Params::kEQ80,       "EQ 80Hz",           0.f, -12.f, 12.f);
        addDb(Params::kEQ200,      "EQ 200Hz",         -2.f, -12.f, 12.f);
        addDb(Params::kEQ500,      "EQ 500Hz",         -1.5f,-12.f, 12.f);
        addDb(Params::kEQ3k,       "EQ 3kHz",           3.f, -12.f, 12.f);
        addDb(Params::kEQ10k,      "EQ 10kHz",          2.f, -12.f, 12.f);

        params.push_back(std::make_unique<juce::AudioParameterFloat>(
            Params::kCompRatio, "Comp Ratio",
            juce::NormalisableRange<float>(1.f, 20.f, 0.1f), 4.f));

        params.push_back(std::make_unique<juce::AudioParameterFloat>(
            Params::kGateRelease, "Gate Release ms",
            juce::NormalisableRange<float>(10.f, 2000.f, 1.f), 100.f));

        params.push_back(std::make_unique<juce::AudioParameterFloat>(
            Params::kAITimbre, "AI Timbre",
            juce::NormalisableRange<float>(0.f, 1.f, 0.01f), 0.f));

        params.push_back(std::make_unique<juce::AudioParameterFloat>(
            Params::kAIPitch, "AI Pitch Shift",
            juce::NormalisableRange<float>(-12.f, 12.f, 0.01f), 0.f));

        params.push_back(std::make_unique<juce::AudioParameterBool>(
            Params::kBypass, "Bypass", false));

        return { params.begin(), params.end() };
    }
    */

    // ── State save/restore ────────────────────────────────────────────────────
    /*
    void getStateInformation(juce::MemoryBlock& dest) override {
        auto state = m_apvts.copyState();
        std::unique_ptr<juce::XmlElement> xml(state.createXml());
        copyXmlToBinary(*xml, dest);
    }

    void setStateInformation(const void* data, int size) override {
        std::unique_ptr<juce::XmlElement> xml(getXmlFromBinary(data, size));
        if (xml && xml->hasTagName(m_apvts.state.getType()))
            m_apvts.replaceState(juce::ValueTree::fromXml(*xml));
    }
    */

    // ── Metering (read from UI/editor thread, lock-free) ──────────────────────
    float inputPeakDb()   const noexcept { return m_meterIn .load(std::memory_order_relaxed); }
    float outputPeakDb()  const noexcept { return m_meterOut.load(std::memory_order_relaxed); }
    float compressionDb() const noexcept { return m_compGR  .load(std::memory_order_relaxed); }
    bool  gateIsOpen()    const noexcept { return m_gateOpen.load(std::memory_order_relaxed); }

private:
    std::unique_ptr<EchoesVSTAdapter> m_adapter;
    // juce::AudioProcessorValueTreeState m_apvts;

    // Lock-free meter values (written on audio thread, read on UI thread)
    std::atomic<float> m_meterIn  {-120.f};
    std::atomic<float> m_meterOut {-120.f};
    std::atomic<float> m_compGR   {0.f};
    std::atomic<bool>  m_gateOpen {true};

    static float toNorm(float val, float lo, float hi) noexcept {
        return (val - lo) / (hi - lo);
    }
};

// ── Plugin factory (uncomment when JUCE is available) ─────────────────────────
/*
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new EchoesPluginProcessor();
}
*/
