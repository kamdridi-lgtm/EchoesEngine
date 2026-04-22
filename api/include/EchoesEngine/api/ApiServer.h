#pragma once

#include "EchoesEngine/api/GenerateModels.h"
#include "EchoesEngine/api/Http.h"
#include "EchoesEngine/api/JobRegistry.h"

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <string_view>

namespace echoes::api {

struct GenerateJobRequest {
    std::string jobId;
    std::string prompt;
    std::string audioPath;
    std::string style;
    double requestedDurationSeconds = 0.0;
    uint32_t requestedWidth = 0;
    uint32_t requestedHeight = 0;
};

class ApiServer {
public:
    using JobSubmitter = std::function<void(GenerateJobRequest)>;

    explicit ApiServer(JobRegistry& registry, JobSubmitter submitter = {});
    ~ApiServer();
    ApiServer(const ApiServer&) = delete;
    ApiServer& operator=(const ApiServer&) = delete;

    HttpResponse handleRequest(const HttpRequest& request) const;

    bool start(uint16_t port = 8080);
    void stop();
    bool isRunning() const noexcept;
    uint16_t port() const noexcept;
    void setJobSubmitter(JobSubmitter submitter);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace echoes::api
