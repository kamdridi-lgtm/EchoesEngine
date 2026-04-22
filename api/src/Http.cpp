#include "EchoesEngine/api/Http.h"

#include <algorithm>
#include <cctype>
#include <string_view>

namespace echoes::api {

HttpMethod methodFromString(std::string_view method) {
    std::string normalized(method.data(), method.size());
    std::transform(normalized.begin(), normalized.end(), normalized.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });

    if (normalized == "get") {
        return HttpMethod::Get;
    }
    if (normalized == "post") {
        return HttpMethod::Post;
    }
    if (normalized == "options") {
        return HttpMethod::Options;
    }
    return HttpMethod::Unknown;
}

} // namespace echoes::api
