#include "RenderPipeline.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <sstream>
#include <utility>
#include <vector>

namespace echoes::render {

namespace {

using SceneVec3 = echoes::scene::Vec3;
using CameraVec3 = echoes::camera::Vec3;
using CameraTransform = echoes::camera::CameraTransform;

constexpr float kPi = 3.14159265358979323846f;

struct Color {
    float r = 0.0f;
    float g = 0.0f;
    float b = 0.0f;
};

struct ProjectedPoint {
    float x = 0.0f;
    float y = 0.0f;
    float depth = 0.0f;
    float scale = 1.0f;
    bool visible = false;
};

CameraVec3 toCameraVec3(const SceneVec3& value) {
    return {value.x, value.y, value.z};
}

Color clampColor(Color color) {
    color.r = std::clamp(color.r, 0.0f, 1.0f);
    color.g = std::clamp(color.g, 0.0f, 1.0f);
    color.b = std::clamp(color.b, 0.0f, 1.0f);
    return color;
}

Color operator+(const Color& lhs, const Color& rhs) {
    return {lhs.r + rhs.r, lhs.g + rhs.g, lhs.b + rhs.b};
}

Color operator-(const Color& lhs, const Color& rhs) {
    return {lhs.r - rhs.r, lhs.g - rhs.g, lhs.b - rhs.b};
}

Color operator*(const Color& color, float scalar) {
    return {color.r * scalar, color.g * scalar, color.b * scalar};
}

Color operator*(float scalar, const Color& color) {
    return color * scalar;
}

Color lerp(const Color& a, const Color& b, float t) {
    const float clamped = std::clamp(t, 0.0f, 1.0f);
    return a * (1.0f - clamped) + b * clamped;
}

float fract(float value) {
    return value - std::floor(value);
}

float hash2(float x, float y) {
    const float dot = x * 127.1f + y * 311.7f;
    return fract(std::sin(dot) * 43758.5453123f);
}

float smoothStep(float edge0, float edge1, float value) {
    if (edge0 == edge1) {
        return value < edge0 ? 0.0f : 1.0f;
    }

    const float t = std::clamp((value - edge0) / (edge1 - edge0), 0.0f, 1.0f);
    return t * t * (3.0f - 2.0f * t);
}

float noise(float x, float y) {
    const float ix = std::floor(x);
    const float iy = std::floor(y);
    const float fx = fract(x);
    const float fy = fract(y);

    const float a = hash2(ix, iy);
    const float b = hash2(ix + 1.0f, iy);
    const float c = hash2(ix, iy + 1.0f);
    const float d = hash2(ix + 1.0f, iy + 1.0f);

    const float ux = fx * fx * (3.0f - 2.0f * fx);
    const float uy = fy * fy * (3.0f - 2.0f * fy);

    return std::lerp(a, b, ux) + (c - a) * uy * (1.0f - ux) + (d - b) * ux * uy;
}

float fbm(float x, float y, int octaves = 5) {
    float value = 0.0f;
    float amplitude = 0.5f;
    for (int i = 0; i < octaves; ++i) {
        value += amplitude * noise(x, y);
        x *= 2.0f;
        y *= 2.0f;
        amplitude *= 0.5f;
    }
    return value;
}

Color basePaletteForScene(std::string_view sceneName) {
    if (sceneName == "cosmic_blackhole") {
        return {0.09f, 0.05f, 0.18f};
    }
    if (sceneName == "cyberpunk_city") {
        return {0.08f, 0.10f, 0.18f};
    }
    if (sceneName == "volcanic_world") {
        return {0.24f, 0.08f, 0.04f};
    }
    if (sceneName == "industrial_hellscape") {
        return {0.14f, 0.09f, 0.07f};
    }
    if (sceneName == "nebula_space") {
        return {0.08f, 0.12f, 0.18f};
    }
    if (sceneName == "dark_cathedral") {
        return {0.07f, 0.07f, 0.10f};
    }
    if (sceneName == "fantasy_realm") {
        return {0.10f, 0.18f, 0.22f};
    }
    if (sceneName == "dystopian_wasteland") {
        return {0.20f, 0.14f, 0.08f};
    }
    return {0.10f, 0.10f, 0.12f};
}

Color materialColor(const echoes::scene::GeometryPrimitive& primitive,
                    const echoes::scene::SceneDescriptor& scene) {
    Color base = basePaletteForScene(scene.name);
    switch (primitive.type) {
        case echoes::scene::GeometryType::Structure:
            base = lerp(base, {0.82f, 0.84f, 0.88f}, 0.32f);
            break;
        case echoes::scene::GeometryType::Terrain:
            base = lerp(base, {0.45f, 0.34f, 0.20f}, 0.45f);
            break;
        case echoes::scene::GeometryType::Emissive:
            base = lerp(base, {1.00f, 0.78f, 0.28f}, 0.80f);
            break;
        case echoes::scene::GeometryType::Fluid:
            base = lerp(base, {0.35f, 0.72f, 0.95f}, 0.60f);
            break;
    }

    base = lerp(base, {1.0f, 0.95f, 0.85f}, primitive.material.emissive * 0.35f);
    base = lerp(base, {0.75f, 0.80f, 1.0f}, primitive.material.metallic * 0.25f);
    return clampColor(base);
}

LightingPreset presetForScene(const echoes::scene::SceneDescriptor& scene) {
    const auto color = scene.lighting.color;
    const float intensity = std::max(0.6f, scene.lighting.intensity);
    return {
        {color.x * 0.08f, color.y * 0.08f, color.z * 0.08f},
        {color.x * 0.45f, color.y * 0.45f, color.z * 0.45f},
        {color.x * 0.90f, color.y * 0.90f, color.z * 0.90f},
        intensity
    };
}

ProjectedPoint projectPoint(const CameraVec3& world,
                            const CameraTransform& camera,
                            uint32_t width,
                            uint32_t height) {
    ProjectedPoint projected;
    if (width == 0 || height == 0) {
        return projected;
    }

    CameraVec3 forward = echoes::camera::normalize(camera.lookAt - camera.position);
    if (echoes::camera::length(forward) == 0.0f) {
        forward = {0.0f, 0.0f, -1.0f};
    }

    CameraVec3 up = echoes::camera::normalize(camera.up);
    if (echoes::camera::length(up) == 0.0f) {
        up = {0.0f, 1.0f, 0.0f};
    }

    CameraVec3 right = echoes::camera::normalize(echoes::camera::cross(forward, up));
    if (echoes::camera::length(right) == 0.0f) {
        right = {1.0f, 0.0f, 0.0f};
    }
    up = echoes::camera::normalize(echoes::camera::cross(right, forward));

    const CameraVec3 relative = world - camera.position;
    const float depth = echoes::camera::dot(relative, forward);
    if (depth <= 0.25f) {
        return projected;
    }

    const float fovRadians = std::max(0.25f, camera.fov * (kPi / 180.0f));
    const float focal = (static_cast<float>(height) * 0.5f) / std::tan(fovRadians * 0.5f);
    projected.x = static_cast<float>(width) * 0.5f + echoes::camera::dot(relative, right) * focal / depth;
    projected.y = static_cast<float>(height) * 0.5f - echoes::camera::dot(relative, up) * focal / depth;
    projected.depth = depth;
    projected.scale = focal / depth;
    projected.visible = projected.x > -width * 0.25f && projected.x < width * 1.25f &&
        projected.y > -height * 0.25f && projected.y < height * 1.25f;
    return projected;
}

void blendPixel(std::vector<uint8_t>& pixels,
                uint32_t width,
                uint32_t height,
                int x,
                int y,
                const Color& color,
                float alpha) {
    if (x < 0 || y < 0 || x >= static_cast<int>(width) || y >= static_cast<int>(height) || alpha <= 0.0f) {
        return;
    }

    const auto clamped = clampColor(color);
    const float a = std::clamp(alpha, 0.0f, 1.0f);
    const std::size_t index = (static_cast<std::size_t>(y) * width + static_cast<std::size_t>(x)) * 4;

    const float destR = static_cast<float>(pixels[index + 0]) / 255.0f;
    const float destG = static_cast<float>(pixels[index + 1]) / 255.0f;
    const float destB = static_cast<float>(pixels[index + 2]) / 255.0f;

    const Color blended{
        destR * (1.0f - a) + clamped.r * a,
        destG * (1.0f - a) + clamped.g * a,
        destB * (1.0f - a) + clamped.b * a,
    };

    pixels[index + 0] = static_cast<uint8_t>(std::clamp(blended.r, 0.0f, 1.0f) * 255.0f);
    pixels[index + 1] = static_cast<uint8_t>(std::clamp(blended.g, 0.0f, 1.0f) * 255.0f);
    pixels[index + 2] = static_cast<uint8_t>(std::clamp(blended.b, 0.0f, 1.0f) * 255.0f);
    pixels[index + 3] = 255;
}

void drawGlow(std::vector<uint8_t>& pixels,
              uint32_t width,
              uint32_t height,
              float centerX,
              float centerY,
              float radiusX,
              float radiusY,
              const Color& color,
              float opacity,
              float sharpness = 1.6f) {
    if (radiusX <= 0.0f || radiusY <= 0.0f) {
        return;
    }

    const int minX = std::max(0, static_cast<int>(std::floor(centerX - radiusX * 2.4f)));
    const int maxX = std::min(static_cast<int>(width) - 1, static_cast<int>(std::ceil(centerX + radiusX * 2.4f)));
    const int minY = std::max(0, static_cast<int>(std::floor(centerY - radiusY * 2.4f)));
    const int maxY = std::min(static_cast<int>(height) - 1, static_cast<int>(std::ceil(centerY + radiusY * 2.4f)));

    for (int y = minY; y <= maxY; ++y) {
        for (int x = minX; x <= maxX; ++x) {
            const float dx = (static_cast<float>(x) - centerX) / radiusX;
            const float dy = (static_cast<float>(y) - centerY) / radiusY;
            const float distance = std::sqrt(dx * dx + dy * dy);
            const float alpha = std::pow(std::max(0.0f, 1.0f - distance), sharpness) * opacity;
            blendPixel(pixels, width, height, x, y, color, alpha);
        }
    }
}

void drawRing(std::vector<uint8_t>& pixels,
              uint32_t width,
              uint32_t height,
              float centerX,
              float centerY,
              float radiusX,
              float radiusY,
              float thickness,
              const Color& color,
              float opacity) {
    if (radiusX <= 0.0f || radiusY <= 0.0f || thickness <= 0.0f) {
        return;
    }

    const int minX = std::max(0, static_cast<int>(std::floor(centerX - radiusX - thickness * 3.0f)));
    const int maxX = std::min(static_cast<int>(width) - 1, static_cast<int>(std::ceil(centerX + radiusX + thickness * 3.0f)));
    const int minY = std::max(0, static_cast<int>(std::floor(centerY - radiusY - thickness * 3.0f)));
    const int maxY = std::min(static_cast<int>(height) - 1, static_cast<int>(std::ceil(centerY + radiusY + thickness * 3.0f)));

    for (int y = minY; y <= maxY; ++y) {
        for (int x = minX; x <= maxX; ++x) {
            const float dx = (static_cast<float>(x) - centerX) / radiusX;
            const float dy = (static_cast<float>(y) - centerY) / radiusY;
            const float distance = std::sqrt(dx * dx + dy * dy);
            const float edge = std::abs(distance - 1.0f);
            const float alpha = std::pow(std::max(0.0f, 1.0f - edge / std::max(thickness, 0.001f)), 2.0f) * opacity;
            blendPixel(pixels, width, height, x, y, color, alpha);
        }
    }
}

void paintBackground(const SceneRenderRequest& request, std::vector<uint8_t>& pixels) {
    const uint32_t width = request.target.width;
    const uint32_t height = request.target.height;
    pixels.assign(static_cast<std::size_t>(width) * height * 4, 255);

    const Color base = basePaletteForScene(request.scene.name);
    const Color accent = clampColor({
        request.scene.lighting.color.x * 1.1f,
        request.scene.lighting.color.y * 1.1f,
        request.scene.lighting.color.z * 1.1f
    });

    const float time = request.timeSeconds;
    const float energy = std::clamp(request.audio.energy, 0.0f, 1.0f);
    const float bass = std::clamp(request.audio.bass, 0.0f, 1.0f);
    const float mid = std::clamp(request.audio.mid, 0.0f, 1.0f);
    const float treble = std::clamp(request.audio.treble, 0.0f, 1.0f);
    const float tempoPulse = std::max(0.4f, request.audio.tempo / 120.0f);
    const float beatPulse = request.audio.beat ? 1.0f : 0.0f;

    for (uint32_t y = 0; y < height; ++y) {
        for (uint32_t x = 0; x < width; ++x) {
            const float u = (static_cast<float>(x) + 0.5f) / static_cast<float>(width);
            const float v = (static_cast<float>(y) + 0.5f) / static_cast<float>(height);
            const float centeredX = u * 2.0f - 1.0f;
            const float centeredY = v * 2.0f - 1.0f;
            const float radius = std::sqrt(centeredX * centeredX + centeredY * centeredY);
            const float angle = std::atan2(centeredY, centeredX);
            const float vignette = 1.0f - smoothStep(0.4f, 1.4f, radius);

            // Procedural Volumetric Base
            float n = fbm(u * (3.0f + mid * 2.0f) + time * (0.1f + tempoPulse * 0.03f),
                          v * 3.0f - time * 0.05f,
                          4);
            Color color = lerp(base * 0.3f, base * 1.5f, n);

            if (request.scene.name == "cosmic_blackhole") {
                const float dist = std::max(0.01f, radius);
                const float swirl = fbm(dist * (5.0f + bass * 4.0f) - time * (0.8f + tempoPulse * 0.15f),
                                        angle * 2.0f / kPi + time * 0.2f,
                                        5);
                const float ringRadius = 0.4f + std::sin(time * tempoPulse) * 0.04f + bass * 0.06f;
                const float ring = std::exp(-std::pow(dist - ringRadius, 2.0f) * (200.0f - bass * 80.0f));
                const float accretion = std::pow(std::max(0.0f, 1.0f - dist * 2.0f), 4.0f);
                const float eventHorizon = smoothStep(0.12f, 0.14f, dist);

                color = color + accent * (swirl * accretion * (0.8f + energy * 0.4f) + ring * (1.5f + bass));
                color = color * eventHorizon;
            } else if (request.scene.name == "cyberpunk_city") {
                const float scanline = std::sin(v * (800.0f + request.audio.tempo * 2.0f) + time * 10.0f) * (0.04f + treble * 0.05f);
                const float grid = std::pow(std::abs(std::sin(u * 20.0f) * std::sin(v * 20.0f + time * 0.5f)), 0.1f);
                const float glow = fbm(u * 10.0f, v * 10.0f + time * 0.3f, 3);
                color = color + accent * (glow * (0.4f + energy * 0.4f) + scanline) + Color{0.1f, 0.0f, 0.2f} * grid * (1.0f - v);
            } else if (request.scene.name == "volcanic_world") {
                const float heat = fbm(u * 4.0f, v * 4.0f - time * (0.6f + bass * 0.3f), 5);
                const float lava = smoothStep(0.4f, 0.6f, fbm(u * 8.0f + time * 0.2f, v * 8.0f, 3));
                color = lerp(color, accent * (2.0f + bass), lava * heat * (1.0f - v));
            } else if (request.scene.name == "nebula_space") {
                const float cloud1 = fbm(u * 2.5f + time * 0.05f, v * 2.5f, 6);
                const float cloud2 = fbm(u * 5.0f - time * (0.02f + tempoPulse * 0.01f), v * 5.0f + time * 0.03f, 4);
                color = lerp(color, accent, cloud1 * 0.6f);
                color = color + accent * cloud2 * (0.4f + treble * 0.5f);
            } else if (request.scene.name == "industrial_hellscape") {
                const float smoke = fbm(u * 6.0f + std::sin(time) * 0.2f, v * 6.0f + time * 0.4f, 4);
                color = lerp(color, {0.1f, 0.1f, 0.1f}, smoke * 0.7f);
                color = color + Color{1.0f, 0.4f, 0.0f} * std::pow(smoke, 8.0f) * (2.0f + energy);
            }

            if (beatPulse > 0.0f) {
                const float wave = std::exp(-std::pow(radius - (0.22f + bass * 0.25f), 2.0f) * 120.0f);
                color = color + accent * wave * (0.6f + beatPulse * 0.5f);
            }

            // High-fidelity Stars
            const float starNoise = hash2(std::floor(u * 1000.0f), std::floor(v * 1000.0f));
            if (starNoise > 0.998f) {
                const float twinkle = 0.5f + 0.5f * std::sin(time * 3.0f + starNoise * 100.0f);
                color = color + Color{1.0f, 1.0f, 1.0f} * twinkle * ((starNoise - 0.998f) * 500.0f);
            }

            color = clampColor(color * (vignette + energy * 0.15f + treble * 0.05f));
            const std::size_t index = (static_cast<std::size_t>(y) * width + x) * 4;
            pixels[index + 0] = static_cast<uint8_t>(color.r * 255.0f);
            pixels[index + 1] = static_cast<uint8_t>(color.g * 255.0f);
            pixels[index + 2] = static_cast<uint8_t>(color.b * 255.0f);
            pixels[index + 3] = 255;
        }
    }
}

void paintGeometry(const SceneRenderRequest& request,
                   const LightingPreset& lighting,
                   std::vector<uint8_t>& pixels) {
    struct DrawItem {
        const echoes::scene::GeometryPrimitive* primitive = nullptr;
        ProjectedPoint projected;
    };

    std::vector<DrawItem> items;
    items.reserve(request.scene.geometry.size());
    for (const auto& primitive : request.scene.geometry) {
        const float pulseScale = 1.0f + request.audio.bass * 0.35f;
        const float rotation = request.timeSeconds * (0.4f + request.audio.mid * 1.4f) + primitive.rotation.y;
        const SceneVec3 animatedPosition{
            primitive.position.x * std::cos(rotation) - primitive.position.z * std::sin(rotation),
            primitive.position.y + std::sin(request.timeSeconds * 1.3f + primitive.position.x) * request.audio.bass * 0.45f,
            primitive.position.x * std::sin(rotation) + primitive.position.z * std::cos(rotation)
        };
        const auto projected = projectPoint(toCameraVec3(animatedPosition), request.camera,
                                            request.target.width, request.target.height);
        if (!projected.visible) {
            continue;
        }
        items.push_back(DrawItem{&primitive, projected});
    }

    std::sort(items.begin(), items.end(), [](const DrawItem& lhs, const DrawItem& rhs) {
        return lhs.projected.depth > rhs.projected.depth;
    });

    for (const auto& item : items) {
        const auto& primitive = *item.primitive;
        const float pulseScale = 1.0f + request.audio.bass * 0.35f;
        const float baseScale = std::max({primitive.scale.x, primitive.scale.y, primitive.scale.z});
        const float projectedSize = std::max(6.0f, item.projected.scale * baseScale * 0.45f * pulseScale);
        const float aspect = std::max(0.45f, primitive.scale.x / std::max(primitive.scale.y, 0.1f));
        Color color = materialColor(primitive, request.scene);
        color = lerp(color, clampColor({
            lighting.diffuse[0] * lighting.intensity,
            lighting.diffuse[1] * lighting.intensity,
            lighting.diffuse[2] * lighting.intensity,
        }), 0.28f + request.audio.energy * 0.2f);
        color = lerp(color, {1.0f, 1.0f, 1.0f}, request.audio.treble * 0.18f);

        switch (primitive.type) {
            case echoes::scene::GeometryType::Structure:
                drawGlow(pixels, request.target.width, request.target.height,
                         item.projected.x, item.projected.y,
                         projectedSize * aspect, projectedSize * 1.6f,
                         color, 0.82f, 2.3f);
                break;
            case echoes::scene::GeometryType::Terrain:
                drawGlow(pixels, request.target.width, request.target.height,
                         item.projected.x, item.projected.y + projectedSize * 0.35f,
                         projectedSize * 2.6f, projectedSize * 0.95f,
                         color, 0.6f, 1.8f);
                break;
            case echoes::scene::GeometryType::Emissive:
                drawRing(pixels, request.target.width, request.target.height,
                         item.projected.x, item.projected.y,
                         projectedSize * 1.7f, projectedSize * 0.7f,
                         0.12f, color, 0.95f);
                drawGlow(pixels, request.target.width, request.target.height,
                         item.projected.x, item.projected.y,
                         projectedSize * 0.75f, projectedSize * 0.75f,
                         color, 0.4f, 2.5f);
                break;
            case echoes::scene::GeometryType::Fluid:
                drawGlow(pixels, request.target.width, request.target.height,
                         item.projected.x, item.projected.y,
                         projectedSize * 2.2f, projectedSize * 1.4f,
                         color, 0.55f, 1.4f);
                break;
        }
    }
}

void paintEmitters(const SceneRenderRequest& request, std::vector<uint8_t>& pixels) {
    const Color accent = clampColor({
        request.scene.lighting.color.x,
        request.scene.lighting.color.y,
        request.scene.lighting.color.z
    });

    const int perEmitterParticles = std::clamp(8 + static_cast<int>(std::round(request.audio.energy * 18.0f + request.audio.bass * 12.0f)), 8, 42);

    for (std::size_t emitterIndex = 0; emitterIndex < request.scene.emitters.size(); ++emitterIndex) {
        const auto& emitter = request.scene.emitters[emitterIndex];
        for (int i = 0; i < perEmitterParticles; ++i) {
            const float local = request.timeSeconds * (0.7f + 0.08f * static_cast<float>(i + 1));
            const float orbit = local + static_cast<float>(emitterIndex) * 0.6f + static_cast<float>(i) * 0.9f;
            const SceneVec3 point{
                emitter.origin.x + std::cos(orbit) * emitter.spread * (0.6f + 0.1f * i) * (1.0f + request.audio.bass * 0.8f),
                emitter.origin.y + std::sin(orbit * 0.7f) * emitter.spread * (0.5f + request.audio.mid * 0.4f),
                emitter.origin.z + std::sin(orbit) * emitter.spread * (0.8f + 0.05f * i),
            };

            const auto projected = projectPoint(toCameraVec3(point), request.camera,
                                                request.target.width, request.target.height);
            if (!projected.visible) {
                continue;
            }

            const Color particleColor = clampColor({
                std::max(emitter.color.x, accent.r * 0.4f),
                std::max(emitter.color.y, accent.g * 0.4f),
                std::max(emitter.color.z, accent.b * 0.4f),
            });

            const float pulse = 0.5f + 0.5f * std::sin(orbit * 1.7f + request.timeSeconds * (2.0f + request.audio.tempo / 120.0f));
            const float size = std::max(3.0f, projected.scale * (0.2f + emitter.lifetime * 0.12f) * (0.7f + pulse + request.audio.energy * 0.5f));
            drawGlow(pixels, request.target.width, request.target.height,
                     projected.x, projected.y, size, size,
                     particleColor, 0.45f + pulse * 0.25f + request.audio.energy * 0.15f, 2.2f);
        }
    }
}

void applyPostProcessing(const SceneRenderRequest& request, std::vector<uint8_t>& pixels) {
    const uint32_t width = request.target.width;
    const uint32_t height = request.target.height;
    if (width == 0 || height == 0 || pixels.empty()) {
        return;
    }

    const float energy = std::clamp(request.audio.energy, 0.0f, 1.0f);
    const float treble = std::clamp(request.audio.treble, 0.0f, 1.0f);
    const float bass = std::clamp(request.audio.bass, 0.0f, 1.0f);
    const Color grade = clampColor({
        request.scene.lighting.color.x * 0.18f,
        request.scene.lighting.color.y * 0.18f,
        request.scene.lighting.color.z * 0.18f
    });

    const auto source = pixels;
    for (uint32_t y = 0; y < height; ++y) {
        for (uint32_t x = 0; x < width; ++x) {
            const float u = (static_cast<float>(x) + 0.5f) / static_cast<float>(width);
            const float v = (static_cast<float>(y) + 0.5f) / static_cast<float>(height);
            const float dx = u * 2.0f - 1.0f;
            const float dy = v * 2.0f - 1.0f;
            const float vignette = 1.0f - smoothStep(0.55f, 1.45f, std::sqrt(dx * dx + dy * dy));

            const std::size_t index = (static_cast<std::size_t>(y) * width + x) * 4;
            Color color{
                static_cast<float>(source[index + 0]) / 255.0f,
                static_cast<float>(source[index + 1]) / 255.0f,
                static_cast<float>(source[index + 2]) / 255.0f
            };

            const int shift = std::clamp(static_cast<int>(std::round((treble + bass) * 3.0f)), 0, 4);
            if (shift > 0) {
                const int rx = std::min<int>(static_cast<int>(width) - 1, static_cast<int>(x) + shift);
                const int bx = std::max<int>(0, static_cast<int>(x) - shift);
                const std::size_t rIndex = (static_cast<std::size_t>(y) * width + static_cast<std::size_t>(rx)) * 4;
                const std::size_t bIndex = (static_cast<std::size_t>(y) * width + static_cast<std::size_t>(bx)) * 4;
                color.r = static_cast<float>(source[rIndex + 0]) / 255.0f;
                color.b = static_cast<float>(source[bIndex + 2]) / 255.0f;
            }

            const float luminance = color.r * 0.2126f + color.g * 0.7152f + color.b * 0.0722f;
            const float bloom = std::max(0.0f, luminance - 0.58f) * (0.35f + energy * 0.55f);
            color = color + grade * (0.22f + energy * 0.4f);
            color = color + Color{1.0f, 0.95f, 0.90f} * bloom;
            color = color * (0.8f + vignette * 0.25f + treble * 0.06f);
            color = clampColor(color);

            pixels[index + 0] = static_cast<uint8_t>(color.r * 255.0f);
            pixels[index + 1] = static_cast<uint8_t>(color.g * 255.0f);
            pixels[index + 2] = static_cast<uint8_t>(color.b * 255.0f);
        }
    }
}

void compileSceneShaders(ShaderManager& manager, const SceneRenderRequest& request) {
    const std::string vertexName = request.sceneName + "_vertex";
    const std::string fragmentName = request.sceneName + "_fragment";

    if (!manager.hasShader(vertexName)) {
        manager.addShader({
            vertexName,
            ShaderStage::Vertex,
            "scene vertex transform",
            {request.sceneName, "GEOMETRY_" + std::to_string(request.scene.geometry.size())}
        });
    }
    if (!manager.hasShader(fragmentName)) {
        manager.addShader({
            fragmentName,
            ShaderStage::Fragment,
            "scene fragment shading",
            {request.sceneName, "EMITTERS_" + std::to_string(request.scene.emitters.size())}
        });
    }

    manager.compile(vertexName);
    manager.compile(fragmentName);
}

} // namespace

JobQueue::JobQueue() = default;

JobQueue::~JobQueue() {
    stop();
}

void JobQueue::start(std::size_t threads) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (running_) {
            return;
        }
        running_ = true;
    }

    for (std::size_t i = 0; i < threads; ++i) {
        workers_.emplace_back([this]() { worker(); });
    }
}

