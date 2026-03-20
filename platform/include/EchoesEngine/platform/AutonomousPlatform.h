#pragma once

#include "EchoesEngine/Core/engine.h"
#include "EchoesEngine/api/ApiServer.h"
#include "EchoesEngine/api/JobRegistry.h"

#include <cstddef>
#include <filesystem>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace echoes::platform {

struct RuntimeConfig {
    std::size_t minConcurrentJobs = 1;
    std::size_t maxConcurrentJobs = 8;
    std::size_t maxRetries = 1;
    double agentTickRate = 8.0;
    std::filesystem::path runtimeDirectory = std::filesystem::path("runtime");
    std::filesystem::path renderOutputDirectory = std::filesystem::path("renders");
    std::string apiEndpoint = "http://127.0.0.1:8080";
    bool autoJobEnabled = true;
    std::size_t autoJobIntervalSeconds = 10;
    std::filesystem::path autoJobAudioFile = std::filesystem::path("jobs/synthetic-test-0p3s.wav");
    std::string autoJobStyle = "nebula";
    std::string autoJobCameraMotion = "orbital sweep";
    double autoJobDurationSeconds = 37.5;
};

struct RuntimeSnapshot {
    std::size_t queuedJobs = 0;
    std::size_t activeJobs = 0;
    std::size_t completedJobs = 0;
    std::size_t failedJobs = 0;
    std::size_t retryBacklog = 0;
    std::size_t maxConcurrentJobs = 0;
    std::vector<std::string> activeAgents;
};

class MasterAgent {
public:
    using JobProcessor = std::function<bool(const echoes::api::GenerateJobRequest&)>;

    MasterAgent(echoes::api::JobRegistry& registry,
                JobProcessor processor,
                RuntimeConfig config = {});
    ~MasterAgent();

    MasterAgent(const MasterAgent&) = delete;
    MasterAgent& operator=(const MasterAgent&) = delete;

    bool start();
    void stop();

    void submit(echoes::api::GenerateJobRequest request);
    RuntimeSnapshot snapshot() const;
    RuntimeConfig config() const;
    bool spawnSubagent(std::string agentName);
    std::vector<std::string> activeAgents() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

RuntimeConfig loadRuntimeConfigFromEnvironment(const std::filesystem::path& runtimeRoot = {});

} // namespace echoes::platform
