#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

#include "EchoesEngine/vst/EchoesVSTAdapter.h"

#include <atomic>

class EchoesPluginProcessor final : public juce::AudioProcessor {
public:
    EchoesPluginProcessor();
    ~EchoesPluginProcessor() override;

    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;
    bool isBusesLayoutSupported(const BusesLayout& layouts) const override;
    void processBlock(juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return "EchoesEngine"; }
    bool acceptsMidi() const override { return false; }
    bool producesMidi() const override { return false; }
    bool isMidiEffect() const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram(int) override {}
    const juce::String getProgramName(int) override { return "Default"; }
    void changeProgramName(int, const juce::String&) override {}

    void getStateInformation(juce::MemoryBlock& destData) override;
    void setStateInformation(const void* data, int sizeInBytes) override;

    float inputPeakDb() const noexcept;
    float outputPeakDb() const noexcept;
    float compressionDb() const noexcept;
    bool gateIsOpen() const noexcept;

private:
    echoes::vst::EchoesVSTAdapter adapter_;
    std::atomic<float> inputPeakDb_ {-120.0f};
    std::atomic<float> outputPeakDb_ {-120.0f};
    std::atomic<float> compressionDb_ {0.0f};
    std::atomic<bool> gateOpen_ {true};
};
