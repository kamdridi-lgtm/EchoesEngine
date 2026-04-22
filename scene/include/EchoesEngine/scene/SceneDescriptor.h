#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace echoes::scene {

struct Vec3 {
    float x = 0.f;
    float y = 0.f;
    float z = 0.f;
};

struct Material {
    std::string name;
    float roughness = 1.f;
    float metallic = 0.f;
    float emissive = 0.f;
};

enum class GeometryType : uint8_t {
    Structure,
    Terrain,
    Emissive,
    Fluid
};

struct GeometryPrimitive {
    std::string id;
    GeometryType type = GeometryType::Structure;
    Vec3 position;
    Vec3 rotation;
    Vec3 scale{1.f, 1.f, 1.f};
    Material material;
};

enum class LightType : uint8_t {
    Ambient,
    Directional,
    Point,
    Spot
};

struct LightPreset {
    std::string name;
    LightType type = LightType::Ambient;
    Vec3 direction{0.f, -1.f, 0.f};
    Vec3 color{1.f, 1.f, 1.f};
    float intensity = 1.f;
    float radius = 10.f;
};

struct ParticleEmitter {
    std::string id;
    Vec3 origin;
    Vec3 velocityBias;
    Vec3 color{1.f, 1.f, 1.f};
    float emissionRate = 60.f;
    float lifetime = 2.f;
    float spread = 1.f;
};

struct CameraKeyframe {
    Vec3 position;
    Vec3 target;
    float duration = 1.f;
    float ease = 0.3f;
};

struct CameraPath {
    std::string label;
    std::vector<CameraKeyframe> keyframes;
    float loopTime = 0.f;
};

struct SceneDescriptor {
    std::string name;
    std::string description;
    std::vector<GeometryPrimitive> geometry;
    LightPreset lighting;
    std::vector<ParticleEmitter> emitters;
    CameraPath cameraPath;
};

} // namespace echoes::scene
