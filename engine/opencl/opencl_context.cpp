#include "opencl/opencl_context.h"

#include "opencl/kernel_loader.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#if ECHOES_USE_OPENCL
#include <CL/cl.h>
#endif

namespace echoes::opencl {

#if ECHOES_USE_OPENCL
namespace {

std::string trimNullTerminated(const std::vector<char>& bytes) {
    auto end = std::find(bytes.begin(), bytes.end(), '\0');
    return std::string(bytes.begin(), end);
}

std::string platformInfoString(cl_platform_id platform, cl_platform_info info) {
    size_t size = 0;
    if (clGetPlatformInfo(platform, info, 0, nullptr, &size) != CL_SUCCESS || size == 0) {
        return {};
    }

    std::vector<char> buffer(size);
    if (clGetPlatformInfo(platform, info, buffer.size(), buffer.data(), nullptr) != CL_SUCCESS) {
        return {};
    }
    return trimNullTerminated(buffer);
}

std::string deviceInfoString(cl_device_id device, cl_device_info info) {
    size_t size = 0;
    if (clGetDeviceInfo(device, info, 0, nullptr, &size) != CL_SUCCESS || size == 0) {
        return {};
    }

    std::vector<char> buffer(size);
    if (clGetDeviceInfo(device, info, buffer.size(), buffer.data(), nullptr) != CL_SUCCESS) {
        return {};
    }
    return trimNullTerminated(buffer);
}

template <typename T>
T deviceInfoValue(cl_device_id device, cl_device_info info, T fallback = {}) {
    T value = fallback;
    if (clGetDeviceInfo(device, info, sizeof(T), &value, nullptr) != CL_SUCCESS) {
        return fallback;
    }
    return value;
}

std::string buildLog(cl_program program, cl_device_id device) {
    size_t size = 0;
    if (clGetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, 0, nullptr, &size) != CL_SUCCESS || size == 0) {
        return {};
    }

    std::vector<char> buffer(size);
    if (clGetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, buffer.size(), buffer.data(), nullptr) != CL_SUCCESS) {
        return {};
    }
    return trimNullTerminated(buffer);
}

std::string errorString(cl_int error) {
    switch (error) {
        case CL_SUCCESS: return "CL_SUCCESS";
        case CL_DEVICE_NOT_FOUND: return "CL_DEVICE_NOT_FOUND";
        case CL_DEVICE_NOT_AVAILABLE: return "CL_DEVICE_NOT_AVAILABLE";
        case CL_COMPILER_NOT_AVAILABLE: return "CL_COMPILER_NOT_AVAILABLE";
        case CL_MEM_OBJECT_ALLOCATION_FAILURE: return "CL_MEM_OBJECT_ALLOCATION_FAILURE";
        case CL_OUT_OF_RESOURCES: return "CL_OUT_OF_RESOURCES";
        case CL_OUT_OF_HOST_MEMORY: return "CL_OUT_OF_HOST_MEMORY";
        case CL_BUILD_PROGRAM_FAILURE: return "CL_BUILD_PROGRAM_FAILURE";
        case CL_INVALID_VALUE: return "CL_INVALID_VALUE";
        case CL_INVALID_DEVICE: return "CL_INVALID_DEVICE";
        case CL_INVALID_BINARY: return "CL_INVALID_BINARY";
        case CL_INVALID_BUILD_OPTIONS: return "CL_INVALID_BUILD_OPTIONS";
        case CL_INVALID_OPERATION: return "CL_INVALID_OPERATION";
        case -1001: return "CL_PLATFORM_NOT_FOUND_KHR";
        default: return "OpenCL error " + std::to_string(error);
    }
}

double deviceScore(cl_device_type type, cl_uint computeUnits, cl_ulong memoryBytes) {
    double score = static_cast<double>(computeUnits) * 1000.0 + static_cast<double>(memoryBytes) / (1024.0 * 1024.0 * 64.0);
    if ((type & CL_DEVICE_TYPE_GPU) != 0) {
        score += 1'000'000.0;
    } else if ((type & CL_DEVICE_TYPE_ACCELERATOR) != 0) {
        score += 500'000.0;
    } else if ((type & CL_DEVICE_TYPE_CPU) != 0) {
        score += 100'000.0;
    }
    return score;
}

} // namespace
#endif

struct OpenCLContext::Impl {
    InitializationResult result;
    std::string lastError;

#if ECHOES_USE_OPENCL
    cl_platform_id platform = nullptr;
    cl_device_id device = nullptr;
    cl_context context = nullptr;
    cl_command_queue queue = nullptr;

