#pragma once

#include "EchoesEngine/ai_prompt/PromptDirector.h"

#include <cstdint>
#include <string>
#include <vector>

namespace echoes::ai_prompt {

enum class CameraMove {
    Locked,
    SlowPush,
    PullBack,
    Orbit,
    Handheld,
    Crane,
    Tracking
};

struct SongSection {
    std::string id;
    double startSeconds = 0.0;
    double endSeconds = 0.0;
    AudioFeatures features;
};

struct ContinuityProfile {
    std::string subjectId;
    std::string styleId;
    std::string referenceAsset;
    float identityStrength = 0.85f;
};

struct ShotPlanEntry {
    std::string id;
    std::string sectionId;
    double startSeconds = 0.0;
    double durationSeconds = 0.0;
    CameraMove camera = CameraMove::Locked;
    std::uint32_t seed = 0;
    std::string prompt;
    std::string transition;
    SceneSuggestion scene;
    ContinuityProfile continuity;
};

struct ShotPlan {
    double durationSeconds = 0.0;
    std::vector<ShotPlanEntry> shots;
};

class ShotPlanner {
public:
    struct Settings {
        double minimumShotSeconds = 2.0;
        double maximumShotSeconds = 6.0;
        std::uint32_t baseSeed = 1337;
        PromptDirector::Settings promptSettings{};
        ContinuityProfile continuity{};
    };

    static ShotPlan build(const std::vector<SongSection>& sections, Settings settings);
    static const char* camera_move_name(CameraMove move);
};

} // namespace echoes::ai_prompt
