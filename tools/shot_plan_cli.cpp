#include "EchoesEngine/ai_prompt/ShotPlanJson.h"
#include "EchoesEngine/ai_prompt/ShotPlanner.h"
#include "EchoesEngine/ai_prompt/SongSectionCsv.h"

#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace echoes::ai_prompt;

int main(int argc, char** argv) {
    if (argc < 3 || argc > 4) {
        std::cerr << "Usage: ShotPlanCli <sections.csv> <output.json> [base-seed]\n";
        return 2;
    }

    try {
        ShotPlanner::Settings settings;
        if (argc == 4) {
            settings.baseSeed = static_cast<std::uint32_t>(std::stoul(argv[3]));
        }

        const auto sections = SongSectionCsv::read_file(argv[1]);
        const auto plan = ShotPlanner::build(sections, settings);
        const auto json = ShotPlanJson::serialize(plan);

        std::ofstream output(argv[2], std::ios::binary | std::ios::trunc);
        if (!output) {
            throw std::runtime_error("cannot open output file: " + std::string(argv[2]));
        }
        output << json;
        output.close();

        std::cout << "ShotPlanCli PASS sections=" << sections.size()
                  << " shots=" << plan.shots.size()
                  << " duration=" << plan.durationSeconds
                  << " output=" << argv[2] << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "ShotPlanCli ERROR: " << error.what() << '\n';
        return 1;
    }
}
