#pragma once

#include "EchoesEngine/api/Http.h"

#include <functional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace echoes::api {

class Router {
public:
    using Params = std::unordered_map<std::string, std::string>;
    using Handler = std::function<HttpResponse(const HttpRequest&, const Params&)>;

    void addRoute(HttpMethod method, std::string pattern, Handler handler);
    HttpResponse dispatch(const HttpRequest& request) const;

private:
    struct Route {
        HttpMethod method;
        std::string pattern;
        Handler handler;
    };

    static std::vector<std::string> splitPath(std::string_view path);
    static bool matchPath(std::string_view pattern,
                          std::string_view actual,
                          Params& outParams);

    std::vector<Route> routes_;
};

} // namespace echoes::api
