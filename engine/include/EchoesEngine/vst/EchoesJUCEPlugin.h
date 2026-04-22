/**
 * EchoesJUCEPlugin.h
 *
 * JUCE AudioProcessor wrapper for EchoesEngine.
 *
 * Produces:
 *   - VST3  (.vst3)    — Windows / Mac / Linux
 *   - AU    (.component) — Mac / Logic Pro
 *   - AAX   (.aaxplugin) — Pro Tools (requires PACE license)
 *   - Standalone app   — Direct ASIO/CoreAudio
 *
 * To activate:
 *   1. Download JUCE: https://juce.com
 *   2. Create new "Audio Plugin" project in Projucer
 *   3. Add EchoesEngine as a module (add engine/ as a source path)
 *   4. Replace the default AudioProcessor with this file
 *   5. Build with Projucer → your IDE → Compile
 *
 * CMake alternative (JUCE 6+):
 *   juce_add_plugin(EchoesPlugin
 *     PLUGIN_MANUFACTURER_CODE KDRIDI
 *     PLUGIN_CODE ECHV
 *     FORMATS VST3 AU Standalone
 *     PRODUCT_NAME "Echoes Engine"
 *   )
 *   target_link_libraries(EchoesPlugin PRIVATE EchoesEngine::Engine juce::juce_audio_processors)
 */

#pragma once

// ── This file requires JUCE ───────────────────────────────────────────────────
// Uncomment when JUCE is on your include path:
// #include <JuceHeader.h>

// For now, include our standalone headers so the code is correct and ready
#include "EchoesEngine/Engine.h"
#include "EchoesEngine/vst/EchoesVSTAdapter.h"
#include "EchoesEngine/vst/IVSTPlugin.h"

namespace echoes::vst {

/**
 * EchoesJUCEProcessor
 *
 * When JUCE is available, change:
 *   class EchoesJUCEProcessor
 * to:
 *   class EchoesJUCEProcessor : public juce::AudioProcessor
 *
 * And uncomment all the JUCE method implementations below.
 */
class EchoesJUCEProcessor /* : public juce::AudioProcessor */ {
public:
    EchoesJUCEProcessor() = default;
    ~EchoesJUCEProcessor() = default;

    // ── Plugin identity ───────────────────────────────────────────────────────
    static constexpr const char* kName    = PluginInfo::kName;
    static constexpr const char* kVendor  = PluginInfo::kVendor;
    static constexpr const char* kVersion = PluginInfo::kVersion;

    // ── JUCE: prepareToPlay ───────────────────────────────────────────────────
    // Called by the host before streaming starts
    void prepareToPlay(double sampleRate, int samplesPerBlock) {
        m_adapter.initialize(sampleRate, samplesPerBlock);

        // juce::AudioProcessorGraph setup would go here
        // m_reverbBuffer.setSize(2, samplesPerBlock);
    }

    // ── JUCE: releaseResources ────────────────────────────────────────────────
    void releaseResources() {
        m_adapter.shutdown();
    }

    // ── JUCE: processBlock ────────────────────────────────────────────────────
    // This is the hot path — called on the audio thread at block rate
    /*
    void processBlock(juce::AudioBuffer<float>& buffer,
                      juce::MidiBuffer& midiMessages) override
    {
        juce::ScopedNoDenormals noDenormals;

        // Build raw float** for our adapter
        float* channels[32];
        int numCh = std::min(buffer.getNumChannels(), 32);
        for (int ch = 0; ch < numCh; ++ch)
            channels[ch] = buffer.getWritePointer(ch);

        ProcessContext ctx;
        ctx.sampleRate = getSampleRate();
        ctx.isPlaying  = isNonRealtime() ? false : true;

        auto& pi = getPlayHead()->getPosition();
        if (pi.hasValue()) {
            ctx.tempo = pi->getBpm().orFallback(120.0);
            ctx.isPlaying = pi->getIsPlaying();
        }

        m_adapter.processBlock(channels, numCh, buffer.getNumSamples(), ctx);

        // Feed metering to UI (lock-free)
        auto stats = m_adapter.getStats();
        m_meterIn .store(stats.inputPeakDb);
        m_meterOut.store(stats.outputPeakDb);
        m_gateOpen.store(stats.gateOpen);
        m_compGR  .store(stats.compressionDb);
    }
    */

    // ── JUCE: parameter management ────────────────────────────────────────────
    /*
    int  getNumParameters()   override { return static_cast<int>(kParamCount); }
    void setParameter(int index, float value) override {
        auto id = static_cast<ParamID>(index);
        m_adapter.setParameter(id, value);
    }
    float getParameter(int index) override {
        return m_adapter.getParameter(static_cast<ParamID>(index));
    }
    */

    // ── JUCE: plugin info ─────────────────────────────────────────────────────
    /*
    const juce::String getName() const override { return kName; }
    bool  acceptsMidi()  const override { return false; }
    bool  producesMidi() const override { return false; }
    double getTailLengthSeconds() const override { return 0.5; }
    int  getNumPrograms() override { return 1; }
    int  getCurrentProgram() override { return 0; }
    void setCurrentProgram(int) override {}
    const juce::String getProgramName(int) override { return "Default"; }
    void changeProgramName(int, const juce::String&) override {}
    bool isBusesLayoutSupported(const BusesLayout& layouts) const override {
        return layouts.getMainInputChannelSet()  == juce::AudioChannelSet::stereo()
            && layouts.getMainOutputChannelSet() == juce::AudioChannelSet::stereo();
    }
    */

    // ── State save/restore ────────────────────────────────────────────────────
    /*
    void getStateInformation(juce::MemoryBlock& dest) override {
        // Save all params as XML
        auto xml = std::make_unique<juce::XmlElement>("EchoesEngine");
        // ... serialize m_adapter params
        copyXmlToBinary(*xml, dest);
    }
    void setStateInformation(const void* data, int size) override {
        // Restore params from XML
        if (auto xml = getXmlFromBinary(data, size)) {
            // ... restore m_adapter params
        }
    }
    */

    // ── Metering (read from UI / editor thread) ───────────────────────────────
    float inputPeakDb()   const noexcept { return m_meterIn.load();  }
    float outputPeakDb()  const noexcept { return m_meterOut.load(); }
    float compressionDb() const noexcept { return m_compGR.load();   }
    bool  gateIsOpen()    const noexcept { return m_gateOpen.load(); }

private:
    EchoesVSTAdapter m_adapter;

    // Lock-free metering atoms (written by audio thread, read by UI thread)
    std::atomic<float> m_meterIn  {-120.0f};
    std::atomic<float> m_meterOut {-120.0f};
    std::atomic<float> m_compGR   {0.0f};
    std::atomic<bool>  m_gateOpen {true};

    static constexpr int kParamCount = 16;
};

} // namespace echoes::vst

/**
 * JUCE plugin factory (uncomment when JUCE is available):
 *
 * juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter() {
 *     return new echoes::vst::EchoesJUCEProcessor();
 * }
 */
