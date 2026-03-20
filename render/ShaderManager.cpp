#include "ShaderManager.h"

#include <sstream>

namespace echoes::render {

bool ShaderManager::addShader(const ShaderDesc& desc) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (records_.count(desc.name)) {
        return false;
    }
    records_.emplace(desc.name, ShaderRecord{desc, false, "pending"});
    return true;
}

bool ShaderManager::compile(const std::string& name) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = records_.find(name);
    if (it == records_.end()) {
        return false;
    }

    auto& record = it->second;
    std::ostringstream status;
    status << "Compiled " << record.desc.name << " (" << ((record.desc.stage == ShaderStage::Vertex) ? "VS" : "PS") << ")";
    for (const auto& define : record.desc.defines) {
        status << " [" << define << "]";
    }
    record.buildLog = status.str();
    record.compiled = true;
    return true;
}

bool ShaderManager::hasShader(const std::string& name) const {
    std::lock_guard<std::mutex> lock(mutex_);
    return records_.count(name) != 0;
}

std::string ShaderManager::status(const std::string& name) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = records_.find(name);
    if (it == records_.end()) {
        return {};
    }
    return it->second.buildLog;
}

} // namespace echoes::render
