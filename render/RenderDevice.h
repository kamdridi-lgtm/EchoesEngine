#pragma once

#include "FrameBuffer.h"

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>

namespace echoes::render {

enum class Backend {
    OpenGL,
    Vulkan,
};

struct DeviceCapabilities {
    bool supportsCompute         = false;
    bool unifiedMemory           = false;
    uint32_t maxTextureSize      = 4096;
    uint32_t maxRenderTargets    = 4;
};

class RenderDevice {
public:
    RenderDevice(Backend backend);
    ~RenderDevice();

    bool initialize();
    void shutdown();

    Backend backend() const noexcept;
    const DeviceCapabilities& capabilities() const noexcept;

    FrameBufferHandle createFrameBuffer(const FrameBufferDesc& desc);
    void destroyFrameBuffer(FrameBufferHandle handle);

    bool beginFrame();
    void endFrame();

    std::string describe() const;

private:
    Backend backend_;
    DeviceCapabilities caps_;
    std::atomic<uint64_t> nextHandle_{1};
};

} // namespace echoes::render
