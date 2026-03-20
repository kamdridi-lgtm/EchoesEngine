#pragma once
#include <vector>
#include <cstdint>
#include <cassert>
#include <cmath>
#include <algorithm>
#include <stdexcept>

namespace echoes {

/// Interleaved 32-bit float audio buffer.
/// All DSP modules operate on this type.
class AudioBuffer {
public:
    AudioBuffer() = default;

    AudioBuffer(uint32_t numChannels, uint32_t numFrames, uint32_t sampleRate)
        : m_channels(numChannels)
        , m_frames(numFrames)
        , m_sampleRate(sampleRate)
        , m_data(numChannels * numFrames, 0.0f)
    {}

    void resize(uint32_t numChannels, uint32_t numFrames, uint32_t sampleRate) {
        m_channels = numChannels;
        m_frames = numFrames;
        m_sampleRate = sampleRate;
        m_data.resize(static_cast<size_t>(numChannels) * numFrames, 0.0f);
    }

    // ── Accessors ─────────────────────────────────────────────────────────────
    [[nodiscard]] uint32_t channels()    const noexcept { return m_channels; }
    [[nodiscard]] uint32_t frames()      const noexcept { return m_frames; }
    [[nodiscard]] uint32_t sampleRate()  const noexcept { return m_sampleRate; }
    [[nodiscard]] bool     empty()       const noexcept { return m_data.empty(); }
    [[nodiscard]] float*   data()        noexcept       { return m_data.data(); }
    [[nodiscard]] const float* data()    const noexcept { return m_data.data(); }
    [[nodiscard]] size_t   totalSamples()const noexcept { return m_data.size(); }

    [[nodiscard]] float durationSeconds() const noexcept {
        return m_sampleRate > 0
            ? static_cast<float>(m_frames) / static_cast<float>(m_sampleRate)
            : 0.0f;
    }

    // Sample access: buffer[frame * channels + channel]
    [[nodiscard]] float& at(uint32_t frame, uint32_t ch) {
        return m_data[frame * m_channels + ch];
    }
    [[nodiscard]] float at(uint32_t frame, uint32_t ch) const {
        return m_data[frame * m_channels + ch];
    }

    // ── Channel extraction ────────────────────────────────────────────────────
    [[nodiscard]] std::vector<float> extractMono(uint32_t ch = 0) const {
        std::vector<float> out(m_frames);
        for (uint32_t f = 0; f < m_frames; ++f)
            out[f] = m_data[f * m_channels + ch];
        return out;
    }

    // ── Metrics ───────────────────────────────────────────────────────────────
    [[nodiscard]] float peakLinear() const noexcept {
        float peak = 0.0f;
        for (float s : m_data) peak = std::max(peak, std::abs(s));
        return peak;
    }

    [[nodiscard]] float peakDb() const noexcept {
        float p = peakLinear();
        return p > 1e-9f ? 20.0f * std::log10(p) : -120.0f;
    }

    [[nodiscard]] float rmsDb() const noexcept {
        if (m_data.empty()) return -120.0f;
        float sum = 0.0f;
        for (float s : m_data) sum += s * s;
        float rms = std::sqrt(sum / static_cast<float>(m_data.size()));
        return rms > 1e-9f ? 20.0f * std::log10(rms) : -120.0f;
    }

    [[nodiscard]] bool hasClipping(float threshold = 0.9999f) const noexcept {
        return peakLinear() >= threshold;
    }

    // ── Normalization ─────────────────────────────────────────────────────────
    void normalizeToDb(float targetDb = -1.0f) {
        float peak = peakLinear();
        if (peak < 1e-9f) return;
        float targetLinear = std::pow(10.0f, targetDb / 20.0f);
        float gain = targetLinear / peak;
        for (float& s : m_data) s *= gain;
    }

    // ── Apply gain ────────────────────────────────────────────────────────────
    void applyGain(float gain) noexcept {
        for (float& s : m_data) s *= gain;
    }

    // ── Mix another buffer in ─────────────────────────────────────────────────
    void mixIn(const AudioBuffer& other, float wetGain = 1.0f, float dryGain = 1.0f) {
        size_t n = std::min(m_data.size(), other.m_data.size());
        for (size_t i = 0; i < n; ++i)
            m_data[i] = m_data[i] * dryGain + other.m_data[i] * wetGain;
    }

    // ── Clear ─────────────────────────────────────────────────────────────────
    void clear() noexcept {
        std::fill(m_data.begin(), m_data.end(), 0.0f);
    }

    void copyFromInterleaved(const float* source, size_t numSamples) {
        if (!source) {
            throw std::invalid_argument("AudioBuffer::copyFromInterleaved source is null");
        }
        if (numSamples > m_data.size()) {
            throw std::out_of_range("AudioBuffer::copyFromInterleaved source is larger than buffer");
        }

        std::copy_n(source, numSamples, m_data.begin());
        if (numSamples < m_data.size()) {
            std::fill(m_data.begin() + static_cast<std::ptrdiff_t>(numSamples), m_data.end(), 0.0f);
        }
    }

    void copyToInterleaved(float* destination, size_t numSamples) const {
        if (!destination) {
            throw std::invalid_argument("AudioBuffer::copyToInterleaved destination is null");
        }
        if (numSamples > m_data.size()) {
            throw std::out_of_range("AudioBuffer::copyToInterleaved destination is larger than buffer");
        }

        std::copy_n(m_data.begin(), numSamples, destination);
    }

private:
    uint32_t           m_channels  = 0;
    uint32_t           m_frames    = 0;
    uint32_t           m_sampleRate= 0;
    std::vector<float> m_data;
};

} // namespace echoes
