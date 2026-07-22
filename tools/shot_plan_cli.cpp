#include "EchoesEngine/ai_prompt/ShotPlanJson.h"
#include "EchoesEngine/ai_prompt/ShotPlanner.h"

#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace echoes::ai_prompt;

namespace {

bool parse_bool(const std::string& value) {
    return value == "1" || value == "true" || value == "TRUE" || value == "yes";
}

std::vector<SongSection> read_sections(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open input file: " + path);
    }

    std::vector<SongSection> sections;
    std::string line;
    std::size_t lineNumber = 0;
    while (std::getline(input, line)) {
        ++lineNumber;
        if (line.empty() || line[0] == '#') {
            continue;
        }

        std::stringstream stream(line);
        std::vector<std::string> fields;
        std::string field;
        while (std::getline(stream, field, ',')) {
            fields.push_back(field);
        }
        if (fields.size() != 9) {
            throw std::runtime_error("invalid section line " + std::to_string(lineNumber) + ": expected 9 comma-separated fields");
        }

        SongSection section;
        section.id = fields[0];
        section.startSeconds = std::stod(fields[1]);
        section.endSeconds = std::stod(fields[2]);
        section.features.bass = std::stof(fields[3]);
        section.features.mid = std::stof(fields[4]);
        section.features.treble = std::stof(fields[5]);
        section.features.energy = std::stof(fields[6]);
        section.features.tempo = std::stof(fields[7]);
        section.features.beat = parse_bool(fields[8]);
        sections.push_back(std::move(section));
    }

    if (sections.empty()) {
        throw std::runtime_error("input contains no song sections");
    }
    return sections;
}

} // namespace

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

        const auto sections = read_sections(argv[1]);
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
