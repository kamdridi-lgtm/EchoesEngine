#include "EchoesEngine/ai_prompt/ShotPlanner.h"

#include <cmath>
#include <iostream>
#include <stdexcept>
#include <vector>

using namespace echoes::ai_prompt;

namespace {

void require(bool condition, const char* message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

} // namespace

int main() {
    const std::vector<SongSection> sections{
        {"intro", 0.0, 8.0, AudioFeatures{0.3f, 0.6f, 0.2f, 0.3f, 72.f, false}},
        {"chorus", 8.0, 20.0, AudioFeatures{0.9f, 0.85f, 0.5f, 0.95f, 140.f, true}},
        {"outro", 20.0, 28.0, AudioFeatures{0.25f, 0.35f, 0.9f, 0.45f, 80.f, false}}
    };

    ShotPlanner::Settings settings;
    settings.minimumShotSeconds = 2.0;
    settings.maximumShotSeconds = 6.0;
    settings.baseSeed = 4242;

    const ShotPlan first = ShotPlanner::build(sections, settings);
    const ShotPlan second = ShotPlanner::build(sections, settings);

    require(!first.shots.empty(), "shot plan must not be empty");
    require(std::fabs(first.durationSeconds - 28.0) < 0.001, "plan duration must match song sections");
    require(first.shots.size() == second.shots.size(), "same input must produce same shot count");

    bool foundBeatCut = false;
    bool foundTracking = false;
    for (std::size_t index = 0; index < first.shots.size(); ++index) {
        const auto& shot = first.shots[index];
        const auto& repeated = second.shots[index];
        require(shot.durationSeconds > 0.0, "all shots need positive duration");
        require(!shot.prompt.empty(), "all shots need a render prompt");
        require(shot.seed == repeated.seed, "seeds must be deterministic");
        require(shot.prompt == repeated.prompt, "prompts must be deterministic");
        foundBeatCut = foundBeatCut || shot.transition == "beat_cut";
        foundTracking = foundTracking || shot.camera == CameraMove::Tracking;
    }

    require(foundBeatCut, "high-energy beat section must create a beat cut");
    require(foundTracking, "high-energy section must create a tracking camera shot");

    std::cout << "ShotPlannerSmoke PASS shots=" << first.shots.size() << " duration=" << first.durationSeconds << '\n';
    return 0;
}