void JobQueue::stop() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        running_ = false;
    }
    cv_.notify_all();
    for (auto& worker : workers_) {
        if (worker.joinable()) {
            worker.join();
        }
    }
    workers_.clear();
}

void JobQueue::enqueue(std::function<void()> job) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        jobs_.push(std::move(job));
    }
    cv_.notify_one();
}

void JobQueue::worker() {
    while (true) {
        std::function<void()> job;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            cv_.wait(lock, [this]() { return !jobs_.empty() || !running_; });
            if (!running_ && jobs_.empty()) {
                return;
            }
            job = std::move(jobs_.front());
            jobs_.pop();
        }
        if (job) {
            job();
        }
    }
}

RenderPipeline::RenderPipeline(Backend backend)
    : device_(backend)
    , particleSystem_(2048) {
}

RenderPipeline::~RenderPipeline() {
    shutdown();
}

bool RenderPipeline::initialize() {
    if (!device_.initialize()) {
        return false;
    }

    jobQueue_.start(2);
    lighting_.registerPreset("nocturne", LightingPreset{{0.02f, 0.02f, 0.05f}, {0.3f, 0.2f, 0.7f}, {0.6f, 0.5f, 1.f}, 1.2f});
    lighting_.registerPreset("cyberpunk", LightingPreset{{0.05f, 0.03f, 0.12f}, {0.4f, 0.0f, 0.9f}, {0.8f, 0.7f, 1.f}, 1.4f});
    lighting_.registerPreset("inferno", LightingPreset{{0.12f, 0.05f, 0.02f}, {0.9f, 0.36f, 0.10f}, {1.0f, 0.74f, 0.25f}, 1.8f});
    lighting_.registerPreset("nebula", LightingPreset{{0.02f, 0.08f, 0.10f}, {0.20f, 0.90f, 0.75f}, {0.55f, 0.95f, 1.0f}, 1.6f});
    lighting_.applyPreset("nocturne");
    initialized_.store(true, std::memory_order_release);
    return true;
}

