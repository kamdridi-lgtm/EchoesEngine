#include "opencl/kernel_loader.h"

#include <cstdlib>
#include <fstream>
#include <optional>
#include <sstream>
#include <stdexcept>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace echoes::opencl {
namespace {

std::optional<std::filesystem::path> executableDirectory() {
#ifdef _WIN32
    wchar_t pathBuffer[MAX_PATH]{};
    const auto length = GetModuleFileNameW(nullptr, pathBuffer, MAX_PATH);
    if (length == 0 || length == MAX_PATH) {
        return std::nullopt;
    }
    return std::filesystem::path(pathBuffer).parent_path();
#else
    return std::nullopt;
#endif
}

void appendIfMissing(std::vector<std::filesystem::path>& paths, const std::filesystem::path& path) {
    if (path.empty()) {
        return;
    }

    const auto normalized = path.lexically_normal();
    for (const auto& existing : paths) {
        if (existing.lexically_normal() == normalized) {
            return;
        }
    }
    paths.push_back(normalized);
}

} // namespace

KernelLoader::KernelLoader(std::filesystem::path preferredDirectory)
    : preferredDirectory_(std::move(preferredDirectory)) {}

KernelSource KernelLoader::load(const std::string& kernelFileName) const {
    std::ostringstream searched;
    bool first = true;

    for (const auto& directory : candidateDirectories()) {
        const auto candidate = directory / kernelFileName;
        if (!first) {
            searched << ", ";
        }
        first = false;
        searched << candidate.string();

        std::ifstream input(candidate, std::ios::binary);
        if (!input) {
            continue;
        }

        std::ostringstream buffer;
        buffer << input.rdbuf();
        return {candidate, buffer.str()};
    }

    throw std::runtime_error("OpenCL kernel not found. Searched: " + searched.str());
}

std::vector<std::filesystem::path> KernelLoader::candidateDirectories() const {
    std::vector<std::filesystem::path> paths;

    if (const char* envPath = std::getenv("ECHOES_OPENCL_KERNELS_DIR")) {
        appendIfMissing(paths, envPath);
    }

    appendIfMissing(paths, preferredDirectory_);

#ifdef ECHOES_OPENCL_KERNELS_DIR
    appendIfMissing(paths, std::filesystem::path(ECHOES_OPENCL_KERNELS_DIR));
#endif

    const auto current = std::filesystem::current_path();
    appendIfMissing(paths, current / "engine" / "opencl" / "kernels");
    appendIfMissing(paths, current.parent_path() / "engine" / "opencl" / "kernels");
    appendIfMissing(paths, current.parent_path().parent_path() / "engine" / "opencl" / "kernels");

    if (const auto exeDir = executableDirectory()) {
        appendIfMissing(paths, *exeDir / "engine" / "opencl" / "kernels");
        appendIfMissing(paths, exeDir->parent_path() / "engine" / "opencl" / "kernels");
        appendIfMissing(paths, exeDir->parent_path().parent_path() / "engine" / "opencl" / "kernels");
    }

    return paths;
}

std::filesystem::path defaultKernelDirectory() {
#ifdef ECHOES_OPENCL_KERNELS_DIR
    return std::filesystem::path(ECHOES_OPENCL_KERNELS_DIR);
#else
    return std::filesystem::current_path() / "engine" / "opencl" / "kernels";
#endif
}

} // namespace echoes::opencl
