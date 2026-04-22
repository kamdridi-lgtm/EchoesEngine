#pragma once

/// ─────────────────────────────────────────────────────────────────────────────
/// VST3 Plugin Interface Stub
///
/// This header defines the interface that EchoesEngine exposes to a VST3 host.
/// It intentionally does NOT include the Steinberg VST3 SDK headers yet —
/// those will be added in Phase 3 when you integrate the SDK.
///
/// What this establishes NOW:
///   - The ProcessContext (tempo, sample rate, transport info from the DAW)
///   - IVSTPlugin interface that VSTAdapter.cpp will implement
///   - Parameter IDs and ranges for the plugin
///
/// How to wire up the real VST3 SDK (Phase 3):
///   1. Add JUCE or the raw Steinberg SDK as a CMake dependency
///   2. Replace IVSTPlugin below with AudioEffect (Steinberg) or AudioProcessor (JUCE)
///   3. EchoesVSTAdapter.cpp becomes the thin glue between the DAW and EchoesEngine
/// ─────────────────────────────────────────────────────────────────────────────

#include <cstdint>
#include <string>

namespace echoes::vst {

// ── Transport / timing from the DAW ──────────────────────────────────────────
struct ProcessContext {
    double sampleRate     = 44100.0;
    double tempo          = 120.0;
    double projectTimeSec = 0.0;
    bool   isPlaying      = false;
    bool   isRecording    = false;
    int32_t timeSigNumerator   = 4;
    int32_t timeSigDenominator = 4;
};

// ── Plugin parameter IDs (will map to VST3 ParamID) ──────────────────────────
enum class ParamID : uint32_t {
    InputGainDb          = 0,
    GateThresholdDb      = 1,
    GateReleaseMs        = 2,
    CompThresholdDb      = 3,
    CompRatio            = 4,
    CompAttackMs         = 5,
    CompReleaseMs        = 6,
    CompMakeupGainDb     = 7,
    EQBand1GainDb        = 10,
    EQBand2GainDb        = 11,
    EQBand3GainDb        = 12,
    EQBand4GainDb        = 13,
    EQBand5GainDb        = 14,
    AITimbreDepth        = 20,
    AIPitchShift         = 21,
    AIBreathPreservation = 22,
    OutputGainDb         = 30,
    Bypass               = 99,
};

// ── Plugin info ───────────────────────────────────────────────────────────────
struct PluginInfo {
    static constexpr const char* kName     = "EchoesEngine™";
    static constexpr const char* kVendor   = "Kam Dridi";
    static constexpr const char* kVersion  = "0.1.0";
    static constexpr const char* kCategory = "Fx|Vocals";
    // VST3 UID — generate a real one at vst.steinberg.net for release
    static constexpr const char* kUID = "ECHOESENGINE00000000000000000001";
};

// ── Abstract plugin interface (becomes AudioEffect in Phase 3) ────────────────
class IVSTPlugin {
public:
    virtual ~IVSTPlugin() = default;

    /// Called by host on instantiation.
    virtual bool initialize(double sampleRate, int32_t maxBlockSize) = 0;

    /// Called when a parameter changes (from DAW automation or UI).
    virtual void setParameter(ParamID id, float normalizedValue) = 0;

    /// Get current normalized parameter value.
    virtual float getParameter(ParamID id) const = 0;

    /// Process a block of audio (in-place).
    virtual void processBlock(float** channelData,
                               int32_t numChannels,
                               int32_t numFrames,
                               const ProcessContext& ctx) = 0;

    /// Called when the plugin is about to be destroyed.
    virtual void shutdown() = 0;

    virtual const PluginInfo& info() const = 0;
};

} // namespace echoes::vst
