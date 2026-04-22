#pragma once

#include <cstdint>

namespace echoes::render {

enum class FrameBufferFormat {
    RGBA8,
    RGBA16F,
    DEPTH24,
};

struct FrameBufferDesc {
    uint32_t width       = 1920;
    uint32_t height      = 1080;
    uint32_t samples     = 1;
    FrameBufferFormat colorFormat = FrameBufferFormat::RGBA16F;
    FrameBufferFormat depthFormat = FrameBufferFormat::DEPTH24;
    bool hdr            = true;
};

struct FrameBufferHandle {
    uint64_t id = 0;
};

} // namespace echoes::render
