#pragma once

#include "CameraMath.h"

#include <cmath>

namespace echoes::camera {

struct AudioFeatures {
    float bass = 0.0f;
    float mid = 0.0f;
    float treble = 0.0f;
    float energy = 0.0f;
    bool beat = false;
    float tempo = 120.0f;
};

struct CameraDirectorConfig {
    float orbitRadius = 9.0f;
    float minOrbitRadius = 4.0f;
    float maxOrbitRadius = 16.0f;
    float orbitSpeed = 0.5f;
    float orbitSpeedScale = 0.6f;
    float orbitHeight = 2.0f;
    float orbitVerticalBias = 0.4f;
    Vec3 driftDirection = {0.02f, 0.01f, 0.0f};
    float driftSpeed = 0.2f;
    float driftMagnitude = 0.12f;
    float trackingSmooth = 4.0f;
    float baseFov = 60.0f;
    float minFov = 35.0f;
    float maxFov = 90.0f;
    float energyZoomScale = 14.0f;
    float beatZoomBoost = 6.0f;
    float beatDecay = 3.5f;
    float shakeBase = 0.03f;
    float shakeTempoInfluence = 0.01f;
    float beatShakeBoost = 0.12f;
};

class CameraDirector {
public:
    explicit CameraDirector(CameraDirectorConfig config = {});
    void setBasePosition(const Vec3& pos);
    void setTargetPosition(const Vec3& target);
    void setDriftDirection(const Vec3& dir);
    void setOrbitRadius(float radius);
    void setOrbitSpeed(float speed);
    void reset();

    void update(float deltaSeconds, AudioFeatures const& features);
    [[nodiscard]] CameraTransform getTransform() const;

private:
    CameraDirectorConfig config_;
    Vec3 basePosition_;
    Vec3 targetPosition_;
    Vec3 currentLookAt_;
    Vec3 currentPosition_;
    float orbitAngle_ = 0.0f;
    float driftPhase_ = 0.0f;
    float fov_ = 60.0f;
    float beatPulse_ = 0.0f;
    float shakePhase_ = 0.0f;
};

} // namespace echoes::camera
