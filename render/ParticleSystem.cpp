#include "ParticleSystem.h"

#include <algorithm>

namespace echoes::render {

ParticleSystem::ParticleSystem(std::size_t maxParticles)
    : particles_(maxParticles) {
}

void ParticleSystem::addEmitter(const ParticleEmitter& emitter) {
    emitters_.push_back(emitter);
}

void ParticleSystem::update(std::chrono::duration<float> deltaTime) {
    const float deltaSeconds = deltaTime.count();
    for (auto& particle : particles_) {
        if (particle.life <= 0.f) {
            continue;
        }
        particle.position[0] += particle.velocity[0] * deltaSeconds;
        particle.position[1] += particle.velocity[1] * deltaSeconds;
        particle.position[2] += particle.velocity[2] * deltaSeconds;
        particle.life -= deltaSeconds;
        particle.energy = std::max(0.f, particle.energy - deltaSeconds * 0.5f);
    }

    for (const auto& emitter : emitters_) {
        emit(emitter, deltaSeconds);
    }
}

const std::vector<Particle>& ParticleSystem::particles() const noexcept {
    return particles_;
}

void ParticleSystem::emit(const ParticleEmitter& emitter, float deltaSeconds) {
    emitAccumulator_ += emitter.spawnRate * deltaSeconds;
    while (emitAccumulator_ >= 1.0f) {
        emitAccumulator_ -= 1.0f;
        auto it = std::find_if(particles_.begin(), particles_.end(), [](const Particle& p) {
            return p.life <= 0.f;
        });
        if (it == particles_.end()) {
            break;
        }
        it->position = emitter.origin;
        it->life     = emitter.lifeSpan;
        it->size     = 1.0f;
        it->energy   = 1.0f;
        const auto dir = emitter.direction;
        it->velocity[0] = dir[0] * emitter.velocity;
        it->velocity[1] = dir[1] * emitter.velocity;
        it->velocity[2] = dir[2] * emitter.velocity;
        if (emitter.customize) {
            emitter.customize(*it);
        }
    }
}

} // namespace echoes::render
