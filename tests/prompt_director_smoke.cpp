#include "EchoesEngine/ai_prompt/PromptDirector.h"

#include <iostream>
#include <string>

using echoes::ai_prompt::AudioFeatures;
using echoes::ai_prompt::PromptDirector;

namespace {

bool require(bool condition, const std::string& message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        return false;
    }
    std::cout << "PASS: " << message << '\n';
    return true;
}

} // namespace

int main() {
    bool ok = true;

    const AudioFeatures industrial{
        .bass = 0.86f,
        .mid = 0.90f,
        .treble = 0.45f,
        .energy = 0.93f,
        .tempo = 140.0f,
        .beat = true,
    };

    const auto industrialResult = PromptDirector::generate_prompt_from_audio(industrial);
    ok &= require(industrialResult.scene.id == "industrial_hellscape",
                  "industrial audio selects industrial_hellscape");
    ok &= require(industrialResult.style == "Industrial Grind" || industrialResult.style == "HyperPulse",
                  "industrial audio selects an energetic cinematic style");
    ok &= require(!industrialResult.prompt.empty(), "industrial prompt is non-empty");
    ok &= require(industrialResult.scene.confidence >= 0.75f,
                  "industrial scene confidence is high");

    const AudioFeatures cathedral{
        .bass = 0.40f,
        .mid = 0.85f,
        .treble = 0.20f,
        .energy = 0.35f,
        .tempo = 72.0f,
        .beat = false,
    };

    const auto cathedralResult = PromptDirector::generate_prompt_from_audio(cathedral);
    ok &= require(cathedralResult.scene.id == "dark_cathedral",
                  "slow solemn audio selects dark_cathedral");
    ok &= require(!cathedralResult.keywords.empty(), "cathedral prompt has keywords");

    const AudioFeatures cosmic{
        .bass = 0.30f,
        .mid = 0.40f,
        .treble = 0.95f,
        .energy = 0.40f,
        .tempo = 60.0f,
        .beat = false,
    };

    const auto cosmicResult = PromptDirector::generate_prompt_from_audio(cosmic);
    ok &= require(cosmicResult.scene.id == "cosmic_blackhole",
                  "ethereal audio selects cosmic_blackhole");

    if (!ok) {
        return 1;
    }

    std::cout << "PromptDirector deterministic smoke suite passed.\n";
    return 0;
}
