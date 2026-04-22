#pragma once

#include <filesystem>
#include <string>
#include <vector>

namespace echoes::opencl {

struct KernelSource {
    std::filesystem::path path;
    std::string source;
};

class KernelLoader {
public:
    explicit KernelLoader(std::filesystem::path preferredDirectory = {});

    KernelSource load(const std::string& kernelFileName) const;
    std::vector<std::filesystem::path> candidateDirectories() const;

private:
    std::filesystem::path preferredDirectory_;
};

std::filesystem::path defaultKernelDirectory();

} // namespace echoes::opencl
