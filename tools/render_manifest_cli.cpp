#include "EchoesEngine/ai_prompt/RenderManifest.h"
#include "EchoesEngine/ai_prompt/ShotPlanner.h"
#include "EchoesEngine/ai_prompt/SongSectionCsv.h"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace echoes::ai_prompt;

namespace {

std::string read_env(const char* name) {
    const char* value = std::getenv(name);
    return value == nullptr ? std::string{} : std::string(value);
}

void apply_continuity_environment(ShotPlanner::Settings& settings) {
    settings.continuity.subjectId = read_env("ECHOES_CONTINUITY_SUBJECT_ID");
    settings.continuity.styleId = read_env("ECHOES_CONTINUITY_STYLE_ID");
    settings.continuity.referenceAsset = read_env("ECHOES_CONTINUITY_REFERENCE_ASSET");
    const std::string strength = read_env("ECHOES_CONTINUITY_STRENGTH");
    if (!strength.empty()) {
        settings.continuity.identityStrength = std::stof(strength);
    }
}

} // namespace

int main(int argc, char** argv) {
    if (argc < 4 || argc > 6) {
        std::cerr << "Usage: RenderManifestCli <sections.csv> <manifest.json> <job-id> [base-seed] [output-dir]\n";
        return 2;
    }

    try {
        ShotPlanner::Settings settings;
        if (argc >= 5) {
            settings.baseSeed = static_cast<std::uint32_t>(std::stoul(argv[4]));
        }
        apply_continuity_environment(settings);
        const std::string outputDirectory = argc == 6 ? argv[5] : "clips";

        const auto sections = SongSectionCsv::read_file(argv[1]);
        const auto plan = ShotPlanner::build(sections, settings);
        const auto manifest = RenderManifestBuilder::build(plan, argv[3], outputDirectory);
        const auto json = RenderManifestBuilder::to_json(manifest);

        std::ofstream output(argv[2], std::ios::binary | std::ios::trunc);
        if (!output) {
            throw std::runtime_error("cannot open output file: " + std::string(argv[2]));
        }
        output << json;
        output.close();

        std::cout << "RenderManifestCli PASS job=" << manifest.jobId
                  << " tasks=" << manifest.tasks.size()
                  << " duration=" << manifest.durationSeconds
                  << " continuitySubject=" << settings.continuity.subjectId
                  << " continuityStyle=" << settings.continuity.styleId
                  << " output=" << argv[2] << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "RenderManifestCli ERROR: " << error.what() << '\n';
        return 1;
    }
}
