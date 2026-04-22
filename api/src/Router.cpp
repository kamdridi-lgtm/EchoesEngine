#include "EchoesEngine/api/Router.h"

#include <sstream>

namespace echoes::api {

void Router::addRoute(HttpMethod method, std::string pattern, Handler handler) {
    routes_.push_back({method, std::move(pattern), std::move(handler)});
}

HttpResponse Router::dispatch(const HttpRequest& request) const {
    for (const auto& route : routes_) {
        if (route.method != request.method) {
            continue;
        }

        Params params;
        if (!matchPath(route.pattern, request.path, params)) {
            continue;
        }

        return route.handler(request, params);
    }

    return HttpResponse{
        HttpStatus::NotFound,
        "{\"error\":\"Endpoint not found\"}"
    };
}

std::vector<std::string> Router::splitPath(std::string_view path) {
    std::vector<std::string> segments;
    std::string token;
    std::istringstream stream{std::string(path)};
    while (std::getline(stream, token, '/')) {
        if (!token.empty()) {
            segments.push_back(token);
        }
    }
    return segments;
}

bool Router::matchPath(std::string_view pattern,
                       std::string_view actual,
                       Params& outParams) {
    const auto expected = splitPath(pattern);
    const auto received = splitPath(actual);
    if (expected.size() != received.size()) {
        return false;
    }

    for (size_t i = 0; i < expected.size(); ++i) {
        const auto& a = expected[i];
        const auto& b = received[i];
        if (!a.empty() && a.front() == '{' && a.back() == '}') {
            outParams[a.substr(1, a.size() - 2)] = b;
            continue;
        }

        if (a != b) {
            return false;
        }
    }

    return true;
}

} // namespace echoes::api
