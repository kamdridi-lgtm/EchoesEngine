#include "EchoesEngine/ai_prompt/PromptDirector.h"

#include <algorithm>
#include <array>
#include <cmath>

namespace echoes::ai_prompt {

namespace {

float dimension_score(float value, float target, float width) {
    if (width <= 0.f) {
        return value == target ? 1.f : 0.f;
    }
    const float diff = std::fabs(value - target);
    return std::clamp(1.f - (diff / width), 0.f, 1.f);
}

struct SceneSignature {
    const char* id;
    const char* description;
    float bass;
    float mid;
    float treble;
    float energy;
    float tempo;
    bool beatFriendly;
    const char* highlight;
    const char* tone;
    std::array<const char*, 3> keywords;
};

constexpr std::array<SceneSignature, 7> kSceneSignatures{{
    {
        "cyberpunk_city",
        "neon-lit cyberpunk city at midnight clawing toward dawn",
        0.9f,
        0.8f,
        0.7f,
        0.85f,
        128.f,
        true,
        "rain-slick neon streets",
        "electric haze",
        { "skyline bustle", "holographic glow", "maglev streaks" }
    },
    {
        "cosmic_blackhole",
        "slowly spiraling cosmic black hole wrapped in stardust",
        0.3f,
        0.4f,
        0.95f,
        0.4f,
        60.f,
        false,
        "silky gravitational tides",
        "void serenity",
        { "event horizon glow", "plasma filaments", "particle drift" }
    },
    {
        "industrial_hellscape",
        "molten industrial hellscape turning out infernal beats",
        0.85f,
        0.9f,
        0.45f,
        0.92f,
        140.f,
        true,
        "molten conveyors",
        "sputtering iron",
        { "slag sparks", "die-sawn gears", "compressed steam" }
    },
    {
        "alien_planet",
        "bio-luminescent alien planet where flora hums to the rhythm",
        0.55f,
        0.6f,
        0.65f,
        0.58f,
        100.f,
        false,
        "luminous flora arcs",
        "prismatic biosphere",
        { "floating monoliths", "silicate fauna", "iridescent skies" }
    },
    {
        "volcanic_world",
        "furious volcanic world pouring lava into the sky",
        0.9f,
        0.7f,
        0.55f,
        0.92f,
        110.f,
        true,
        "lava rivers",
        "fire-forged haze",
        { "embers bloom", "ash roars", "magma arcs" }
    },
    {
        "dark_cathedral",
        "gothic cathedral carved from obsidian with hush choirs",
        0.4f,
        0.85f,
        0.2f,
        0.35f,
        72.f,
        false,
        "stained glass murmurs",
        "solemn cathedral",
        { "vaulted arches", "candle glow", "choir echoes" }
    },
    {
        "nebula_space",
        "slow nebula drift threading star-dusted clouds",
        0.25f,
        0.35f,
        0.9f,
        0.5f,
        80.f,
        false,
        "prismatic mist",
        "gossamer void",
        { "stardust ribbons", "plume trails", "halo constellations" }
    },
}};

SceneSuggestion evaluate_scene(const AudioFeatures& features, const PromptDirector::Settings& settings) {
    AudioFeatures biased = features;
    biased.tempo *= settings.tempoBias;
    biased.energy *= settings.energyBias;
    biased.bass *= settings.bassBias;

    SceneSuggestion best;
    best.confidence = -1.f;

    for (const auto& signature : kSceneSignatures) {
        const float bassScore = dimension_score(biased.bass, signature.bass, 1.f);
        const float midScore = dimension_score(biased.mid, signature.mid, 1.f);
        const float trebleScore = dimension_score(biased.treble, signature.treble, 1.f);
        const float energyScore = dimension_score(biased.energy, signature.energy, 1.f);
        const float tempoScore = dimension_score(biased.tempo, signature.tempo, 60.f);

        float score = (bassScore + midScore + trebleScore + energyScore + tempoScore) / 5.f;
        if (biased.beat && signature.beatFriendly) {
            score += 0.03f;
        }
        score = std::clamp(score, 0.f, 1.f);

        if (score <= best.confidence) {
            continue;
        }

        best.id = signature.id;
        best.description = signature.description;
        best.highlight = signature.highlight;
        best.keywords.clear();
        best.keywords.emplace_back(signature.tone);
        best.keywords.emplace_back(signature.highlight);
        for (const char* word : signature.keywords) {
            best.keywords.emplace_back(word);
        }
        best.confidence = score;
    }

    if (best.keywords.empty()) {
        best.keywords.emplace_back("cinematic pulse");
    }

    return best;
}

struct StyleProfile {
    const char* name;
    const char* description;
    float targetEnergy;
    float targetTempo;
    float targetBass;
    float targetTreble;
    float warmth;
    float contrast;
    bool beatAccent;
    std::array<const char*, 3> palette;
};

constexpr std::array<StyleProfile, 4> kStyleProfiles{{
    {
        "HyperPulse",
        "High-contrast neon with synchronized beats",
        0.9f,
        135.f,
        0.85f,
        0.6f,
        0.8f,
        0.9f,
        true,
        { "neon purple", "electric blue", "chrome sparks" }
    },
    {
        "Cosmic Wash",
        "Soft, ethereal glow that drifts like starlight",
        0.4f,
        70.f,
        0.3f,
        0.95f,
        0.45f,
        0.4f,
        false,
        { "aurora teal", "soft violet", "glimmer silver" }
    },
    {
        "Industrial Grind",
        "Gritty metallic textures with bold shadows",
        0.75f,
        115.f,
        0.85f,
        0.4f,
        0.6f,
        0.85f,
        true,
        { "rust orange", "coal black", "acid green" }
    },
    {
        "Mystic Drift",
        "Dreamy gradients framed by slow, cinematic motion",
        0.55f,
        95.f,
        0.5f,
        0.7f,
        0.5f,
        0.6f,
        false,
        { "midnight blue", "amber", "cool slate" }
    },
}};

StyleDescriptor evaluate_style(const AudioFeatures& features, const PromptDirector::Settings& settings) {
    AudioFeatures biased = features;
    biased.tempo *= settings.tempoBias;
    biased.energy *= settings.energyBias;
    biased.bass *= settings.bassBias;

    const StyleProfile* best = nullptr;
    float bestScore = -1.f;

    for (const auto& profile : kStyleProfiles) {
        const float energyScore = dimension_score(biased.energy, profile.targetEnergy, 0.6f);
        const float bassScore = dimension_score(biased.bass, profile.targetBass, 0.6f);
        const float trebleScore = dimension_score(biased.treble, profile.targetTreble, 0.6f);
        const float tempoScore = dimension_score(biased.tempo, profile.targetTempo, 50.f);
        float score = (energyScore + bassScore + trebleScore + tempoScore) / 4.f;
        if (biased.beat && profile.beatAccent) {
            score += 0.04f;
        }
        score = std::clamp(score, 0.f, 1.f);

        if (score > bestScore) {
            bestScore = score;
            best = &profile;
        }
    }

    StyleDescriptor descriptor;
    if (best == nullptr) {
        descriptor.name = "Neutral Cinematic";
        descriptor.description = "Balanced lighting for any scene";
        descriptor.warmth = 0.5f;
        descriptor.contrast = 0.5f;
        descriptor.palette = { "balanced amber", "soft cyan", "mist white" };
        return descriptor;
    }

    descriptor.name = best->name;
    descriptor.description = best->description;
    descriptor.warmth = best->warmth;
    descriptor.contrast = best->contrast;
    for (const char* tone : best->palette) {
        descriptor.palette.emplace_back(tone);
    }

    return descriptor;
}

} // namespace

PromptResult PromptDirector::generate_prompt_from_audio(const AudioFeatures& features, Settings settings) {
    const SceneSuggestion scene = evaluate_scene(features, settings);
    const StyleDescriptor style = generate_style(features, settings);

    PromptResult result;
    result.scene = scene;
    result.style = style.name;
    result.keywords = scene.keywords;
    result.keywords.push_back(style.name);
    for (const auto& tone : style.palette) {
        result.keywords.push_back(tone);
    }

    std::string focus = scene.highlight.empty() ? scene.id : scene.highlight;
    std::string primeKeyword = scene.keywords.empty() ? "cinematic energy" : scene.keywords.front();
    result.prompt = "Render " + scene.description + " with " + style.name + " lighting, weaving " + focus + " through " + primeKeyword + ".";
    return result;
}

StyleDescriptor PromptDirector::generate_style(const AudioFeatures& features, Settings settings) {
    return evaluate_style(features, settings);
}

} // namespace echoes::ai_prompt