void RenderPipeline::shutdown() {
    if (!initialized_.load(std::memory_order_acquire)) {
        return;
    }
    jobQueue_.stop();
    device_.shutdown();
    initialized_.store(false, std::memory_order_release);
}

void RenderPipeline::submitScene(const SceneRenderRequest& request) {
    if (!initialized_.load(std::memory_order_acquire)) {
        return;
    }
    RenderPass pass;
    pass.name = "Scene:" + request.sceneName;
    pass.request = request;
    {
        std::lock_guard<std::mutex> lock(pendingMutex_);
        pendingPasses_.push_back(pass);
    }
}

void RenderPipeline::tick() {
    std::vector<RenderPass> queue;
    {
        std::lock_guard<std::mutex> lock(pendingMutex_);
        queue.swap(pendingPasses_);
    }
    for (const auto& pass : queue) {
        jobQueue_.enqueue([this, pass]() { executePass(pass); });
    }
    particleSystem_.update(std::chrono::milliseconds(16));
}

const LightingEngine& RenderPipeline::lighting() const noexcept {
    return lighting_;
}

LightingEngine& RenderPipeline::lighting() noexcept {
    return lighting_;
}

ShaderManager& RenderPipeline::shaders() {
    return shaderManager_;
}

ParticleSystem& RenderPipeline::particles() {
    return particleSystem_;
}

