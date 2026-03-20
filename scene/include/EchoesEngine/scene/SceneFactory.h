#pragma once

#include "SceneDescriptor.h"

#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace echoes::scene {

class SceneFactory {
public:
    static std::vector<std::string> availableScenes();
    static std::optional<SceneDescriptor> createScene(std::string_view name);
};

} // namespace echoes::scene
