#include "EchoesEngine/ai_prompt/RenderManifest.h"
#include "EchoesEngine/ai_prompt/ShotPlanner.h"
#include "EchoesEngine/ai_prompt/SongSectionCsv.h"

#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace echoes::ai_prompt;

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
                  << " output=" << argv[2] << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "RenderManifestCli ERROR: " << error.what() << '\n';
        return 1;
    }
}
