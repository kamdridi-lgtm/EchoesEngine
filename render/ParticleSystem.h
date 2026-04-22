#pragma once

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <vector>

namespace echoes::render {

struct Particle {
    std::array<float, 3> position{0.f, 0.f, 0.f};
    std::array<float, 3> velocity{0.f, 0.f, 0.f};
    float life        = 1.0f;
    float size        = 1.0f;
    float energy      = 1.0f;
};

struct ParticleEmitter {
    std::array<float, 3> origin{0.f, 0.f, 0.f};
    std::array<float, 3> direction{0.f, 1.f, 0.f};
    float spawnRate    = 60.f; // particles per second
    float velocity      = 5.f;
    float lifeSpan      = 2.f;
    std::function<void(Particle&)> customize;
};

class ParticleSystem {
public:
    explicit ParticleSystem(std::size_t maxParticles = 4096);

    void addEmitter(const ParticleEmitter& emitter);
    void update(std::chrono::duration<float> deltaTime);
    const std::vector<Particle>& particles() const noexcept;

private:
    void emit(const ParticleEmitter& emitter, float deltaSeconds);

    std::vector<Particle> particles_;
    std::vector<ParticleEmitter> emitters_;
    float emitAccumulator_ = 0.f;
};

} // namespace echoes::render
