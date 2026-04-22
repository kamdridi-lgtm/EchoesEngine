#include "EchoesEngine/scene/SceneFactory.h"

#include <algorithm>
#include <cctype>

namespace echoes::scene {

namespace {

Vec3 makeVec3(float x, float y, float z) {
    return Vec3{x, y, z};
}

SceneDescriptor buildCyberpunkCity() {
    SceneDescriptor scene;
    scene.name = "cyberpunk_city";
    scene.description = "Vertical corridors flooded with neon and smog, perfect for rapid paced camera runs.";

    scene.geometry.push_back(
        GeometryPrimitive{"neon_arcade", GeometryType::Structure, makeVec3(0.f, 0.f, -30.f), makeVec3(0.f, 45.f, 0.f),
                          makeVec3(30.f, 12.f, 2.f), Material{"reflective_steel", 0.05f, 1.f, 0.3f}});
    scene.geometry.push_back(
        GeometryPrimitive{"holo_tower", GeometryType::Structure, makeVec3(-15.f, 40.f, 0.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(4.f, 80.f, 4.f), Material{"glass_neon", 0.1f, 0.f, 0.8f}});
    scene.geometry.push_back(
        GeometryPrimitive{"hover_highway", GeometryType::Structure, makeVec3(0.f, 15.f, 20.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(60.f, 0.5f, 6.f), Material{"slick_concrete", 0.8f, 0.2f, 0.f}});

    scene.lighting = LightPreset{"neon_ambient", LightType::Ambient, makeVec3(0.f, -1.f, 0.f),
                                 makeVec3(0.4f, 0.2f, 1.f), 1.0f, 80.f};

    scene.emitters.push_back(
        ParticleEmitter{"drone_trail", makeVec3(0.f, 18.f, -5.f), makeVec3(0.f, -8.f, 40.f), makeVec3(0.8f, 0.1f, 1.f), 80.f, 2.5f, 0.8f});
    scene.emitters.push_back(
        ParticleEmitter{"smog", makeVec3(0.f, 6.f, 0.f), makeVec3(0.f, 2.f, 0.f), makeVec3(0.1f, 0.1f, 0.1f), 120.f, 4.f, 3.f});

    scene.cameraPath.label = "neon_run";
    scene.cameraPath.keyframes = {
        {makeVec3(0.f, 10.f, 25.f), makeVec3(0.f, 5.f, 0.f), 2.f, 0.2f},
        {makeVec3(-10.f, 18.f, 5.f), makeVec3(0.f, 12.f, -6.f), 3.f, 0.3f},
        {makeVec3(10.f, 25.f, -10.f), makeVec3(0.f, 20.f, -20.f), 2.5f, 0.35f},
    };
    scene.cameraPath.loopTime = 7.5f;
    return scene;
}

SceneDescriptor buildCosmicBlackhole() {
    SceneDescriptor scene;
    scene.name = "cosmic_blackhole";
    scene.description = "Gravity wells and curved spacetime light bending, ideal for slow ambient movement.";

    scene.geometry.push_back(
        GeometryPrimitive{"event_horizon", GeometryType::Emissive, makeVec3(0.f, 0.f, 0.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(60.f, 2.f, 60.f), Material{"dark_matter", 0.2f, 0.f, 1.f}});
    scene.geometry.push_back(
        GeometryPrimitive{"accretion_rings", GeometryType::Emissive, makeVec3(0.f, 0.f, 0.f), makeVec3(90.f, 0.f, 0.f),
                          makeVec3(150.f, 0.5f, 150.f), Material{"plasma", 0.1f, 0.f, 0.6f}});
    scene.geometry.push_back(
        GeometryPrimitive{"distorted_stars", GeometryType::Fluid, makeVec3(0.f, 20.f, 0.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(200.f, 40.f, 200.f), Material{"ion_cloud", 0.9f, 0.f, 0.7f}});

    scene.lighting = LightPreset{"gravity_reflection", LightType::Point, makeVec3(0.f, 0.f, 0.f),
                                 makeVec3(0.6f, 0.2f, 1.f), 1.7f, 150.f};

    scene.emitters.push_back(
        ParticleEmitter{"cosmic_dust", makeVec3(0.f, 0.f, 0.f), makeVec3(0.f, 0.f, 0.f), makeVec3(0.2f, 0.3f, 1.f), 40.f, 6.f, 5.f});
    scene.emitters.push_back(
        ParticleEmitter{"photon_orbs", makeVec3(0.f, 15.f, 0.f), makeVec3(0.f, -15.f, 15.f), makeVec3(1.f, 0.7f, 0.4f), 30.f, 3.f, 1.f});

    scene.cameraPath.label = "slow_orbit";
    scene.cameraPath.keyframes = {
        {makeVec3(0.f, 40.f, 110.f), makeVec3(0.f, 0.f, 0.f), 4.f, 0.5f},
        {makeVec3(0.f, 80.f, 20.f), makeVec3(0.f, 0.f, 0.f), 5.f, 0.45f},
        {makeVec3(0.f, 20.f, -120.f), makeVec3(0.f, 0.f, 0.f), 3.5f, 0.4f},
    };
    scene.cameraPath.loopTime = 12.5f;
    return scene;
}

SceneDescriptor buildIndustrialHellscape() {
    SceneDescriptor scene;
    scene.name = "industrial_hellscape";
    scene.description = "Charred industrial complex with molten flows, suited for aggressive camera cuts.";

    scene.geometry.push_back(
        GeometryPrimitive{"smelting_riser", GeometryType::Structure, makeVec3(0.f, 30.f, -25.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(8.f, 60.f, 8.f), Material{"burned_metal", 0.7f, 0.6f, 0.5f}});
    scene.geometry.push_back(
        GeometryPrimitive{"molten_channel", GeometryType::Fluid, makeVec3(-20.f, 5.f, 15.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(40.f, 1.f, 10.f), Material{"lava", 0.3f, 0.f, 1.f}});
    scene.geometry.push_back(
        GeometryPrimitive{"crane_gig", GeometryType::Structure, makeVec3(15.f, 25.f, 10.f), makeVec3(0.f, 30.f, 0.f),
                          makeVec3(30.f, 5.f, 2.f), Material{"rusted_steel", 0.9f, 0.4f, 0.f}});

    scene.lighting = LightPreset{"inferno_glow", LightType::Directional, makeVec3(-0.2f, -1.f, 0.f),
                                 makeVec3(1.f, 0.4f, 0.f), 2.3f, 60.f};

    scene.emitters.push_back(
        ParticleEmitter{"ember", makeVec3(-10.f, 2.f, 12.f), makeVec3(0.f, 45.f, 0.f), makeVec3(1.f, 0.5f, 0.1f), 140.f, 1.2f, 1.f});
    scene.emitters.push_back(
        ParticleEmitter{"sulfur_smoke", makeVec3(0.f, 25.f, -5.f), makeVec3(0.f, 12.f, 0.f), makeVec3(0.6f, 0.4f, 0.1f), 90.f, 3.f, 2.f});

    scene.cameraPath.label = "industrial_sweep";
    scene.cameraPath.keyframes = {
        {makeVec3(-40.f, 12.f, 0.f), makeVec3(0.f, 15.f, 0.f), 2.5f, 0.25f},
        {makeVec3(10.f, 30.f, -10.f), makeVec3(0.f, 20.f, -20.f), 2.f, 0.3f},
        {makeVec3(30.f, 8.f, 15.f), makeVec3(5.f, 10.f, 0.f), 2.2f, 0.2f},
    };
    scene.cameraPath.loopTime = 6.7f;
    return scene;
}

SceneDescriptor buildAlienPlanet() {
    SceneDescriptor scene;
    scene.name = "alien_planet";
    scene.description = "Floating flora, bioluminescent terrain, and slow panoramic sweeps.";

    scene.geometry.push_back(
        GeometryPrimitive{"floating_crystals", GeometryType::Emissive, makeVec3(0.f, 15.f, 5.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(5.f, 15.f, 5.f), Material{"crystal", 0.2f, 0.f, 0.9f}});
    scene.geometry.push_back(
        GeometryPrimitive{"alien_forest", GeometryType::Structure, makeVec3(25.f, 0.f, -10.f), makeVec3(0.f, 0.f, 45.f),
                          makeVec3(25.f, 10.f, 25.f), Material{"biomoss", 0.7f, 0.f, 0.4f}});
    scene.geometry.push_back(
        GeometryPrimitive{"levitating_plateaus", GeometryType::Structure, makeVec3(-30.f, 25.f, -35.f),
                          makeVec3(0.f, 0.f, 0.f), makeVec3(18.f, 6.f, 18.f), Material{"stone", 0.8f, 0.f, 0.f}});

    scene.lighting = LightPreset{"aurora", LightType::Ambient, makeVec3(0.f, -1.f, 0.f),
                                 makeVec3(0.5f, 1.f, 0.8f), 1.3f, 120.f};

    scene.emitters.push_back(
        ParticleEmitter{"spore_field", makeVec3(-5.f, 3.f, -5.f), makeVec3(0.f, 8.f, 0.f), makeVec3(0.3f, 1.f, 0.5f), 60.f, 4.f, 4.f});
    scene.emitters.push_back(
        ParticleEmitter{"glow_motes", makeVec3(15.f, 12.f, 0.f), makeVec3(5.f, -3.f, 10.f), makeVec3(1.f, 0.2f, 1.f), 30.f, 5.f, 2.f});

    scene.cameraPath.label = "floating_pan";
    scene.cameraPath.keyframes = {
        {makeVec3(-60.f, 35.f, 15.f), makeVec3(0.f, 10.f, -10.f), 3.5f, 0.4f},
        {makeVec3(0.f, 20.f, -50.f), makeVec3(10.f, 15.f, -10.f), 3.f, 0.25f},
        {makeVec3(50.f, 45.f, 20.f), makeVec3(30.f, 5.f, 0.f), 3.8f, 0.35f},
    };
    scene.cameraPath.loopTime = 10.3f;
    return scene;
}

SceneDescriptor buildVolcanicWorld() {
    SceneDescriptor scene;
    scene.name = "volcanic_world";
    scene.description = "Explosive lava bursts, lightning-charged ash, and low orbit flyovers.";

    scene.geometry.push_back(
        GeometryPrimitive{"main_caldera", GeometryType::Structure, makeVec3(0.f, 4.f, 0.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(50.f, 6.f, 50.f), Material{"scorched_rock", 0.7f, 0.f, 0.f}});
    scene.geometry.push_back(
        GeometryPrimitive{"lava_spine", GeometryType::Fluid, makeVec3(10.f, 8.f, -15.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(5.f, 20.f, 5.f), Material{"magma", 0.1f, 0.f, 1.f}});
    scene.geometry.push_back(
        GeometryPrimitive{"ash_cloud", GeometryType::Emissive, makeVec3(0.f, 30.f, 0.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(200.f, 50.f, 200.f), Material{"charged_gas", 0.9f, 0.f, 0.8f}});

    scene.lighting = LightPreset{"lava_flash", LightType::Directional, makeVec3(0.4f, -1.f, -0.2f),
                                 makeVec3(1.f, 0.5f, 0.2f), 2.0f, 90.f};

    scene.emitters.push_back(
        ParticleEmitter{"lava_burst", makeVec3(8.f, 10.f, -10.f), makeVec3(0.f, 40.f, 0.f), makeVec3(1.f, 0.4f, 0.f), 200.f, 1.f, 0.5f});
    scene.emitters.push_back(
        ParticleEmitter{"ash_whorl", makeVec3(0.f, 20.f, 0.f), makeVec3(0.f, 15.f, 0.f), makeVec3(0.2f, 0.2f, 0.2f), 120.f, 5.f, 3.f});

    scene.cameraPath.label = "lava_orbit";
    scene.cameraPath.keyframes = {
        {makeVec3(0.f, 60.f, 80.f), makeVec3(0.f, 10.f, 0.f), 3.f, 0.4f},
        {makeVec3(20.f, 30.f, -30.f), makeVec3(0.f, 5.f, 0.f), 2.5f, 0.3f},
        {makeVec3(-40.f, 25.f, -10.f), makeVec3(0.f, 8.f, -5.f), 2.2f, 0.25f},
    };
    scene.cameraPath.loopTime = 7.7f;
    return scene;
}

SceneDescriptor buildDarkCathedral() {
    SceneDescriptor scene;
    scene.name = "dark_cathedral";
    scene.description = "Gothic arches, stained glass, and dramatic low angles.";

    scene.geometry.push_back(
        GeometryPrimitive{"spire", GeometryType::Structure, makeVec3(-10.f, 50.f, 0.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(5.f, 60.f, 5.f), Material{"aged_stone", 0.9f, 0.f, 0.f}});
    scene.geometry.push_back(
        GeometryPrimitive{"nave", GeometryType::Structure, makeVec3(0.f, 6.f, 0.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(40.f, 12.f, 12.f), Material{"polished_marble", 0.3f, 0.f, 0.2f}});
    scene.geometry.push_back(
        GeometryPrimitive{"glass_window", GeometryType::Emissive, makeVec3(0.f, 30.f, -5.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(22.f, 20.f, 1.f), Material{"stained_glass", 0.1f, 0.f, 0.7f}});

    scene.lighting = LightPreset{"candlelight", LightType::Spot, makeVec3(0.f, -1.f, 0.f),
                                 makeVec3(1.f, 0.6f, 0.4f), 1.5f, 40.f};

    scene.emitters.push_back(
        ParticleEmitter{"candle_dust", makeVec3(0.f, 5.f, 0.f), makeVec3(0.f, 15.f, 0.f), makeVec3(1.f, 0.8f, 0.6f), 60.f, 3.f, 1.5f});
    scene.emitters.push_back(
        ParticleEmitter{"prayer_motes", makeVec3(0.f, 25.f, -10.f), makeVec3(0.f, 5.f, 0.f), makeVec3(0.4f, 0.6f, 1.f), 30.f, 4.f, 0.8f});

    scene.cameraPath.label = "cathedral_pano";
    scene.cameraPath.keyframes = {
        {makeVec3(-30.f, 10.f, 50.f), makeVec3(0.f, 12.f, 0.f), 3.f, 0.4f},
        {makeVec3(0.f, 30.f, -10.f), makeVec3(0.f, 25.f, 0.f), 2.7f, 0.35f},
        {makeVec3(25.f, 15.f, 40.f), makeVec3(10.f, 12.f, 0.f), 2.4f, 0.25f},
    };
    scene.cameraPath.loopTime = 8.2f;
    return scene;
}

SceneDescriptor buildNebulaSpace() {
    SceneDescriptor scene;
    scene.name = "nebula_space";
    scene.description = "Colorful nebula with drifting tendrils and gentle camera drifts.";

    scene.geometry.push_back(
        GeometryPrimitive{"nebula_filaments", GeometryType::Fluid, makeVec3(0.f, 0.f, 0.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(200.f, 100.f, 200.f), Material{"plasma_field", 0.9f, 0.f, 0.9f}});
    scene.geometry.push_back(
        GeometryPrimitive{"orbital_platform", GeometryType::Structure, makeVec3(30.f, 5.f, -20.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(20.f, 5.f, 40.f), Material{"ceramic_alloy", 0.05f, 0.3f, 0.5f}});
    scene.geometry.push_back(
        GeometryPrimitive{"floating_pearl", GeometryType::Emissive, makeVec3(-25.f, 8.f, 15.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(6.f, 6.f, 6.f), Material{"pearl", 0.2f, 0.f, 0.8f}});

    scene.lighting = LightPreset{"nebula_glow", LightType::Ambient, makeVec3(0.f, -1.f, 0.f),
                                 makeVec3(0.2f, 1.f, 0.8f), 1.8f, 180.f};

    scene.emitters.push_back(
        ParticleEmitter{"nebula_sparkles", makeVec3(0.f, 20.f, 0.f), makeVec3(0.f, -4.f, 0.f), makeVec3(0.6f, 0.9f, 1.f), 50.f, 6.f, 5.f});
    scene.emitters.push_back(
        ParticleEmitter{"energy_arc", makeVec3(15.f, 15.f, -10.f), makeVec3(4.f, 0.f, 20.f), makeVec3(1.f, 0.4f, 1.f), 35.f, 2.5f, 1.5f});

    scene.cameraPath.label = "nebula_drift";
    scene.cameraPath.keyframes = {
        {makeVec3(0.f, 40.f, 200.f), makeVec3(0.f, 10.f, 0.f), 4.f, 0.3f},
        {makeVec3(50.f, 25.f, 40.f), makeVec3(15.f, 8.f, 0.f), 3.2f, 0.3f},
        {makeVec3(-40.f, 50.f, -50.f), makeVec3(-10.f, 20.f, -5.f), 3.6f, 0.4f},
    };
    scene.cameraPath.loopTime = 10.8f;
    return scene;
}

SceneDescriptor buildFantasyRealm() {
    SceneDescriptor scene;
    scene.name = "fantasy_realm";
    scene.description = "Floating islands, ethereal waterfalls, and golden sunsets.";

    scene.geometry.push_back(
        GeometryPrimitive{"floating_island", GeometryType::Terrain, makeVec3(0.f, 20.f, -40.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(25.f, 10.f, 25.f), Material{"grass_stone", 0.6f, 0.f, 0.1f}});
    scene.geometry.push_back(
        GeometryPrimitive{"ethereal_waterfall", GeometryType::Fluid, makeVec3(10.f, 15.f, -35.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(4.f, 30.f, 1.f), Material{"shimmering_water", 0.2f, 0.f, 0.9f}});
    scene.geometry.push_back(
        GeometryPrimitive{"ancient_ruin", GeometryType::Structure, makeVec3(-15.f, 22.f, -45.f), makeVec3(0.f, 30.f, 0.f),
                          makeVec3(8.f, 15.f, 8.f), Material{"mossy_marble", 0.8f, 0.1f, 0.2f}});

    scene.lighting = LightPreset{"golden_hour", LightType::Directional, makeVec3(0.5f, -0.5f, -0.7f),
                                 makeVec3(1.f, 0.8f, 0.4f), 1.6f, 100.f};

    scene.emitters.push_back(
        ParticleEmitter{"magic_wisps", makeVec3(0.f, 25.f, -30.f), makeVec3(0.f, 5.f, 0.f), makeVec3(0.6f, 0.9f, 1.f), 40.f, 5.f, 2.f});

    scene.cameraPath.label = "fantasy_soar";
    scene.cameraPath.keyframes = {
        {makeVec3(-50.f, 40.f, 50.f), makeVec3(0.f, 20.f, -20.f), 4.f, 0.35f},
        {makeVec3(30.f, 35.f, 15.f), makeVec3(10.f, 25.f, -10.f), 3.5f, 0.3f},
    };
    scene.cameraPath.loopTime = 9.0f;
    return scene;
}

SceneDescriptor buildDystopianWasteland() {
    SceneDescriptor scene;
    scene.name = "dystopian_wasteland";
    scene.description = "Deserted dunes, rusted remains, and a harsh, dying sun.";

    scene.geometry.push_back(
        GeometryPrimitive{"rust_monolith", GeometryType::Structure, makeVec3(0.f, 15.f, -50.f), makeVec3(0.f, 15.f, 0.f),
                          makeVec3(12.f, 30.f, 12.f), Material{"rusted_iron", 0.9f, 0.5f, 0.f}});
    scene.geometry.push_back(
        GeometryPrimitive{"sand_dune", GeometryType::Terrain, makeVec3(0.f, 2.f, 0.f), makeVec3(0.f, 0.f, 0.f),
                          makeVec3(100.f, 4.f, 100.f), Material{"desert_sand", 0.8f, 0.f, 0.05f}});
    scene.geometry.push_back(
        GeometryPrimitive{"broken_scaffold", GeometryType::Structure, makeVec3(25.f, 10.f, -20.f), makeVec3(0.f, -20.f, 10.f),
                          makeVec3(15.f, 1.f, 8.f), Material{"decayed_alloy", 0.7f, 0.4f, 0.1f}});

    scene.lighting = LightPreset{"harsh_sun", LightType::Directional, makeVec3(-0.8f, -0.6f, -0.2f),
                                 makeVec3(1.f, 0.7f, 0.5f), 2.2f, 70.f};

    scene.emitters.push_back(
        ParticleEmitter{"sand_storm", makeVec3(0.f, 10.f, 0.f), makeVec3(20.f, -2.f, 10.f), makeVec3(0.7f, 0.6f, 0.4f), 150.f, 3.f, 4.f});

    scene.cameraPath.label = "wasteland_crawl";
    scene.cameraPath.keyframes = {
        {makeVec3(50.f, 8.f, 40.f), makeVec3(0.f, 5.f, -20.f), 4.5f, 0.2f},
        {makeVec3(-30.f, 12.f, 10.f), makeVec3(10.f, 10.f, -40.f), 5.f, 0.25f},
    };
    scene.cameraPath.loopTime = 11.0f;
    return scene;
}

const std::vector<SceneDescriptor>& sceneLibrary() {
    static const std::vector<SceneDescriptor> library{
        buildCyberpunkCity(),
        buildCosmicBlackhole(),
        buildIndustrialHellscape(),
        buildAlienPlanet(),
        buildVolcanicWorld(),
        buildDarkCathedral(),
        buildNebulaSpace(),
        buildFantasyRealm(),
        buildDystopianWasteland(),
    };
    return library;
}

std::string normalize(std::string_view value) {
    std::string lower;
    lower.reserve(value.size());
    for (char ch : value) {
        lower.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(ch))));
    }
    return lower;
}

} // namespace

std::vector<std::string> SceneFactory::availableScenes() {
    std::vector<std::string> names;
    names.reserve(sceneLibrary().size());
    for (const auto& entry : sceneLibrary()) {
        names.push_back(entry.name);
    }
    return names;
}

std::optional<SceneDescriptor> SceneFactory::createScene(std::string_view name) {
    const auto normalizedRequest = normalize(name);
    for (const auto& entry : sceneLibrary()) {
        const auto normalized = normalize(entry.name);
        if (normalized == normalizedRequest) {
            return entry;
        }
    }
    return std::nullopt;
}

} // namespace echoes::scene
