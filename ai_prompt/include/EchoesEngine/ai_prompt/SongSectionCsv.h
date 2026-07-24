#pragma once

#include "EchoesEngine/ai_prompt/ShotPlanner.h"

#include <string>
#include <vector>

namespace echoes::ai_prompt {

class SongSectionCsv {
public:
    static std::vector<SongSection> read_file(const std::string& path);
};

} // namespace echoes::ai_prompt
