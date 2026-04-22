#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_gui_basics/juce_gui_basics.h>

class EchoesPluginProcessor;

class EchoesPluginEditor final : public juce::AudioProcessorEditor, private juce::Timer {
public:
    explicit EchoesPluginEditor(EchoesPluginProcessor& processor);

    void paint(juce::Graphics& g) override;
    void resized() override;

private:
    void timerCallback() override;
    static void drawMeter(juce::Graphics& g, juce::Rectangle<int> area, const juce::String& label, float valueDb);

    EchoesPluginProcessor& processor_;
    float inputPeakDb_ {-120.0f};
    float outputPeakDb_ {-120.0f};
    float compressionDb_ {0.0f};
    bool gateOpen_ {true};
};
