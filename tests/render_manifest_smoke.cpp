#include "EchoesEngine/ai_prompt/RenderManifest.h"
#include "EchoesEngine/ai_prompt/ShotPlanner.h"

#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
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
        {"intro", 0.0, 6.0, AudioFeatures{0.30f, 0.60f, 0.20f, 0.30f, 72.0f, false}},
        {"chorus", 6.0, 14.0, AudioFeatures{0.90f, 0.85f, 0.50f, 0.95f, 140.0f, true}}
    };

    ShotPlanner::Settings settings;
    settings.minimumShotSeconds = 2.0;
    settings.maximumShotSeconds = 6.0;
    settings.baseSeed = 4242;
    settings.continuity.subjectId = "kam-dridi-live";
    settings.continuity.styleId = "echoes-brasil-cinematic";
    settings.continuity.referenceAsset = "assets/reference/kam-live.png";
    settings.continuity.identityStrength = 0.92f;

    const auto plan = ShotPlanner::build(sections, settings);
    const auto manifest = RenderManifestBuilder::build(plan, "cinema-smoke-001", "rendered/clips");
    const auto json = RenderManifestBuilder::to_json(manifest);

    require(manifest.schema == "echoes.render-manifest.v1", "render manifest schema mismatch");
    require(manifest.jobId == "cinema-smoke-001", "render manifest job id mismatch");
    require(std::fabs(manifest.durationSeconds - 14.0) < 0.001, "render manifest duration mismatch");
    require(manifest.tasks.size() == plan.shots.size(), "each shot must produce one render task");
    require(!manifest.tasks.empty(), "render manifest must contain tasks");
    require(manifest.tasks.front().id == "cinema-smoke-001-task-1", "first task id must be deterministic");
    require(manifest.tasks.front().outputFile.find("rendered/clips/") == 0, "output directory must be preserved");
    require(manifest.tasks.front().outputFile.ends_with(".mp4"), "render task output must be mp4");
    require(!manifest.tasks.front().prompt.empty(), "render task prompt must not be empty");
    require(manifest.tasks.front().continuity.subjectId == "kam-dridi-live", "continuity subject must propagate");
    require(manifest.tasks.front().continuity.styleId == "echoes-brasil-cinematic", "continuity style must propagate");
    require(std::fabs(manifest.tasks.front().continuity.identityStrength - 0.92f) < 0.001f,
            "continuity strength must propagate");
    require(json.find("\"schema\": \"echoes.render-manifest.v1\"") != std::string::npos,
            "serialized manifest must contain schema");
    require(json.find("\"continuity\"") != std::string::npos,
            "serialized manifest must contain continuity metadata");
    require(json.find("kam-dridi-live") != std::string::npos,
            "serialized manifest must contain continuity subject");
    require(json.find("\"outputFile\"") != std::string::npos,
            "serialized manifest must contain output files");

    std::cout << "RenderManifestSmoke PASS tasks=" << manifest.tasks.size()
              << " duration=" << manifest.durationSeconds << '\n';
    return 0;
}
