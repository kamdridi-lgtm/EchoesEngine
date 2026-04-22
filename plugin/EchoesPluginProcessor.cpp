#include "EchoesPluginProcessor.h"

#include "EchoesPluginEditor.h"

#include <array>
#include <algorithm>

EchoesPluginProcessor::EchoesPluginProcessor()
    : juce::AudioProcessor(BusesProperties()
          .withInput("Input", juce::AudioChannelSet::stereo(), true)
          .withOutput("Output", juce::AudioChannelSet::stereo(), true)) {}

EchoesPluginProcessor::~EchoesPluginProcessor() {
    releaseResources();
}

void EchoesPluginProcessor::prepareToPlay(double sampleRate, int samplesPerBlock) {
    adapter_.initialize(sampleRate, samplesPerBlock);
}

void EchoesPluginProcessor::releaseResources() {
    adapter_.shutdown();
}

bool EchoesPluginProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const {
    return layouts.getMainInputChannelSet() == juce::AudioChannelSet::stereo()
        && layouts.getMainOutputChannelSet() == juce::AudioChannelSet::stereo();
}

void EchoesPluginProcessor::processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages) {
    juce::ignoreUnused(midiMessages);
    juce::ScopedNoDenormals noDenormals;

    const auto totalInputChannels = getTotalNumInputChannels();
    const auto totalOutputChannels = getTotalNumOutputChannels();
    for (auto channel = totalInputChannels; channel < totalOutputChannels; ++channel) {
        buffer.clear(channel, 0, buffer.getNumSamples());
    }

    constexpr int kMaxChannels = 32;
    std::array<float*, kMaxChannels> channels{};
    const auto numChannels = std::min(buffer.getNumChannels(), kMaxChannels);
    for (int channel = 0; channel < numChannels; ++channel) {
        channels[static_cast<size_t>(channel)] = buffer.getWritePointer(channel);
    }

    echoes::vst::ProcessContext context;
    context.sampleRate = getSampleRate();
    if (auto* hostPlayHead = getPlayHead()) {
        if (auto position = hostPlayHead->getPosition()) {
            context.tempo = position->getBpm().orFallback(120.0);
            context.isPlaying = position->getIsPlaying();
            context.isRecording = position->getIsRecording();
            const auto timeSig = position->getTimeSignature();
            if (timeSig.hasValue()) {
                context.timeSigNumerator = timeSig->numerator;
                context.timeSigDenominator = timeSig->denominator;
            }
            context.projectTimeSec = position->getTimeInSeconds().orFallback(0.0);
        }
    }

    adapter_.processBlock(channels.data(), numChannels, buffer.getNumSamples(), context);

    const auto stats = adapter_.getStats();
    inputPeakDb_.store(stats.inputPeakDb, std::memory_order_relaxed);
    outputPeakDb_.store(stats.outputPeakDb, std::memory_order_relaxed);
    compressionDb_.store(stats.compressionDb, std::memory_order_relaxed);
    gateOpen_.store(stats.gateOpen, std::memory_order_relaxed);
}

juce::AudioProcessorEditor* EchoesPluginProcessor::createEditor() {
    return new EchoesPluginEditor(*this);
}

void EchoesPluginProcessor::getStateInformation(juce::MemoryBlock& destData) {
    juce::MemoryOutputStream stream(destData, false);
    stream.writeString("EchoesEnginePluginState:v1");
}

void EchoesPluginProcessor::setStateInformation(const void* data, int sizeInBytes) {
    juce::ignoreUnused(data, sizeInBytes);
}

float EchoesPluginProcessor::inputPeakDb() const noexcept {
    return inputPeakDb_.load(std::memory_order_relaxed);
}

float EchoesPluginProcessor::outputPeakDb() const noexcept {
    return outputPeakDb_.load(std::memory_order_relaxed);
}

float EchoesPluginProcessor::compressionDb() const noexcept {
    return compressionDb_.load(std::memory_order_relaxed);
}

bool EchoesPluginProcessor::gateIsOpen() const noexcept {
    return gateOpen_.load(std::memory_order_relaxed);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter() {
    return new EchoesPluginProcessor();
}
