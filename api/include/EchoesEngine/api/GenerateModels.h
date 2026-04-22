#pragma once

#include <optional>
#include <cstdint>
#include <string>
#include <string_view>

namespace echoes::api {

namespace json {
std::string escape(std::string_view value);
} // namespace json

struct GenerateRequest {
    std::string prompt;
    std::string audio;
    std::string style;
    double durationSeconds = 0.0;
    uint32_t width = 0;
    uint32_t height = 0;

    static std::optional<GenerateRequest> fromJson(std::string_view payload);
};

struct GenerateResponse {
    std::string jobId;

    std::string toJson() const;
};

struct StatusResponse {
    std::string jobId;
    std::string status;
    std::string stage;
    double progress = 0.0;
    std::string videoPath;
    std::string error;

    std::string toJson() const;
};

struct VideoResponse {
    std::string jobId;
    std::string videoPath;
    std::string status;

    std::string toJson() const;
};

} // namespace echoes::api
