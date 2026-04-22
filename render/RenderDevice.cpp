#include "RenderDevice.h"

#include <algorithm>
#include <sstream>

namespace echoes::render {

RenderDevice::RenderDevice(Backend backend)
    : backend_(backend) {
}

RenderDevice::~RenderDevice() {
    shutdown();
}

bool RenderDevice::initialize() {
    caps_.supportsCompute      = backend_ == Backend::Vulkan;
    caps_.unifiedMemory        = false;
    caps_.maxTextureSize       = (backend_ == Backend::Vulkan) ? 16384 : 8192;
    caps_.maxRenderTargets     = 4;
    return true;
}

void RenderDevice::shutdown() {
    backend_ = Backend::OpenGL;
    nextHandle_ = 1;
}

Backend RenderDevice::backend() const noexcept {
    return backend_;
}

const DeviceCapabilities& RenderDevice::capabilities() const noexcept {
    return caps_;
}

FrameBufferHandle RenderDevice::createFrameBuffer(const FrameBufferDesc& desc) {
    FrameBufferHandle handle{};
    handle.id = nextHandle_.fetch_add(1, std::memory_order_relaxed);
    (void)desc;
    return handle;
}

void RenderDevice::destroyFrameBuffer(FrameBufferHandle handle) {
    (void)handle;
}

bool RenderDevice::beginFrame() {
    return true;
}

void RenderDevice::endFrame() {
}

std::string RenderDevice::describe() const {
    std::ostringstream oss;
    oss << "RenderDevice[" << ((backend_ == Backend::Vulkan) ? "Vulkan" : "OpenGL") << "]";
    return oss.str();
}

} // namespace echoes::render
