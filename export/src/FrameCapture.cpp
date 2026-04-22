#include "EchoesEngine/export/FrameCapture.h"

#include <cmath>
#include <numeric>

namespace echoes::export_ {

SyntheticFrameSource::SyntheticFrameSource() = default;

bool SyntheticFrameSource::capture(uint32_t width, uint32_t height, std::vector<uint8_t>& outPixels) {
    if (width == 0 || height == 0) {
        return false;
    }

    const size_t pixelCount = static_cast<size_t>(width) * height * 4;
    outPixels.resize(pixelCount);

    const float phase = static_cast<float>(frameIndex_) * 0.08f;
    for (uint32_t y = 0; y < height; ++y) {
        for (uint32_t x = 0; x < width; ++x) {
            const size_t idx = (static_cast<size_t>(y) * width + x) * 4;
            const float u = static_cast<float>(x) / width;
            const float v = static_cast<float>(y) / height;
            uint8_t r = static_cast<uint8_t>((0.5f + 0.5f * std::sin(6.28f * (u + phase))) * 255.0f);
            uint8_t g = static_cast<uint8_t>((0.5f + 0.5f * std::cos(6.28f * (v + phase))) * 255.0f);
            uint8_t b = static_cast<uint8_t>((0.5f + 0.5f * std::sin(6.28f * (u + v + phase))) * 255.0f);
            outPixels[idx + 0] = r;
            outPixels[idx + 1] = g;
            outPixels[idx + 2] = b;
            outPixels[idx + 3] = 255;
        }
    }

    ++frameIndex_;
    return true;
}

void SyntheticFrameSource::reset() {
    frameIndex_ = 0;
}

} // namespace echoes::export_
