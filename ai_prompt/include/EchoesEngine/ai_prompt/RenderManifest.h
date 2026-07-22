#pragma once

#include "EchoesEngine/ai_prompt/ShotPlanner.h"

#include <cstdint>
#include <string>
#include <vector>

namespace echoes::ai_prompt {

struct RenderTask {
    std::string id;
    std::string shotId;
    double startSeconds = 0.0;
    double durationSeconds = 0.0;
    std::uint32_t seed = 0;
    std::string prompt;
    std::string camera;
    std::string transition;
    std::string outputFile;
    ContinuityProfile continuity;
};

struct RenderManifest {
    std::string schema = "echoes.render-manifest.v1";
    std::string jobId;
    double durationSeconds = 0.0;
    std::vector<RenderTask> tasks;
};

class RenderManifestBuilder {
public:
    static RenderManifest build(const ShotPlan& plan, const std::string& jobId, const std::string& outputDirectory = "clips");
    static std::string to_json(const RenderManifest& manifest);
};

} // namespace echoes::ai_prompt
