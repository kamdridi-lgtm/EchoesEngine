#pragma once

#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace echoes::api {

enum class HttpMethod {
    Get,
    Post,
    Options,
    Unknown
};

enum class HttpStatus {
    Ok = 200,
    BadRequest = 400,
    NotFound = 404,
    MethodNotAllowed = 405,
    InternalServerError = 500
};

inline std::string statusMessage(HttpStatus status) {
    switch (status) {
        case HttpStatus::Ok:
            return "OK";
        case HttpStatus::BadRequest:
            return "Bad Request";
        case HttpStatus::NotFound:
            return "Not Found";
        case HttpStatus::MethodNotAllowed:
            return "Method Not Allowed";
        case HttpStatus::InternalServerError:
            return "Internal Server Error";
    }
    return "Unknown";
}

struct HttpRequest {
    HttpMethod method = HttpMethod::Unknown;
    std::string path;
    std::string body;
    std::unordered_map<std::string, std::string> headers;
};

struct HttpResponse {
    HttpStatus status = HttpStatus::Ok;
    std::string body;
    std::vector<std::pair<std::string, std::string>> headers;
};

HttpMethod methodFromString(std::string_view method);

} // namespace echoes::api
