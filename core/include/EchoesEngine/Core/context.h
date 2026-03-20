#pragma once

#include <cstddef>

namespace echoes::core {

class Engine;
class JobSystem;

struct ModuleContext {
    Engine* engine = nullptr;
    JobSystem* jobSystem = nullptr;
    double targetFps = 60.0;
};

} // namespace echoes::core
