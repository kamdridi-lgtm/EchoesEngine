#include "LightingEngine.h"

namespace echoes::render {

LightingEngine::LightingEngine() {
    registerPreset("neutral", LightingPreset{});
    registerPreset("cyberpunk", LightingPreset{{0.05f, 0.03f, 0.12f}, {0.4f, 0.0f, 0.9f}, {0.8f, 0.7f, 1.f}, 1.4f});
}

void LightingEngine::registerPreset(const std::string& name, LightingPreset preset) {
    presets_[name] = preset;
}

bool LightingEngine::applyPreset(const std::string& name) {
    auto it = presets_.find(name);
    if (it == presets_.end()) {
        return false;
    }
    activePreset_ = it->second;
    return true;
}

const LightingPreset& LightingEngine::current() const noexcept {
    return activePreset_;
}

} // namespace echoes::render
