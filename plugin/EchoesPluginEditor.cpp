#include "EchoesPluginEditor.h"

#include "EchoesPluginProcessor.h"

EchoesPluginEditor::EchoesPluginEditor(EchoesPluginProcessor& processor)
    : juce::AudioProcessorEditor(&processor), processor_(processor) {
    setSize(520, 280);
    startTimerHz(24);
}

void EchoesPluginEditor::paint(juce::Graphics& g) {
    g.fillAll(juce::Colour::fromRGB(12, 12, 16));

    g.setColour(juce::Colour::fromRGB(231, 233, 238));
    g.setFont(juce::FontOptions(24.0f));
    g.drawText("EchoesEngine", 24, 18, getWidth() - 48, 30, juce::Justification::centredLeft);

    g.setColour(juce::Colour::fromRGB(114, 125, 148));
    g.setFont(juce::FontOptions(14.0f));
    g.drawText("JUCE plugin bridge for EchoesVSTAdapter", 24, 54, getWidth() - 48, 20, juce::Justification::centredLeft);

    drawMeter(g, juce::Rectangle<int>(24, 104, getWidth() - 48, 28), "Input Peak", inputPeakDb_);
    drawMeter(g, juce::Rectangle<int>(24, 152, getWidth() - 48, 28), "Output Peak", outputPeakDb_);
    drawMeter(g, juce::Rectangle<int>(24, 200, getWidth() - 48, 28), "Compression", compressionDb_);

    g.setColour(gateOpen_ ? juce::Colour::fromRGB(70, 176, 109) : juce::Colour::fromRGB(190, 69, 69));
    g.setFont(juce::FontOptions(13.0f));
    g.drawText(gateOpen_ ? "Gate Open" : "Gate Closed", 24, 240, 160, 20, juce::Justification::centredLeft);
}

void EchoesPluginEditor::resized() {}

void EchoesPluginEditor::timerCallback() {
    inputPeakDb_ = processor_.inputPeakDb();
    outputPeakDb_ = processor_.outputPeakDb();
    compressionDb_ = processor_.compressionDb();
    gateOpen_ = processor_.gateIsOpen();
    repaint();
}

void EchoesPluginEditor::drawMeter(juce::Graphics& g, juce::Rectangle<int> area, const juce::String& label, float valueDb) {
    const auto clamped = juce::jlimit(-60.0f, 6.0f, valueDb);
    const auto normalized = juce::jmap(clamped, -60.0f, 6.0f, 0.0f, 1.0f);

    g.setColour(juce::Colour::fromRGB(36, 39, 49));
    g.fillRoundedRectangle(area.toFloat(), 6.0f);

    auto fillArea = area.reduced(2);
    fillArea.setWidth(static_cast<int>(fillArea.getWidth() * normalized));
    g.setColour(juce::Colour::fromRGB(78, 163, 255));
    g.fillRoundedRectangle(fillArea.toFloat(), 5.0f);

    g.setColour(juce::Colour::fromRGB(231, 233, 238));
    g.setFont(juce::FontOptions(13.0f));
    g.drawText(label, area.removeFromLeft(120), juce::Justification::centredLeft);
    g.drawText(juce::String(valueDb, 1) + " dB", area, juce::Justification::centredRight);
}
