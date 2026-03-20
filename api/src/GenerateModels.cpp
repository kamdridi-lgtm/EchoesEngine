#include "EchoesEngine/api/GenerateModels.h"

#include <charconv>
#include <optional>
#include <sstream>
#include <utility>

namespace echoes::api {

namespace {

std::optional<std::string> extractString(std::string_view payload, std::string_view key) {
    const std::string needle = std::string{"\""} + std::string(key) + "\"";
    const auto keyPos = payload.find(needle);
    if (keyPos == std::string_view::npos) {
        return std::nullopt;
    }

    const auto colonPos = payload.find(':', keyPos + needle.size());
    if (colonPos == std::string_view::npos) {
        return std::nullopt;
    }

    const auto quoteStart = payload.find('"', colonPos);
    if (quoteStart == std::string_view::npos) {
        return std::nullopt;
    }

    const auto quoteEnd = payload.find('"', quoteStart + 1);
    if (quoteEnd == std::string_view::npos) {
        return std::nullopt;
    }

    return std::string(payload.substr(quoteStart + 1, quoteEnd - quoteStart - 1));
}

std::optional<double> extractNumber(std::string_view payload, std::string_view key) {
    const std::string needle = std::string{"\""} + std::string(key) + "\"";
    const auto keyPos = payload.find(needle);
    if (keyPos == std::string_view::npos) {
        return std::nullopt;
    }

    const auto colonPos = payload.find(':', keyPos + needle.size());
    if (colonPos == std::string_view::npos) {
        return std::nullopt;
    }

    const auto valueStart = payload.find_first_not_of(" \t\r\n", colonPos + 1);
    if (valueStart == std::string_view::npos) {
        return std::nullopt;
    }

    const auto valueEnd = payload.find_first_of(",}", valueStart);
    const auto raw = payload.substr(valueStart, valueEnd == std::string_view::npos ? payload.size() - valueStart : valueEnd - valueStart);

    try {
        return std::stod(std::string(raw));
    } catch (...) {
        return std::nullopt;
    }
}

std::optional<std::pair<uint32_t, uint32_t>> parseResolution(std::string_view value) {
    const auto separator = value.find('x');
    if (separator == std::string_view::npos) {
        return std::nullopt;
    }

    uint32_t width = 0;
    uint32_t height = 0;
    const auto widthPart = value.substr(0, separator);
    const auto heightPart = value.substr(separator + 1);

    const auto widthResult = std::from_chars(widthPart.data(), widthPart.data() + widthPart.size(), width);
    const auto heightResult = std::from_chars(heightPart.data(), heightPart.data() + heightPart.size(), height);
    if (widthResult.ec != std::errc{} || heightResult.ec != std::errc{}) {
        return std::nullopt;
    }

    return std::make_pair(width, height);
}

} // namespace

namespace json {

std::string escape(std::string_view value) {
    std::string result;
    result.reserve(value.size() + 16);
    for (char c : value) {
        switch (c) {
            case '\\':
                result += "\\\\";
                break;
            case '"':
                result += "\\\"";
                break;
            case '\n':
                result += "\\n";
                break;
            case '\r':
                result += "\\r";
                break;
            default:
                result.push_back(c);
                break;
        }
    }
    return result;
}

} // namespace json

std::optional<GenerateRequest> GenerateRequest::fromJson(std::string_view payload) {
    auto prompt = extractString(payload, "prompt");
    auto audio = extractString(payload, "audio");
    auto style = extractString(payload, "style");
    auto durationSeconds = extractNumber(payload, "duration");
    auto resolution = extractString(payload, "resolution");

    if (!style) {
        return std::nullopt;
    }

    GenerateRequest request;
    request.prompt = prompt.value_or("");
    request.audio = audio.value_or("");
    request.style = *style;
    request.durationSeconds = durationSeconds.value_or(0.0);

    if (resolution) {
        if (const auto parsedResolution = parseResolution(*resolution)) {
            request.width = parsedResolution->first;
            request.height = parsedResolution->second;
        }
    }

    return request;
}

std::string GenerateResponse::toJson() const {
    std::ostringstream oss;
    oss << "{"
        << "\"jobId\":\"" << json::escape(jobId) << "\""
        << "}";
    return oss.str();
}

std::string StatusResponse::toJson() const {
    std::ostringstream oss;
    oss << "{"
        << "\"jobId\":\"" << json::escape(jobId) << "\""
        << ",\"status\":\"" << json::escape(status) << "\""
        << ",\"stage\":\"" << json::escape(stage) << "\""
        << ",\"progress\":" << progress
        << ",\"videoPath\":\"" << json::escape(videoPath) << "\""
        << ",\"error\":\"" << json::escape(error) << "\""
        << "}";
    return oss.str();
}

std::string VideoResponse::toJson() const {
    std::ostringstream oss;
    oss << "{"
        << "\"jobId\":\"" << json::escape(jobId) << "\""
        << ",\"videoPath\":\"" << json::escape(videoPath) << "\""
        << ",\"status\":\"" << json::escape(status) << "\""
        << "}";
    return oss.str();
}

} // namespace echoes::api