std::string RenderPipeline::describe() const {
    return device_.describe();
}

bool RenderPipeline::renderToPixels(const SceneRenderRequest& request, std::vector<uint8_t>& outPixels) {
    if (!initialized_.load(std::memory_order_acquire) ||
        request.target.width == 0 || request.target.height == 0) {
        return false;
    }

    if (!device_.beginFrame()) {
        return false;
    }

    const auto preset = presetForScene(request.scene);
    lighting_.registerPreset(request.sceneName, preset);
    lighting_.applyPreset(request.sceneName);
    compileSceneShaders(shaderManager_, request);

    paintBackground(request, outPixels);
    paintGeometry(request, lighting_.current(), outPixels);
    paintEmitters(request, outPixels);
    applyPostProcessing(request, outPixels);

    particleSystem_.update(std::chrono::milliseconds(16));
    device_.endFrame();
    return true;
}

void RenderPipeline::executePass(const RenderPass& pass) {
    std::vector<uint8_t> scratch;
    const bool ok = renderToPixels(pass.request, scratch);
    std::ostringstream message;
    message << "[RenderPipeline] Executing pass " << pass.name
            << " -> " << pass.request.target.width << "x" << pass.request.target.height
            << " drawCalls=" << (1 + pass.request.scene.geometry.size() + pass.request.scene.emitters.size())
            << " shaders=(" << shaderManager_.status(pass.request.sceneName + "_vertex")
            << ", " << shaderManager_.status(pass.request.sceneName + "_fragment") << ")";
    if (!ok) {
        message << " [render failed]";
    }
    std::cout << message.str() << "\n";
}

} // namespace echoes::render