    bool runVectorAddSelfTest(cl_program program) {
        constexpr size_t elementCount = 64;
        cl_int error = CL_SUCCESS;

        cl_kernel kernel = clCreateKernel(program, "vector_add", &error);
        if (error != CL_SUCCESS || kernel == nullptr) {
            lastError = "Failed to create vector_add kernel: " + errorString(error);
            return false;
        }

        std::vector<float> left(elementCount);
        std::vector<float> right(elementCount);
        std::vector<float> output(elementCount, -1.0f);
        for (size_t i = 0; i < elementCount; ++i) {
            left[i] = static_cast<float>(i);
            right[i] = static_cast<float>(elementCount - i);
        }

        cl_mem leftBuffer = clCreateBuffer(context, CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR,
                                           sizeof(float) * elementCount, left.data(), &error);
        if (error != CL_SUCCESS || leftBuffer == nullptr) {
            clReleaseKernel(kernel);
            lastError = "Failed to allocate left OpenCL buffer: " + errorString(error);
            return false;
        }

        cl_mem rightBuffer = clCreateBuffer(context, CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR,
                                            sizeof(float) * elementCount, right.data(), &error);
        if (error != CL_SUCCESS || rightBuffer == nullptr) {
            clReleaseMemObject(leftBuffer);
            clReleaseKernel(kernel);
            lastError = "Failed to allocate right OpenCL buffer: " + errorString(error);
            return false;
        }

        cl_mem outputBuffer = clCreateBuffer(context, CL_MEM_WRITE_ONLY, sizeof(float) * elementCount, nullptr, &error);
        if (error != CL_SUCCESS || outputBuffer == nullptr) {
            clReleaseMemObject(rightBuffer);
            clReleaseMemObject(leftBuffer);
            clReleaseKernel(kernel);
            lastError = "Failed to allocate output OpenCL buffer: " + errorString(error);
            return false;
        }

        cl_uint count = static_cast<cl_uint>(elementCount);
        error = clSetKernelArg(kernel, 0, sizeof(cl_mem), &leftBuffer);
        error |= clSetKernelArg(kernel, 1, sizeof(cl_mem), &rightBuffer);
        error |= clSetKernelArg(kernel, 2, sizeof(cl_mem), &outputBuffer);
        error |= clSetKernelArg(kernel, 3, sizeof(cl_uint), &count);
        if (error != CL_SUCCESS) {
            clReleaseMemObject(outputBuffer);
            clReleaseMemObject(rightBuffer);
            clReleaseMemObject(leftBuffer);
            clReleaseKernel(kernel);
            lastError = "Failed to set OpenCL kernel arguments: " + errorString(error);
            return false;
        }

        const size_t globalSize = elementCount;
        error = clEnqueueNDRangeKernel(queue, kernel, 1, nullptr, &globalSize, nullptr, 0, nullptr, nullptr);
        if (error == CL_SUCCESS) {
            error = clFinish(queue);
        }
        if (error == CL_SUCCESS) {
            error = clEnqueueReadBuffer(queue, outputBuffer, CL_TRUE, 0, sizeof(float) * elementCount,
                                        output.data(), 0, nullptr, nullptr);
        }

        clReleaseMemObject(outputBuffer);
        clReleaseMemObject(rightBuffer);
        clReleaseMemObject(leftBuffer);
        clReleaseKernel(kernel);

        if (error != CL_SUCCESS) {
            lastError = "OpenCL vector-add dispatch failed: " + errorString(error);
            return false;
        }

        for (size_t i = 0; i < elementCount; ++i) {
            const float expected = left[i] + right[i];
            if (std::fabs(output[i] - expected) > 0.001f) {
                lastError = "OpenCL vector-add validation failed at index " + std::to_string(i);
                return false;
            }
        }

        result.selfTestPassed = true;
        return true;
    }
#endif
};

OpenCLContext::OpenCLContext()
    : impl_(std::make_unique<Impl>()) {}

OpenCLContext::~OpenCLContext() {
    shutdown();
}

