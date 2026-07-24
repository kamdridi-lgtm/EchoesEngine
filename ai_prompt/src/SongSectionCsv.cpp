#include "EchoesEngine/ai_prompt/SongSectionCsv.h"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace echoes::ai_prompt {
namespace {

std::string trim(std::string value) {
    const auto first = std::find_if_not(value.begin(), value.end(), [](unsigned char ch) { return std::isspace(ch) != 0; });
    const auto last = std::find_if_not(value.rbegin(), value.rend(), [](unsigned char ch) { return std::isspace(ch) != 0; }).base();
    if (first >= last) {
        return {};
    }
    return std::string(first, last);
}

bool parse_bool(std::string value) {
    value = trim(std::move(value));
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    if (value == "1" || value == "true" || value == "yes") {
        return true;
    }
    if (value == "0" || value == "false" || value == "no") {
        return false;
    }
    throw std::runtime_error("invalid boolean value: " + value);
}

void require_unit_interval(float value, const char* fieldName, std::size_t lineNumber) {
    if (value < 0.0f || value > 1.0f) {
        throw std::runtime_error(
            "invalid " + std::string(fieldName) + " on line " + std::to_string(lineNumber) + ": expected 0..1");
    }
}

} // namespace

std::vector<SongSection> SongSectionCsv::read_file(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open input file: " + path);
    }

    std::vector<SongSection> sections;
    std::string line;
    std::size_t lineNumber = 0;
    while (std::getline(input, line)) {
        ++lineNumber;
        line = trim(std::move(line));
        if (line.empty() || line.front() == '#') {
            continue;
        }

        std::stringstream stream(line);
        std::vector<std::string> fields;
        std::string field;
        while (std::getline(stream, field, ',')) {
            fields.push_back(trim(std::move(field)));
        }
        if (fields.size() != 9) {
            throw std::runtime_error(
                "invalid section line " + std::to_string(lineNumber) + ": expected 9 comma-separated fields");
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

        if (section.id.empty()) {
            throw std::runtime_error("empty section id on line " + std::to_string(lineNumber));
        }
        if (section.startSeconds < 0.0 || section.endSeconds <= section.startSeconds) {
            throw std::runtime_error("invalid section timing on line " + std::to_string(lineNumber));
        }
        if (section.features.tempo <= 0.0f) {
            throw std::runtime_error("invalid tempo on line " + std::to_string(lineNumber));
        }
        require_unit_interval(section.features.bass, "bass", lineNumber);
        require_unit_interval(section.features.mid, "mid", lineNumber);
        require_unit_interval(section.features.treble, "treble", lineNumber);
        require_unit_interval(section.features.energy, "energy", lineNumber);

        sections.push_back(std::move(section));
    }

    if (sections.empty()) {
        throw std::runtime_error("input contains no song sections");
    }
    return sections;
}

} // namespace echoes::ai_prompt
