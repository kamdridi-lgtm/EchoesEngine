#pragma once

#include <array>
#include <map>
#include <string>

namespace echoes::render {

struct LightingPreset {
    std::array<float, 3> ambient{0.1f, 0.1f, 0.1f};
    std::array<float, 3> diffuse{0.6f, 0.6f, 0.6f};
    std::array<float, 3> specular{0.3f, 0.3f, 0.3f};
    float intensity = 1.0f;
};

class LightingEngine {
public:
    LightingEngine();

    void registerPreset(const std::string& name, LightingPreset preset);
    bool applyPreset(const std::string& name);
    const LightingPreset& current() const noexcept;

private:
    std::map<std::string, LightingPreset> presets_;
    LightingPreset activePreset_;
};

} // namespace echoes::render
