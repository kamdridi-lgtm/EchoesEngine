#pragma once

#include "EchoesEngine/ai_prompt/ShotPlanner.h"

#include <string>

namespace echoes::ai_prompt {

class ShotPlanJson {
public:
    static std::string serialize(const ShotPlan& plan);
};

} // namespace echoes::ai_prompt
