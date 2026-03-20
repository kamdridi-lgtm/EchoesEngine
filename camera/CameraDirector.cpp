#include "CameraDirector.h"

#include <cmath>

namespace echoes::camera {

CameraDirector::CameraDirector(CameraDirectorConfig config)
    : config_(config)
    , basePosition_{0.0f, 0.0f, 0.0f}
    , targetPosition_{0.0f, 0.0f, 0.0f}
    , currentLookAt_{0.0f, 0.0f, 0.0f}
    , currentPosition_{0.0f, 0.0f, 0.0f}
    , orbitAngle_(0.0f)
    , driftPhase_(0.0f)
    , fov_(config.baseFov)
    , beatPulse_(0.0f)
    , shakePhase_(0.0f) {}

void CameraDirector::setBasePosition(const Vec3& pos) {
    basePosition_ = pos;
}

void CameraDirector::setTargetPosition(const Vec3& target) {
    targetPosition_ = target;
}

void CameraDirector::setDriftDirection(const Vec3& dir) {
    const Vec3 normalized = normalize(dir);
    config_.driftDirection = (length(normalized) > 0.0f) ? normalized : Vec3{0.02f, 0.01f, 0.0f};
}

void CameraDirector::setOrbitRadius(float radius) {
    config_.orbitRadius = clampf(radius, config_.minOrbitRadius, config_.maxOrbitRadius);
}

void CameraDirector::setOrbitSpeed(float speed) {
    config_.orbitSpeed = speed;
}

void CameraDirector::reset() {
    orbitAngle_ = 0.0f;
    driftPhase_ = 0.0f;
    shakePhase_ = 0.0f;
    beatPulse_ = 0.0f;
    currentPosition_ = basePosition_;
    currentLookAt_ = targetPosition_;
    fov_ = config_.baseFov;
}

void CameraDirector::update(float deltaSeconds, AudioFeatures const& features) {
    if (deltaSeconds <= 0.0f) {
        return;
    }

    const float speed = config_.orbitSpeed + features.mid * config_.orbitSpeedScale;
    orbitAngle_ += deltaSeconds * speed;
    constexpr float twoPi = 6.283185307179586f;
    orbitAngle_ = std::fmod(orbitAngle_, twoPi);

    const float radius = clampf(config_.orbitRadius + features.bass * 2.0f,
                                config_.minOrbitRadius,
                                config_.maxOrbitRadius);

    const Vec3 orbitOffset{
        std::cos(orbitAngle_) * radius,
        config_.orbitHeight + features.mid * 0.35f,
        std::sin(orbitAngle_) * radius,
    };

    driftPhase_ += deltaSeconds * config_.driftSpeed;
    const Vec3 driftDir = normalize(config_.driftDirection);
    const Vec3 drift = driftDir * (std::sin(driftPhase_) * config_.driftMagnitude);

    const Vec3 center = targetPosition_ + basePosition_;
    const Vec3 viewTarget = lerp(currentLookAt_, targetPosition_, clampf(deltaSeconds * config_.trackingSmooth, 0.0f, 1.0f));
    currentLookAt_ = viewTarget;

    const Vec3 desiredBase = center + orbitOffset + drift;
    Vec3 viewDir = desiredBase - currentPosition_;
    if (length(viewDir) == 0.0f) {
        viewDir = Vec3{0.0f, 0.0f, -1.0f};
    } else {
        viewDir = normalize(viewDir);
    }

    if (features.beat) {
        beatPulse_ = 1.0f;
    } else {
        beatPulse_ = clampf(beatPulse_ - deltaSeconds * config_.beatDecay, 0.0f, 1.0f);
    }

    const Vec3 beatMovement = viewDir * (beatPulse_ * config_.beatShakeBoost * 2.0f);

    shakePhase_ += deltaSeconds * (features.tempo / 60.0f + 0.5f);
    const Vec3 shake{
        std::sin(shakePhase_) * (config_.shakeBase + features.energy * config_.beatShakeBoost),
        std::cos(shakePhase_ * 1.3f) * (config_.shakeBase + features.energy * config_.beatShakeBoost),
        std::sin(shakePhase_ * 0.6f) * (config_.shakeTempoInfluence * features.tempo),
    };

    const Vec3 trackingPole = targetPosition_ - currentPosition_;
    const Vec3 trackingShift = normalize(trackingPole) * (clampf(features.treble, 0.0f, 1.0f) * 0.4f);

    const Vec3 desired = desiredBase + beatMovement + shake + trackingShift;
    currentPosition_ = lerp(currentPosition_, desired, clampf(deltaSeconds * 4.0f, 0.0f, 1.0f));

    const float energyInfluence = clampf(features.energy, 0.0f, 1.0f);
    const float zoomTarget = config_.baseFov - energyInfluence * config_.energyZoomScale - beatPulse_ * config_.beatZoomBoost;
    const float clampedZoom = clampf(zoomTarget, config_.minFov, config_.maxFov);
    fov_ = lerp(fov_, clampedZoom, clampf(deltaSeconds * 3.5f, 0.0f, 1.0f));
}

CameraTransform CameraDirector::getTransform() const {
    return {currentPosition_, currentLookAt_, {0.0f, 1.0f, 0.0f}, fov_};
}

} // namespace echoes::camera
