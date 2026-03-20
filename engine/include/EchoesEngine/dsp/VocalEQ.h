#pragma once
#include "EchoesEngine/dsp/BiquadFilter.h"
#include "EchoesEngine/audio/AudioBuffer.h"
#include <array>
#include <memory>
#include <string>

namespace echoes::dsp {

/// 5-band parametric EQ — default preset tuned for Hard Rock vocals.
///
/// Chain:
///  [HighPass] → [Low Shelf] → [Presence Bell] → [Air Shelf] → [Low Cut Bell]
///
///  Default Hard Rock preset:
///   Band 1 — HP  @ 80Hz    : cuts mud / mic rumble
///   Band 2 — LS  @ 200Hz   : -2dB body control
///   Band 3 — PK  @ 3kHz    : +3dB presence / aggression
///   Band 4 — HS  @ 10kHz   : +2dB air / brightness
///   Band 5 — PK  @ 500Hz   : -1.5dB boxiness cut
class VocalEQ {
public:
    static constexpr int kNumBands = 5;

    struct BandParams {
        BiquadFilter::Params filter;
        std::string          label;
    };

    explicit VocalEQ(float sampleRate)
        : m_sampleRate(sampleRate)
    {
        // ── Hard Rock vocal preset ────────────────────────────────────────────
        m_bands[0].label          = "HP Cut";
        m_bands[0].filter.type    = BiquadFilter::Type::HighPass;
        m_bands[0].filter.freqHz  = 80.0f;
        m_bands[0].filter.Q       = 0.707f;

        m_bands[1].label          = "Low Body";
        m_bands[1].filter.type    = BiquadFilter::Type::LowShelf;
        m_bands[1].filter.freqHz  = 200.0f;
        m_bands[1].filter.gainDb  = -2.0f;
        m_bands[1].filter.Q       = 0.707f;

        m_bands[2].label          = "Presence";
        m_bands[2].filter.type    = BiquadFilter::Type::Peaking;
        m_bands[2].filter.freqHz  = 3000.0f;
        m_bands[2].filter.gainDb  = +3.0f;
        m_bands[2].filter.Q       = 1.2f;

        m_bands[3].label          = "Air";
        m_bands[3].filter.type    = BiquadFilter::Type::HighShelf;
        m_bands[3].filter.freqHz  = 10000.0f;
        m_bands[3].filter.gainDb  = +2.0f;
        m_bands[3].filter.Q       = 0.707f;

        m_bands[4].label          = "Box Cut";
        m_bands[4].filter.type    = BiquadFilter::Type::Peaking;
        m_bands[4].filter.freqHz  = 500.0f;
        m_bands[4].filter.gainDb  = -1.5f;
        m_bands[4].filter.Q       = 1.0f;

        // Instantiate filters
        for (int i = 0; i < kNumBands; ++i) {
            m_filters[i] = std::make_unique<BiquadFilter>(sampleRate);
            m_filters[i]->setParams(m_bands[i].filter);
        }
    }

    void setBand(int index, const BiquadFilter::Params& p) {
        if (index < 0 || index >= kNumBands) return;
        m_bands[index].filter = p;
        m_filters[index]->setParams(p);
    }

    [[nodiscard]] const BandParams& getBand(int index) const {
        return m_bands[index];
    }

    void process(AudioBuffer& buf) {
        for (int i = 0; i < kNumBands; ++i)
            m_filters[i]->process(buf);
    }

private:
    float m_sampleRate;
    std::array<BandParams, kNumBands>                m_bands;
    std::array<std::unique_ptr<BiquadFilter>, kNumBands> m_filters;
};

} // namespace echoes::dsp
