#pragma once

#include <string>
#include <vector>

namespace echoes::ai_prompt {

struct AudioFeatures {
    float bass = 0.f;
    float mid = 0.f;
    float treble = 0.f;
    float energy = 0.f;
    float tempo = 0.f;
    bool beat = false;
};

struct SceneSuggestion {
    std::string id;
    std::string description;
    std::string highlight;
    std::vector<std::string> keywords;
    float confidence = 0.f;
};

struct PromptResult {
    std::string prompt;
    std::string style;
    SceneSuggestion scene;
    std::vector<std::string> keywords;
};

struct StyleDescriptor {
    std::string name;
    std::string description;
    std::vector<std::string> palette;
    float warmth = 0.f;
    float contrast = 0.f;
};

class PromptDirector {
public:
    struct Settings {
        float tempoBias = 1.f;
        float energyBias = 1.f;
        float bassBias = 1.f;
    };

    static PromptResult generate_prompt_from_audio(const AudioFeatures& features, Settings settings = {});
    static StyleDescriptor generate_style(const AudioFeatures& features, Settings settings = {});
};

} // namespace echoes::ai_prompt
