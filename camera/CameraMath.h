#pragma once

#include <algorithm>
#include <cmath>

namespace echoes::camera {

struct Vec3 {
    float x = 0.0f;
    float y = 0.0f;
    float z = 0.0f;
};

inline Vec3 operator+(const Vec3& a, const Vec3& b) {
    return {a.x + b.x, a.y + b.y, a.z + b.z};
}

inline Vec3 operator-(const Vec3& a, const Vec3& b) {
    return {a.x - b.x, a.y - b.y, a.z - b.z};
}

inline Vec3 operator*(const Vec3& v, float s) {
    return {v.x * s, v.y * s, v.z * s};
}

inline Vec3 operator*(float s, const Vec3& v) {
    return v * s;
}

inline Vec3 operator/(const Vec3& v, float s) {
    const float inv = (s != 0.0f) ? (1.0f / s) : 0.0f;
    return {v.x * inv, v.y * inv, v.z * inv};
}

inline float dot(const Vec3& a, const Vec3& b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

inline Vec3 cross(const Vec3& a, const Vec3& b) {
    return {
        a.y * b.z - a.z * b.y,
        a.z * b.x - a.x * b.z,
        a.x * b.y - a.y * b.x,
    };
}

inline float length(const Vec3& v) {
    return std::sqrt(dot(v, v));
}

inline Vec3 normalize(const Vec3& v) {
    const float len = length(v);
    return (len > 0.0f) ? (v / len) : Vec3{};
}

inline float clampf(float value, float lower, float upper) {
    return std::max(lower, std::min(upper, value));
}

inline float lerp(float a, float b, float t) {
    return a + (b - a) * clampf(t, 0.0f, 1.0f);
}

inline Vec3 lerp(const Vec3& a, const Vec3& b, float t) {
    return a * (1.0f - t) + b * t;
}

struct CameraTransform {
    Vec3 position;
    Vec3 lookAt;
    Vec3 up{0.0f, 1.0f, 0.0f};
    float fov{60.0f};
};

} // namespace echoes::camera
