#pragma once
#include "EchoesEngine/audio/AudioBuffer.h"
#include <array>
#include <cmath>
#include <numbers>

namespace echoes::dsp {

/// Direct Form II Transposed biquad filter.
/// Used as the building block for all EQ bands.
///
/// Supports: LowShelf, HighShelf, Peaking (Bell), LowPass, HighPass, Notch
class BiquadFilter {
public:
    enum class Type {
        LowPass, HighPass, BandPass,
        LowShelf, HighShelf,
        Peaking, Notch, AllPass
    };

    struct Params {
        Type  type      = Type::Peaking;
        float freqHz    = 1000.0f;
        float gainDb    = 0.0f;    // For shelf / peaking only
        float Q         = 0.707f;  // Bandwidth
        bool  enabled   = true;
    };

    explicit BiquadFilter(float sampleRate)
        : m_sampleRate(sampleRate)
    {
        computeCoefs();
    }

    void setParams(const Params& p) {
        m_params = p;
        computeCoefs();
    }

    void process(AudioBuffer& buf) {
        if (!m_params.enabled) return;

        for (uint32_t f = 0; f < buf.frames(); ++f) {
            for (uint32_t ch = 0; ch < buf.channels(); ++ch) {
                float& s = buf.at(f, ch);
                // Direct Form II Transposed
                float y = m_b0 * s + m_z1[ch];
                m_z1[ch] = m_b1 * s - m_a1 * y + m_z2[ch];
                m_z2[ch] = m_b2 * s - m_a2 * y;
                s = y;
            }
        }
    }

    void reset() noexcept {
        m_z1.fill(0.0f);
        m_z2.fill(0.0f);
    }

private:
    void computeCoefs() {
        float Fs = m_sampleRate;
        float f0 = m_params.freqHz;
        float Q  = m_params.Q;
        float G  = m_params.gainDb;
        float A  = std::pow(10.0f, G / 40.0f);
        float w0 = 2.0f * std::numbers::pi_v<float> * f0 / Fs;
        float cw = std::cos(w0);
        float sw = std::sin(w0);
        float alpha = sw / (2.0f * Q);

        float a0, a1, a2, b0, b1, b2;

        switch (m_params.type) {
        case Type::LowPass:
            b0=(1-cw)/2; b1=1-cw; b2=(1-cw)/2;
            a0=1+alpha;  a1=-2*cw; a2=1-alpha;
            break;
        case Type::HighPass:
            b0=(1+cw)/2; b1=-(1+cw); b2=(1+cw)/2;
            a0=1+alpha;  a1=-2*cw;   a2=1-alpha;
            break;
        case Type::LowShelf:
            b0=A*((A+1)-(A-1)*cw+2*std::sqrt(A)*alpha);
            b1=2*A*((A-1)-(A+1)*cw);
            b2=A*((A+1)-(A-1)*cw-2*std::sqrt(A)*alpha);
            a0=(A+1)+(A-1)*cw+2*std::sqrt(A)*alpha;
            a1=-2*((A-1)+(A+1)*cw);
            a2=(A+1)+(A-1)*cw-2*std::sqrt(A)*alpha;
            break;
        case Type::HighShelf:
            b0=A*((A+1)+(A-1)*cw+2*std::sqrt(A)*alpha);
            b1=-2*A*((A-1)+(A+1)*cw);
            b2=A*((A+1)+(A-1)*cw-2*std::sqrt(A)*alpha);
            a0=(A+1)-(A-1)*cw+2*std::sqrt(A)*alpha;
            a1=2*((A-1)-(A+1)*cw);
            a2=(A+1)-(A-1)*cw-2*std::sqrt(A)*alpha;
            break;
        case Type::Peaking:
        default:
            b0=1+alpha*A; b1=-2*cw; b2=1-alpha*A;
            a0=1+alpha/A; a1=-2*cw; a2=1-alpha/A;
            break;
        case Type::Notch:
            b0=1; b1=-2*cw; b2=1;
            a0=1+alpha; a1=-2*cw; a2=1-alpha;
            break;
        }

        float inv = 1.0f / a0;
        m_b0 = b0*inv; m_b1 = b1*inv; m_b2 = b2*inv;
        m_a1 = a1*inv; m_a2 = a2*inv;
    }

    float m_sampleRate;
    Params m_params;

    float m_b0=1, m_b1=0, m_b2=0;
    float m_a1=0, m_a2=0;

    // State per channel (support up to 8 channels)
    std::array<float,8> m_z1{}, m_z2{};
};

} // namespace echoes::dsp