InitializationResult OpenCLContext::initialize() {
    shutdown();
    impl_->result = {};
    impl_->lastError.clear();
    impl_->result.enabledAtBuild = ECHOES_USE_OPENCL != 0;
    impl_->result.kernelDirectory = defaultKernelDirectory().string();

#if !ECHOES_USE_OPENCL
    impl_->result.message = "EchoesEngine OpenCL support disabled at build time. Reconfigure with -DUSE_OPENCL=ON.";
    return impl_->result;
#else
    cl_uint platformCount = 0;
    cl_int error = clGetPlatformIDs(0, nullptr, &platformCount);
    if (error != CL_SUCCESS || platformCount == 0) {
        impl_->lastError = "No OpenCL platforms detected: " + errorString(error);
        impl_->result.message = impl_->lastError;
        return impl_->result;
    }

    std::vector<cl_platform_id> platforms(platformCount);
    error = clGetPlatformIDs(platformCount, platforms.data(), nullptr);
    if (error != CL_SUCCESS) {
        impl_->lastError = "Failed to enumerate OpenCL platforms: " + errorString(error);
        impl_->result.message = impl_->lastError;
        return impl_->result;
    }

    struct Candidate {
        cl_platform_id platform = nullptr;
        cl_device_id device = nullptr;
        DeviceInfo info;
        double score = -std::numeric_limits<double>::infinity();
    };

    Candidate best;
    for (const auto platform : platforms) {
        cl_uint deviceCount = 0;
        error = clGetDeviceIDs(platform, CL_DEVICE_TYPE_ALL, 0, nullptr, &deviceCount);
        if (error != CL_SUCCESS || deviceCount == 0) {
            continue;
        }

        std::vector<cl_device_id> devices(deviceCount);
        error = clGetDeviceIDs(platform, CL_DEVICE_TYPE_ALL, deviceCount, devices.data(), nullptr);
        if (error != CL_SUCCESS) {
            continue;
        }

        for (const auto device : devices) {
            const auto type = deviceInfoValue<cl_device_type>(device, CL_DEVICE_TYPE, 0);
            const auto computeUnits = deviceInfoValue<cl_uint>(device, CL_DEVICE_MAX_COMPUTE_UNITS, 0);
            const auto memoryBytes = deviceInfoValue<cl_ulong>(device, CL_DEVICE_GLOBAL_MEM_SIZE, 0);
            const auto score = deviceScore(type, computeUnits, memoryBytes);
            if (score <= best.score) {
                continue;
            }

            best.platform = platform;
            best.device = device;
            best.score = score;
            best.info.platformName = platformInfoString(platform, CL_PLATFORM_NAME);
            best.info.deviceName = deviceInfoString(device, CL_DEVICE_NAME);
            best.info.vendorName = deviceInfoString(device, CL_DEVICE_VENDOR);
            best.info.computeUnits = computeUnits;
            best.info.globalMemoryBytes = memoryBytes;
            best.info.isGpu = (type & CL_DEVICE_TYPE_GPU) != 0;
        }
    }

    if (best.device == nullptr) {
        impl_->lastError = "OpenCL runtime is present, but no usable devices were detected.";
        impl_->result.message = impl_->lastError;
        return impl_->result;
    }

    const cl_context_properties contextProperties[] = {
        CL_CONTEXT_PLATFORM,
        reinterpret_cast<cl_context_properties>(best.platform),
        0
    };

    impl_->platform = best.platform;
    impl_->device = best.device;
    impl_->context = clCreateContext(contextProperties, 1, &impl_->device, nullptr, nullptr, &error);
    if (error != CL_SUCCESS || impl_->context == nullptr) {
        impl_->lastError = "Failed to create OpenCL context: " + errorString(error);
        impl_->result.message = impl_->lastError;
        shutdown();
        return impl_->result;
    }

    impl_->queue = clCreateCommandQueue(impl_->context, impl_->device, 0, &error);
    if (error != CL_SUCCESS || impl_->queue == nullptr) {
        impl_->lastError = "Failed to create OpenCL command queue: " + errorString(error);
        impl_->result.message = impl_->lastError;
        shutdown();
        return impl_->result;
    }

    KernelLoader loader(defaultKernelDirectory());
    KernelSource kernelSource;
    try {
        kernelSource = loader.load("vector_add.cl");
        impl_->result.kernelDirectory = kernelSource.path.parent_path().string();
    } catch (const std::exception& ex) {
        impl_->lastError = ex.what();
        impl_->result.message = impl_->lastError;
        shutdown();
        return impl_->result;
    }

    const char* sourcePointer = kernelSource.source.c_str();
    const size_t sourceLength = kernelSource.source.size();
    cl_program program = clCreateProgramWithSource(impl_->context, 1, &sourcePointer, &sourceLength, &error);
    if (error != CL_SUCCESS || program == nullptr) {
        impl_->lastError = "Failed to create OpenCL program: " + errorString(error);
        impl_->result.message = impl_->lastError;
        shutdown();
        return impl_->result;
    }

    error = clBuildProgram(program, 1, &impl_->device, nullptr, nullptr, nullptr);
    if (error != CL_SUCCESS) {
        const auto log = buildLog(program, impl_->device);
        clReleaseProgram(program);
        impl_->lastError = "Failed to compile OpenCL kernels: " + errorString(error);
        if (!log.empty()) {
            impl_->lastError += " | build log: " + log;
        }
        impl_->result.message = impl_->lastError;
        shutdown();
        return impl_->result;
    }

    if (!impl_->runVectorAddSelfTest(program)) {
        clReleaseProgram(program);
        impl_->result.message = impl_->lastError;
        shutdown();
        return impl_->result;
    }

    clReleaseProgram(program);

    impl_->result.available = true;
    impl_->result.initialized = true;
    impl_->result.device = best.info;
    impl_->result.message =
        "EchoesEngine OpenCL device initialized: " + best.info.deviceName +
        " (" + best.info.vendorName + "), compute units=" + std::to_string(best.info.computeUnits);
    return impl_->result;
#endif
}

void OpenCLContext::shutdown() {
#if ECHOES_USE_OPENCL
    if (impl_->queue != nullptr) {
        clReleaseCommandQueue(impl_->queue);
        impl_->queue = nullptr;
    }
    if (impl_->context != nullptr) {
        clReleaseContext(impl_->context);
        impl_->context = nullptr;
    }
    impl_->device = nullptr;
    impl_->platform = nullptr;
#endif
}

bool OpenCLContext::isInitialized() const noexcept {
    return impl_->result.initialized;
}

const InitializationResult& OpenCLContext::result() const noexcept {
    return impl_->result;
}

const std::string& OpenCLContext::lastError() const noexcept {
    return impl_->lastError;
}

} // namespace echoes::opencl
