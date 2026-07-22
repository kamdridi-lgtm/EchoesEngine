#include "EchoesEngine/ai_prompt/ShotPlanner.h"

#include <algorithm>
#include <cmath>
#include <functional>
#include <stdexcept>

namespace echoes::ai_prompt {

namespace {

CameraMove choose_camera(const AudioFeatures& features, std::size_t shotIndex) {
    if (features.energy >= 0.82f) {
        return (shotIndex % 2 == 0) ? CameraMove::Tracking : CameraMove::Handheld;
    }
    if (features.tempo >= 120.f) {
        return CameraMove::Orbit;
    }
    if (features.energy <= 0.35f) {
        return (shotIndex % 2 == 0) ? CameraMove::SlowPush : CameraMove::Crane;
    }
    return (shotIndex % 2 == 0) ? CameraMove::SlowPush : CameraMove::PullBack;
}

std::string choose_transition(std::size_t shotIndex, const AudioFeatures& features) {
    if (shotIndex == 0) {
        return "fade_in";
    }
    if (features.beat && features.energy >= 0.65f) {
        return "beat_cut";
    }
    return "cinematic_dissolve";
}

std::uint32_t deterministic_seed(const std::string& sectionId, std::size_t shotIndex, std::uint32_t baseSeed) {
    const auto hash = static_cast<std::uint64_t>(std::hash<std::string>{}(sectionId));
    return static_cast<std::uint32_t>((hash ^ (static_cast<std::uint64_t>(shotIndex + 1) * 0x9E3779B97F4A7C15ULL) ^ baseSeed) & 0xffffffffULL);
}

} // namespace

ShotPlan ShotPlanner::build(const std::vector<SongSection>& sections, Settings settings) {
    if (settings.minimumShotSeconds <= 0.0 || settings.maximumShotSeconds < settings.minimumShotSeconds) {
        throw std::invalid_argument("invalid shot duration settings");
    }
    if (settings.continuity.identityStrength < 0.0f || settings.continuity.identityStrength > 1.0f) {
        throw std::invalid_argument("continuity strength must be between 0 and 1");
    }

    ShotPlan plan;
    std::size_t globalShotIndex = 0;

    for (const auto& section : sections) {
        if (section.id.empty() || section.endSeconds <= section.startSeconds) {
            throw std::invalid_argument("song section must have an id and positive duration");
        }

        const double sectionDuration = section.endSeconds - section.startSeconds;
        const double targetShot = std::clamp(
            section.features.energy >= 0.75f ? settings.minimumShotSeconds : settings.maximumShotSeconds,
            settings.minimumShotSeconds,
            settings.maximumShotSeconds);
        const auto shotCount = std::max<std::size_t>(1, static_cast<std::size_t>(std::ceil(sectionDuration / targetShot)));
        const double exactDuration = sectionDuration / static_cast<double>(shotCount);
        const PromptResult promptResult = PromptDirector::generate_prompt_from_audio(section.features, settings.promptSettings);

        for (std::size_t localIndex = 0; localIndex < shotCount; ++localIndex, ++globalShotIndex) {
            ShotPlanEntry shot;
            shot.id = section.id + "-shot-" + std::to_string(localIndex + 1);
            shot.sectionId = section.id;
            shot.startSeconds = section.startSeconds + exactDuration * static_cast<double>(localIndex);
            shot.durationSeconds = exactDuration;
            shot.camera = choose_camera(section.features, globalShotIndex);
            shot.seed = deterministic_seed(section.id, localIndex, settings.baseSeed);
            shot.transition = choose_transition(globalShotIndex, section.features);
            shot.scene = promptResult.scene;
            shot.continuity = settings.continuity;
            shot.prompt = promptResult.prompt + " Camera: " + camera_move_name(shot.camera) + ". Maintain visual continuity.";
            plan.shots.push_back(std::move(shot));
        }

        plan.durationSeconds = std::max(plan.durationSeconds, section.endSeconds);
    }

    return plan;
}

const char* ShotPlanner::camera_move_name(CameraMove move) {
    switch (move) {
        case CameraMove::Locked: return "locked";
        case CameraMove::SlowPush: return "slow_push";
        case CameraMove::PullBack: return "pull_back";
        case CameraMove::Orbit: return "orbit";
        case CameraMove::Handheld: return "handheld";
        case CameraMove::Crane: return "crane";
        case CameraMove::Tracking: return "tracking";
    }
    return "locked";
}

} // namespace echoes::ai_prompt
