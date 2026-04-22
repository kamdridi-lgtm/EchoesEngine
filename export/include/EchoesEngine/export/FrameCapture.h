#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace echoes::export_ {

struct Resolution {
    uint32_t width  = 3840;
    uint32_t height = 2160;
};

struct CapturedFrame {
    std::chrono::steady_clock::time_point timestamp;
    std::vector<uint8_t> pixels;
};

struct ExportSettings {
    Resolution resolution;
    uint32_t    frameRate = 60;
    std::filesystem::path workingDirectory = std::filesystem::path("renders");
    std::string outputFile = "render.mp4";
    uint32_t    keyframeInterval = 120;
};

class FrameCaptureSource {
public:
    virtual ~FrameCaptureSource() = default;

    virtual bool capture(uint32_t width, uint32_t height, std::vector<uint8_t>& outPixels) = 0;
    virtual void reset() = 0;
};

class SyntheticFrameSource : public FrameCaptureSource {
public:
    SyntheticFrameSource();

    bool capture(uint32_t width, uint32_t height, std::vector<uint8_t>& outPixels) override;
    void reset() override;

private:
    uint64_t frameIndex_ = 0;
};

struct RenderResult {
    bool success = false;
    std::string message;
    std::vector<CapturedFrame> frames;
    std::filesystem::path frameFolder;
};

} // namespace echoes::export_
