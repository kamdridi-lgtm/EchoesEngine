#pragma once

#include <cstdint>
#include <memory>
#include <string>

namespace echoes::opencl {

struct DeviceInfo {
    std::string platformName;
    std::string deviceName;
    std::string vendorName;
    std::uint32_t computeUnits = 0;
    std::uint64_t globalMemoryBytes = 0;
    bool isGpu = false;
};

struct InitializationResult {
    bool enabledAtBuild = false;
    bool available = false;
    bool initialized = false;
    bool selfTestPassed = false;
    DeviceInfo device;
    std::string kernelDirectory;
    std::string message;
};

class OpenCLContext {
public:
    OpenCLContext();
    ~OpenCLContext();

    OpenCLContext(const OpenCLContext&) = delete;
    OpenCLContext& operator=(const OpenCLContext&) = delete;

    InitializationResult initialize();
    void shutdown();

    bool isInitialized() const noexcept;
    const InitializationResult& result() const noexcept;
    const std::string& lastError() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace echoes::opencl
