#pragma once

#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace echoes::render {

enum class ShaderStage {
    Vertex,
    Fragment,
    Compute,
};

struct ShaderDesc {
    std::string name;
    ShaderStage stage = ShaderStage::Vertex;
    std::string source;
    std::vector<std::string> defines;
};

class ShaderManager {
public:
    bool addShader(const ShaderDesc& desc);
    bool compile(const std::string& name);
    bool hasShader(const std::string& name) const;
    std::string status(const std::string& name) const;

private:
    struct ShaderRecord {
        ShaderDesc desc;
        bool compiled = false;
        std::string buildLog;
    };

    mutable std::mutex mutex_;
    std::map<std::string, ShaderRecord> records_;
};

} // namespace echoes::render
