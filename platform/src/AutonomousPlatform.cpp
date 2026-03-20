#include "EchoesEngine/platform/AutonomousPlatform.h"

#include "EchoesEngine/Core/module.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <cstdint>
#include <ctime>
#include <deque>
#include <fstream>
#include <mutex>
#include <optional>
#include <sstream>
#include <string_view>
#include <system_error>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace echoes::platform {

namespace {

using Clock = std::chrono::steady_clock;

constexpr std::size_t kHistoryLimit = 128;

std::optional<std::size_t> readEnvSizeT(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || *value == '\0') {
        return std::nullopt;
    }

    try {
        return static_cast<std::size_t>(std::stoull(value));
    } catch (...) {
        return std::nullopt;
    }
}

std::optional<double> readEnvDouble(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || *value == '\0') {
        return std::nullopt;
    }

    try {
        return std::stod(value);
    } catch (...) {
        return std::nullopt;
    }
}

std::optional<std::string> readEnvString(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || *value == '\0') {
        return std::nullopt;
    }
    return std::string(value);
}

std::optional<bool> readEnvBool(const char* name) {
    const auto value = readEnvString(name);
    if (!value) {
        return std::nullopt;
    }

    std::string lowered = *value;
    std::transform(lowered.begin(), lowered.end(), lowered.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    if (lowered == "1" || lowered == "true" || lowered == "yes" || lowered == "on") {
        return true;
    }
    if (lowered == "0" || lowered == "false" || lowered == "no" || lowered == "off") {
        return false;
    }
    return std::nullopt;
}

std::string statusToString(api::JobStatus status) {
    switch (status) {
        case api::JobStatus::Pending:
            return "pending";
        case api::JobStatus::Processing:
            return "processing";
        case api::JobStatus::ANALYSIS:
            return "ANALYSIS";
        case api::JobStatus::SCENE_GENERATION:
            return "SCENE_GENERATION";
        case api::JobStatus::RENDERING:
            return "RENDERING";
        case api::JobStatus::ENCODING:
            return "ENCODING";
        case api::JobStatus::FINISHED:
            return "FINISHED";
        case api::JobStatus::Ready:
            return "ready";
        case api::JobStatus::Failed:
            return "failed";
    }
    return "unknown";
}

std::string jsonEscape(std::string_view value) {
    std::string result;
    result.reserve(value.size() + 16);
    for (char ch : value) {
        switch (ch) {
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
            case '\t':
                result += "\\t";
                break;
            default:
                result.push_back(ch);
                break;
        }
    }
    return result;
}

std::string trim(std::string value) {
    auto notSpace = [](unsigned char ch) { return !std::isspace(ch); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), notSpace));
    value.erase(std::find_if(value.rbegin(), value.rend(), notSpace).base(), value.end());
    return value;
}

std::string parseSubagentName(std::string command) {
    command = trim(std::move(command));
    constexpr std::string_view kPrefix = "/subagents spawn ";
    if (command.rfind(kPrefix, 0) == 0) {
        command.erase(0, kPrefix.size());
    }
    return trim(std::move(command));
}

void trimHistory(std::deque<std::string>& history) {
    while (history.size() > kHistoryLimit) {
        history.pop_front();
    }
}

void writeTextFile(const std::filesystem::path& path, const std::string& content) {
    if (!path.parent_path().empty()) {
        std::filesystem::create_directories(path.parent_path());
    }

    std::ofstream output(path, std::ios::binary);
    output << content;
}

void appendLogLine(const std::filesystem::path& path, std::string_view message) {
    if (!path.parent_path().empty()) {
        std::filesystem::create_directories(path.parent_path());
    }

    std::ofstream output(path, std::ios::app | std::ios::binary);
    const auto now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm localTime{};
#ifdef _WIN32
    localtime_s(&localTime, &now);
#else
    localtime_r(&now, &localTime);
#endif
    char buffer[32];
    std::strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M:%S", &localTime);
    output << "[" << buffer << "] " << message << "\n";
}

std::filesystem::path resolvePath(const std::filesystem::path& path) {
    if (path.is_absolute()) {
        return path;
    }
    return std::filesystem::current_path() / path;
}

std::string jsonArray(const std::vector<std::string>& values) {
    std::ostringstream json;
    json << "[";
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) {
            json << ", ";
        }
        json << "\"" << jsonEscape(values[index]) << "\"";
    }
    json << "]";
    return json.str();
}

template <typename Container>
std::vector<std::string> toVector(const Container& container) {
    return std::vector<std::string>(container.begin(), container.end());
}

struct SystemTelemetry {
    double cpuUsagePercent = 0.0;
    double ramUsagePercent = 0.0;
    std::uint64_t ramUsedMb = 0;
    std::uint64_t ramTotalMb = 0;
    double diskUsagePercent = 0.0;
    double diskFreeGb = 0.0;
    double diskTotalGb = 0.0;
    std::string gpuStatus = "telemetry unavailable";
};

SystemTelemetry collectSystemTelemetry(const std::filesystem::path& renderOutputDirectory) {
    SystemTelemetry telemetry;

#ifdef _WIN32
    MEMORYSTATUSEX memoryStatus{};
    memoryStatus.dwLength = sizeof(memoryStatus);
    if (GlobalMemoryStatusEx(&memoryStatus)) {
        telemetry.ramUsagePercent = static_cast<double>(memoryStatus.dwMemoryLoad);
        telemetry.ramTotalMb = static_cast<std::uint64_t>(memoryStatus.ullTotalPhys / (1024ull * 1024ull));
        telemetry.ramUsedMb =
            static_cast<std::uint64_t>((memoryStatus.ullTotalPhys - memoryStatus.ullAvailPhys) / (1024ull * 1024ull));
    }
#endif

    std::error_code ec;
    const auto absoluteOutput = std::filesystem::absolute(renderOutputDirectory, ec);
    const auto spaceInfo = std::filesystem::space(ec ? renderOutputDirectory : absoluteOutput, ec);
    if (!ec && spaceInfo.capacity > 0) {
        telemetry.diskTotalGb = static_cast<double>(spaceInfo.capacity) / (1024.0 * 1024.0 * 1024.0);
        telemetry.diskFreeGb = static_cast<double>(spaceInfo.available) / (1024.0 * 1024.0 * 1024.0);
        telemetry.diskUsagePercent =
            100.0 * static_cast<double>(spaceInfo.capacity - spaceInfo.available) / static_cast<double>(spaceInfo.capacity);
    }

    return telemetry;
}

struct ManagedJob {
    api::GenerateJobRequest request;
    std::size_t attempts = 0;
    Clock::time_point queuedAt = Clock::now();
    Clock::time_point lastFailureAt{};
};

struct SharedState {
    explicit SharedState(RuntimeConfig runtimeConfig)
        : config(std::move(runtimeConfig))
        , dynamicMaxConcurrentJobs(config.maxConcurrentJobs)
        , availableAgents{
              "AutoJobAgent",
              "SceneAgent",
              "AudioAgent",
              "RenderAgent",
              "EncodingAgent",
              "UploadAgent",
              "MonitoringAgent",
              "OptimizationAgent",
              "ScalingAgent"} {
        activeAgents.insert("OrchestratorAgent");
        commandHistory.push_back("/subagents spawn OrchestratorAgent");
    }

    void enqueue(api::GenerateJobRequest request) {
        std::lock_guard lock(mutex);
        const std::string jobId = request.jobId;
        trackedJobs[jobId] = ManagedJob{std::move(request), trackedJobs[jobId].attempts, Clock::now(), {}};
        pending.push_back(jobId);
    }

    bool tryDispatch(api::GenerateJobRequest& request) {
        std::lock_guard lock(mutex);
        if (pending.empty() || running.size() >= dynamicMaxConcurrentJobs) {
            return false;
        }

        const auto jobId = pending.front();
        pending.pop_front();
        auto it = trackedJobs.find(jobId);
        if (it == trackedJobs.end()) {
            return false;
        }

        running.insert(jobId);
        request = it->second.request;
        return true;
    }

    void markSucceeded(const std::string& jobId) {
        std::lock_guard lock(mutex);
        running.erase(jobId);
        trackedJobs.erase(jobId);
        finished.push_back(jobId);
        trimHistory(finished);
        ++completedJobs;
    }

    void markFailed(const std::string& jobId) {
        std::lock_guard lock(mutex);
        running.erase(jobId);
        auto it = trackedJobs.find(jobId);
        if (it != trackedJobs.end() && it->second.attempts < config.maxRetries) {
            ++it->second.attempts;
            it->second.lastFailureAt = Clock::now();
            retry.push_back(jobId);
            trimHistory(retry);
            return;
        }

        trackedJobs.erase(jobId);
        failed.push_back(jobId);
        trimHistory(failed);
        ++failedJobs;
    }

    bool promoteRetry(api::JobRegistry& registry) {
        std::lock_guard lock(mutex);
        if (retry.empty()) {
            return false;
        }

        const auto jobId = retry.front();
        auto it = trackedJobs.find(jobId);
        if (it == trackedJobs.end()) {
            retry.pop_front();
            return false;
        }

        if (Clock::now() - it->second.lastFailureAt < std::chrono::seconds(1)) {
            return false;
        }

        retry.pop_front();
        pending.push_back(jobId);
        registry.incrementRetry(jobId);
        registry.markProcessing(jobId);
        return true;
    }

    bool spawnSubagent(std::string commandOrName) {
        std::lock_guard lock(mutex);
        const auto agentName = parseSubagentName(std::move(commandOrName));
        if (agentName.empty() ||
            std::find(availableAgents.begin(), availableAgents.end(), agentName) == availableAgents.end()) {
            return false;
        }

        activeAgents.insert(agentName);
        commandHistory.push_back("/subagents spawn " + agentName);
        trimHistory(commandHistory);
        return true;
    }

    RuntimeSnapshot snapshot() const {
        std::lock_guard lock(mutex);
        RuntimeSnapshot snapshot;
        snapshot.queuedJobs = pending.size();
        snapshot.activeJobs = running.size();
        snapshot.completedJobs = completedJobs;
        snapshot.failedJobs = failedJobs;
        snapshot.retryBacklog = retry.size();
        snapshot.maxConcurrentJobs = dynamicMaxConcurrentJobs;
        snapshot.activeAgents = orderedActiveAgentsLocked();
        return snapshot;
    }

    void setDynamicConcurrency(std::size_t value) {
        std::lock_guard lock(mutex);
        dynamicMaxConcurrentJobs = std::clamp(value, config.minConcurrentJobs, config.maxConcurrentJobs);
    }

    std::size_t recordUploads(const std::vector<api::JobInfo>& jobs,
                              std::vector<api::JobInfo>& uploadable) {
        std::lock_guard lock(mutex);
        for (const auto& job : jobs) {
            if ((job.status != api::JobStatus::Ready && job.status != api::JobStatus::FINISHED) ||
                job.videoUri.empty()) {
                continue;
            }

            if (publishedVideos.insert(job.jobId).second) {
                uploadable.push_back(job);
            }
        }
        return uploadable.size();
    }

    std::vector<std::string> pendingJobs() const {
        std::lock_guard lock(mutex);
        return toVector(pending);
    }

    std::vector<std::string> runningJobs() const {
        std::lock_guard lock(mutex);
        std::vector<std::string> jobs(running.begin(), running.end());
        std::sort(jobs.begin(), jobs.end());
        return jobs;
    }

    std::vector<std::string> finishedJobs() const {
        std::lock_guard lock(mutex);
        return toVector(finished);
    }

    std::vector<std::string> failedJobsList() const {
        std::lock_guard lock(mutex);
        return toVector(failed);
    }

    std::vector<std::string> activeAgentsList() const {
        std::lock_guard lock(mutex);
        return orderedActiveAgentsLocked();
    }

    std::vector<std::string> subagentCommands() const {
        std::lock_guard lock(mutex);
        return toVector(commandHistory);
    }

    RuntimeConfig config;
    mutable std::mutex mutex;
    std::unordered_map<std::string, ManagedJob> trackedJobs;
    std::deque<std::string> pending;
    std::deque<std::string> retry;
    std::unordered_set<std::string> running;
    std::deque<std::string> finished;
    std::deque<std::string> failed;
    std::unordered_set<std::string> publishedVideos;
    std::unordered_set<std::string> activeAgents;
    std::vector<std::string> availableAgents;
    std::deque<std::string> commandHistory;
    std::size_t completedJobs = 0;
    std::size_t failedJobs = 0;
    std::size_t dynamicMaxConcurrentJobs = 1;

private:
    std::vector<std::string> orderedActiveAgentsLocked() const {
        std::vector<std::string> agents;
        agents.push_back("OrchestratorAgent");
        for (const auto& agent : availableAgents) {
            if (activeAgents.count(agent) != 0) {
                agents.push_back(agent);
            }
        }
        return agents;
    }
};

class AutoJobAgent final : public core::Module {
public:
    AutoJobAgent(std::shared_ptr<SharedState> state,
                 api::JobRegistry& registry,
                 std::filesystem::path runtimeDirectory)
        : Module("AutoJobAgent")
        , state_(std::move(state))
        , registry_(registry)
        , runtimeDirectory_(std::move(runtimeDirectory))
        , logPath_(runtimeDirectory_ / "autojob.log") {}

    void update(double) override {
        const auto now = Clock::now();

        if (now - lastManifestWrite_ >= std::chrono::seconds(1)) {
            lastManifestWrite_ = now;
            writeManifest(now);
        }

        if (!state_->config.autoJobEnabled) {
            return;
        }

        if (now - lastDispatch_ < std::chrono::seconds(state_->config.autoJobIntervalSeconds)) {
            return;
        }
        lastDispatch_ = now;

        const auto snapshot = state_->snapshot();
        if (snapshot.queuedJobs + snapshot.activeJobs >= snapshot.maxConcurrentJobs) {
            appendLogLine(logPath_, "skip: queue is saturated");
            return;
        }

        const auto audioPath = resolvePath(state_->config.autoJobAudioFile);
        if (!std::filesystem::exists(audioPath)) {
            appendLogLine(logPath_, "skip: audio file missing at " + audioPath.string());
            return;
        }

        std::ostringstream prompt;
        prompt << "Autonomous EchoesEngine render " << (++sequence_)
               << " | style=" << state_->config.autoJobStyle
               << " | camera_motion=" << state_->config.autoJobCameraMotion
               << " | duration=" << state_->config.autoJobDurationSeconds << "s";

        const auto jobId = registry_.createJob(
            prompt.str(),
            audioPath.string(),
            state_->config.autoJobStyle,
            state_->config.autoJobDurationSeconds,
            1280,
            720);
        state_->enqueue(api::GenerateJobRequest{
            jobId,
            prompt.str(),
            audioPath.string(),
            state_->config.autoJobStyle,
            state_->config.autoJobDurationSeconds,
            1280,
            720
        });
        lastJobId_ = jobId;

        std::ostringstream logMessage;
        logMessage << "submitted " << jobId
                   << " audio=" << audioPath.string()
                   << " style=" << state_->config.autoJobStyle
                   << " camera_motion=" << state_->config.autoJobCameraMotion
                   << " duration=" << state_->config.autoJobDurationSeconds << "s";
        appendLogLine(logPath_, logMessage.str());
    }

private:
    void writeManifest(Clock::time_point now) {
        const auto snapshot = state_->snapshot();
        const auto audioPath = resolvePath(state_->config.autoJobAudioFile);
        const auto nextRun = lastDispatch_ + std::chrono::seconds(state_->config.autoJobIntervalSeconds);
        const auto secondsUntilNextRun =
            nextRun > now ? std::chrono::duration_cast<std::chrono::seconds>(nextRun - now).count() : 0;

        std::ostringstream json;
        json << "{\n"
             << "  \"agent\": \"AutoJobAgent\",\n"
             << "  \"enabled\": " << (state_->config.autoJobEnabled ? "true" : "false") << ",\n"
             << "  \"interval_seconds\": " << state_->config.autoJobIntervalSeconds << ",\n"
             << "  \"audio_file\": \"" << jsonEscape(audioPath.string()) << "\",\n"
             << "  \"scene_style\": \"" << jsonEscape(state_->config.autoJobStyle) << "\",\n"
             << "  \"camera_motion\": \"" << jsonEscape(state_->config.autoJobCameraMotion) << "\",\n"
             << "  \"duration_seconds\": " << state_->config.autoJobDurationSeconds << ",\n"
             << "  \"last_job_id\": \"" << jsonEscape(lastJobId_) << "\",\n"
             << "  \"seconds_until_next_run\": " << secondsUntilNextRun << ",\n"
             << "  \"max_concurrent_jobs\": " << snapshot.maxConcurrentJobs << "\n"
             << "}\n";
        writeTextFile(runtimeDirectory_ / "autojob_agent.json", json.str());
    }

    std::shared_ptr<SharedState> state_;
    api::JobRegistry& registry_;
    std::filesystem::path runtimeDirectory_;
    std::filesystem::path logPath_;
    Clock::time_point lastDispatch_{};
    Clock::time_point lastManifestWrite_{};
    std::string lastJobId_;
    std::size_t sequence_ = 0;
};

class OrchestratorAgent final : public core::Module {
public:
    OrchestratorAgent(std::shared_ptr<SharedState> state,
                      MasterAgent::JobProcessor processor,
                      std::filesystem::path runtimeDirectory)
        : Module("OrchestratorAgent")
        , state_(std::move(state))
        , processor_(std::move(processor))
        , runtimeDirectory_(std::move(runtimeDirectory)) {}

    void update(double) override {
        if (!bootstrapped_) {
            for (const auto& agent : state_->availableAgents) {
                (void)state_->spawnSubagent(agent);
            }
            bootstrapped_ = true;
        }

        if (context() != nullptr && context()->jobSystem != nullptr) {
            while (true) {
                api::GenerateJobRequest request;
                if (!state_->tryDispatch(request)) {
                    break;
                }

                context()->jobSystem->enqueue([state = state_, request, processor = processor_]() mutable {
                    const bool ok = processor(request);
                    if (ok) {
                        state->markSucceeded(request.jobId);
                    } else {
                        state->markFailed(request.jobId);
                    }
                });
            }
        }

        const auto now = Clock::now();
        if (now - lastWrite_ < std::chrono::milliseconds(700)) {
            return;
        }
        lastWrite_ = now;

        const auto snapshot = state_->snapshot();
        const auto activeAgents = state_->activeAgentsList();
        const auto commands = state_->subagentCommands();

        std::ostringstream json;
        json << "{\n"
             << "  \"agent\": \"OrchestratorAgent\",\n"
             << "  \"responsibilities\": "
             << "[\"manage job queue\", \"spawn sub-agents\", \"monitor tasks\", \"coordinate pipeline\"],\n"
             << "  \"api_endpoint\": \"" << jsonEscape(state_->config.apiEndpoint) << "\",\n"
             << "  \"render_output_directory\": \"" << jsonEscape(state_->config.renderOutputDirectory.string()) << "\",\n"
             << "  \"max_concurrent_jobs\": " << snapshot.maxConcurrentJobs << ",\n"
             << "  \"pipeline\": [\"audio_analysis\", \"scene_generation\", \"render\", \"encoding\", \"export\"],\n"
             << "  \"active_agents\": " << jsonArray(activeAgents) << ",\n"
             << "  \"supported_commands\": " << jsonArray(commands) << ",\n"
             << "  \"loop\": \"while system_running: generate scene -> render video -> encode video -> store output\"\n"
             << "}\n";
        writeTextFile(runtimeDirectory_ / "orchestrator_agent.json", json.str());

        std::ostringstream subagents;
        subagents << "{\n"
                  << "  \"orchestrator\": \"OrchestratorAgent\",\n"
                  << "  \"active\": " << jsonArray(activeAgents) << ",\n"
                  << "  \"commands\": " << jsonArray(commands) << "\n"
                  << "}\n";
        writeTextFile(runtimeDirectory_ / "subagents.json", subagents.str());
    }

private:
    std::shared_ptr<SharedState> state_;
    MasterAgent::JobProcessor processor_;
    std::filesystem::path runtimeDirectory_;
    Clock::time_point lastWrite_{};
    bool bootstrapped_ = false;
};

class RepairAgent final : public core::Module {
public:
    RepairAgent(std::shared_ptr<SharedState> state, api::JobRegistry& registry)
        : Module("RepairAgent")
        , state_(std::move(state))
        , registry_(registry) {}

    void update(double) override {
        (void)state_->promoteRetry(registry_);
    }

private:
    std::shared_ptr<SharedState> state_;
    api::JobRegistry& registry_;
};

class ScalingAgent final : public core::Module {
public:
    explicit ScalingAgent(std::shared_ptr<SharedState> state)
        : Module("ScalingAgent")
        , state_(std::move(state)) {}

    void update(double) override {
        state_->setDynamicConcurrency(state_->config.maxConcurrentJobs);
    }

private:
    std::shared_ptr<SharedState> state_;
};

class SharedQueueAgent final : public core::Module {
public:
    SharedQueueAgent(std::shared_ptr<SharedState> state, std::filesystem::path runtimeDirectory)
        : Module("SharedQueueAgent")
        , state_(std::move(state))
        , runtimeDirectory_(std::move(runtimeDirectory)) {}

    void update(double) override {
        const auto now = Clock::now();
        if (now - lastWrite_ < std::chrono::milliseconds(500)) {
            return;
        }
        lastWrite_ = now;

        std::ostringstream json;
        json << "{\n"
             << "  \"jobs\": {\n"
             << "    \"pending\": " << jsonArray(state_->pendingJobs()) << ",\n"
             << "    \"running\": " << jsonArray(state_->runningJobs()) << ",\n"
             << "    \"finished\": " << jsonArray(state_->finishedJobs()) << ",\n"
             << "    \"failed\": " << jsonArray(state_->failedJobsList()) << "\n"
             << "  }\n"
             << "}\n";
        writeTextFile(runtimeDirectory_ / "job_queue.json", json.str());
    }

private:
    std::shared_ptr<SharedState> state_;
    std::filesystem::path runtimeDirectory_;
    Clock::time_point lastWrite_{};
};

class StageAgent final : public core::Module {
public:
    StageAgent(std::string name,
               std::filesystem::path outputPath,
               api::JobRegistry& registry,
               api::JobStatus watchedStatus)
        : Module(std::move(name))
        , outputPath_(std::move(outputPath))
        , registry_(registry)
        , watchedStatus_(watchedStatus) {}

    void update(double) override {
        const auto now = Clock::now();
        if (now - lastWrite_ < std::chrono::milliseconds(700)) {
            return;
        }
        lastWrite_ = now;

        std::size_t activeCount = 0;
        for (const auto& job : registry_.listJobs()) {
            if (job.status == watchedStatus_) {
                ++activeCount;
            }
        }

        std::ostringstream json;
        json << "{\n"
             << "  \"agent\": \"" << jsonEscape(name()) << "\",\n"
             << "  \"stage\": \"" << jsonEscape(statusToString(watchedStatus_)) << "\",\n"
             << "  \"active_jobs\": " << activeCount << "\n"
             << "}\n";
        writeTextFile(outputPath_, json.str());
    }

private:
    std::filesystem::path outputPath_;
    api::JobRegistry& registry_;
    api::JobStatus watchedStatus_;
    Clock::time_point lastWrite_{};
};

class UploadAgent final : public core::Module {
public:
    UploadAgent(std::shared_ptr<SharedState> state,
                api::JobRegistry& registry,
                std::filesystem::path runtimeDirectory)
        : Module("UploadAgent")
        , state_(std::move(state))
        , registry_(registry)
        , runtimeDirectory_(std::move(runtimeDirectory)) {}

    void update(double) override {
        const auto now = Clock::now();
        if (now - lastWrite_ < std::chrono::milliseconds(1200)) {
            return;
        }
        lastWrite_ = now;

        std::vector<api::JobInfo> uploadable;
        state_->recordUploads(registry_.listJobs(), uploadable);

        std::ostringstream json;
        json << "{\n  \"videos\": [";
        for (std::size_t index = 0; index < uploadable.size(); ++index) {
            if (index > 0) {
                json << ",";
            }
            json << "\n    {"
                 << "\"job_id\":\"" << jsonEscape(uploadable[index].jobId) << "\","
                 << "\"video_uri\":\"" << jsonEscape(uploadable[index].videoUri) << "\""
                 << "}";
        }
        if (!uploadable.empty()) {
            json << "\n  ";
        }
        json << "]\n}\n";
        writeTextFile(runtimeDirectory_ / "upload_queue.json", json.str());
    }

private:
    std::shared_ptr<SharedState> state_;
    api::JobRegistry& registry_;
    std::filesystem::path runtimeDirectory_;
    Clock::time_point lastWrite_{};
};

class MonitoringAgent final : public core::Module {
public:
    MonitoringAgent(std::shared_ptr<SharedState> state,
                    api::JobRegistry& registry,
                    std::filesystem::path runtimeDirectory)
        : Module("MonitoringAgent")
        , state_(std::move(state))
        , registry_(registry)
        , runtimeDirectory_(std::move(runtimeDirectory)) {}

    void update(double) override {
        const auto now = Clock::now();
        if (now - lastWrite_ < std::chrono::milliseconds(900)) {
            return;
        }
        lastWrite_ = now;

        std::size_t analysis = 0;
        std::size_t scene = 0;
        std::size_t rendering = 0;
        std::size_t encoding = 0;
        std::size_t ready = 0;
        std::size_t failed = 0;

        for (const auto& job : registry_.listJobs()) {
            switch (job.status) {
                case api::JobStatus::ANALYSIS:
                    ++analysis;
                    break;
                case api::JobStatus::SCENE_GENERATION:
                    ++scene;
                    break;
                case api::JobStatus::RENDERING:
                    ++rendering;
                    break;
                case api::JobStatus::ENCODING:
                    ++encoding;
                    break;
                case api::JobStatus::Ready:
                case api::JobStatus::FINISHED:
                    ++ready;
                    break;
                case api::JobStatus::Failed:
                    ++failed;
                    break;
                default:
                    break;
            }
        }

        const auto snapshot = state_->snapshot();
        const auto telemetry = collectSystemTelemetry(state_->config.renderOutputDirectory);

        std::ostringstream json;
        json << "{\n"
             << "  \"queued_jobs\": " << snapshot.queuedJobs << ",\n"
             << "  \"active_jobs\": " << snapshot.activeJobs << ",\n"
             << "  \"completed_jobs\": " << snapshot.completedJobs << ",\n"
             << "  \"failed_jobs\": " << snapshot.failedJobs << ",\n"
             << "  \"retry_backlog\": " << snapshot.retryBacklog << ",\n"
             << "  \"max_concurrent_jobs\": " << snapshot.maxConcurrentJobs << ",\n"
             << "  \"analysis_jobs\": " << analysis << ",\n"
             << "  \"scene_jobs\": " << scene << ",\n"
             << "  \"rendering_jobs\": " << rendering << ",\n"
             << "  \"encoding_jobs\": " << encoding << ",\n"
             << "  \"ready_jobs\": " << ready << ",\n"
             << "  \"failed_registry_jobs\": " << failed << ",\n"
             << "  \"active_agents\": " << jsonArray(snapshot.activeAgents) << ",\n"
             << "  \"cpu_usage_percent\": " << telemetry.cpuUsagePercent << ",\n"
             << "  \"gpu_usage_percent\": null,\n"
             << "  \"gpu_status\": \"" << jsonEscape(telemetry.gpuStatus) << "\",\n"
             << "  \"ram_usage_percent\": " << telemetry.ramUsagePercent << ",\n"
             << "  \"ram_used_mb\": " << telemetry.ramUsedMb << ",\n"
             << "  \"ram_total_mb\": " << telemetry.ramTotalMb << ",\n"
             << "  \"disk_usage_percent\": " << telemetry.diskUsagePercent << ",\n"
             << "  \"disk_free_gb\": " << telemetry.diskFreeGb << ",\n"
             << "  \"disk_total_gb\": " << telemetry.diskTotalGb << ",\n"
             << "  \"render_failures\": " << failed << "\n"
             << "}\n";
        writeTextFile(runtimeDirectory_ / "monitoring.json", json.str());
    }

private:
    std::shared_ptr<SharedState> state_;
    api::JobRegistry& registry_;
    std::filesystem::path runtimeDirectory_;
    Clock::time_point lastWrite_{};
};

class OptimizationAgent final : public core::Module {
public:
    OptimizationAgent(std::shared_ptr<SharedState> state, std::filesystem::path runtimeDirectory)
        : Module("OptimizationAgent")
        , state_(std::move(state))
        , runtimeDirectory_(std::move(runtimeDirectory)) {}

    void update(double) override {
        const auto now = Clock::now();
        if (now - lastWrite_ < std::chrono::milliseconds(1500)) {
            return;
        }
        lastWrite_ = now;

        const auto snapshot = state_->snapshot();
        const bool underLoad = snapshot.queuedJobs > snapshot.maxConcurrentJobs;

        std::ostringstream json;
        json << "{\n"
             << "  \"under_load\": " << (underLoad ? "true" : "false") << ",\n"
             << "  \"recommendation\": \""
             << (underLoad ? "prepare scene generation while active renders are running"
                           : "pipeline synchronized; keep next job prepared during rendering")
             << "\",\n"
             << "  \"active_agents\": " << jsonArray(snapshot.activeAgents) << "\n"
             << "}\n";
        writeTextFile(runtimeDirectory_ / "optimization.json", json.str());
    }

private:
    std::shared_ptr<SharedState> state_;
    std::filesystem::path runtimeDirectory_;
    Clock::time_point lastWrite_{};
};

class DeploymentAgent final : public core::Module {
public:
    DeploymentAgent(std::shared_ptr<SharedState> state, std::filesystem::path runtimeDirectory)
        : Module("DeploymentAgent")
        , state_(std::move(state))
        , runtimeDirectory_(std::move(runtimeDirectory)) {}

    void update(double) override {
        const auto now = Clock::now();
        if (now - lastWrite_ < std::chrono::seconds(2)) {
            return;
        }
        lastWrite_ = now;

        const auto activeAgents = state_->activeAgentsList();
        const auto allAgents = [&]() {
            auto values = state_->availableAgents;
            values.insert(values.begin(), "OrchestratorAgent");
            return values;
        }();

        std::ostringstream deployment;
        deployment << "{\n"
                   << "  \"service\": \"EchoesEnginePro\",\n"
                   << "  \"orchestrator\": \"OrchestratorAgent\",\n"
                   << "  \"api_endpoint\": \"" << jsonEscape(state_->config.apiEndpoint) << "\",\n"
                   << "  \"api\": [\"POST /generate\", \"GET /status/{job_id}\", \"GET /video/{job_id}\"],\n"
                   << "  \"mode\": \"continuous\",\n"
                   << "  \"render_output_directory\": \"" << jsonEscape(state_->config.renderOutputDirectory.string()) << "\",\n"
                   << "  \"active_agents\": " << jsonArray(activeAgents) << "\n"
                   << "}\n";
        writeTextFile(runtimeDirectory_ / "deployment.json", deployment.str());

        std::ostringstream architecture;
        architecture << "{\n"
                     << "  \"orchestrator\": \"OrchestratorAgent\",\n"
                     << "  \"specialized_agents\": " << jsonArray(allAgents) << ",\n"
                     << "  \"shared_job_queue\": \"job_queue.json\",\n"
                     << "  \"pipeline\": [\"audio_analysis\", \"scene_generation\", \"render\", \"encoding\", \"export\"],\n"
                     << "  \"max_concurrent_jobs\": " << state_->config.maxConcurrentJobs << ",\n"
                     << "  \"api_endpoint\": \"" << jsonEscape(state_->config.apiEndpoint) << "\",\n"
                     << "  \"render_output_directory\": \"" << jsonEscape(state_->config.renderOutputDirectory.string()) << "\",\n"
                     << "  \"openclaw_subagents\": ["
                     << "\"/subagents spawn SceneAgent\", "
                     << "\"/subagents spawn RenderAgent\", "
                     << "\"/subagents spawn EncodingAgent\""
                     << "]\n"
                     << "}\n";
        writeTextFile(runtimeDirectory_ / "system_architecture.json", architecture.str());
    }

private:
    std::shared_ptr<SharedState> state_;
    std::filesystem::path runtimeDirectory_;
    Clock::time_point lastWrite_{};
};

} // namespace

struct MasterAgent::Impl {
    Impl(api::JobRegistry& jobRegistry, JobProcessor jobProcessor, RuntimeConfig runtimeConfig)
        : registry(jobRegistry)
        , processor(std::move(jobProcessor))
        , config(std::move(runtimeConfig))
        , state(std::make_shared<SharedState>(config))
        , engine(core::Engine::Config{"EchoesEngine MasterAgent", config.maxConcurrentJobs + 4, config.agentTickRate}) {}

    bool start() {
        std::filesystem::create_directories(config.runtimeDirectory);
        std::filesystem::create_directories(config.renderOutputDirectory);

        engine.registerModule(std::make_shared<AutoJobAgent>(state, registry, config.runtimeDirectory));
        engine.registerModule(std::make_shared<OrchestratorAgent>(state, processor, config.runtimeDirectory));
        engine.registerModule(std::make_shared<StageAgent>(
            "AudioAgent",
            config.runtimeDirectory / "audio_agent.json",
            registry,
            api::JobStatus::ANALYSIS));
        engine.registerModule(std::make_shared<StageAgent>(
            "SceneAgent",
            config.runtimeDirectory / "scene_agent.json",
            registry,
            api::JobStatus::SCENE_GENERATION));
        engine.registerModule(std::make_shared<StageAgent>(
            "RenderAgent",
            config.runtimeDirectory / "render_agent.json",
            registry,
            api::JobStatus::RENDERING));
        engine.registerModule(std::make_shared<StageAgent>(
            "EncodingAgent",
            config.runtimeDirectory / "encoding_agent.json",
            registry,
            api::JobStatus::ENCODING));
        engine.registerModule(std::make_shared<SharedQueueAgent>(state, config.runtimeDirectory));
        engine.registerModule(std::make_shared<UploadAgent>(state, registry, config.runtimeDirectory));
        engine.registerModule(std::make_shared<MonitoringAgent>(state, registry, config.runtimeDirectory));
        engine.registerModule(std::make_shared<RepairAgent>(state, registry));
        engine.registerModule(std::make_shared<OptimizationAgent>(state, config.runtimeDirectory));
        engine.registerModule(std::make_shared<ScalingAgent>(state));
        engine.registerModule(std::make_shared<DeploymentAgent>(state, config.runtimeDirectory));

        if (!engine.init()) {
            return false;
        }

        running = true;
        engineThread = std::thread([this]() { engine.run(); });
        return true;
    }

    void stop() {
        if (!running) {
            return;
        }

        running = false;
        engine.stop();
        if (engineThread.joinable()) {
            engineThread.join();
        }
        engine.shutdown();
    }

    api::JobRegistry& registry;
    JobProcessor processor;
    RuntimeConfig config;
    std::shared_ptr<SharedState> state;
    core::Engine engine;
    std::thread engineThread;
    bool running = false;
};

MasterAgent::MasterAgent(api::JobRegistry& registry,
                         JobProcessor processor,
                         RuntimeConfig config)
    : impl_(std::make_unique<Impl>(registry, std::move(processor), std::move(config))) {}

MasterAgent::~MasterAgent() {
    stop();
}

bool MasterAgent::start() {
    return impl_->start();
}

void MasterAgent::stop() {
    impl_->stop();
}

void MasterAgent::submit(api::GenerateJobRequest request) {
    impl_->state->enqueue(std::move(request));
}

RuntimeSnapshot MasterAgent::snapshot() const {
    return impl_->state->snapshot();
}

RuntimeConfig MasterAgent::config() const {
    return impl_->config;
}

bool MasterAgent::spawnSubagent(std::string agentName) {
    return impl_->state->spawnSubagent(std::move(agentName));
}

std::vector<std::string> MasterAgent::activeAgents() const {
    return impl_->state->activeAgentsList();
}

RuntimeConfig loadRuntimeConfigFromEnvironment(const std::filesystem::path& runtimeRoot) {
    RuntimeConfig config;
    if (!runtimeRoot.empty()) {
        config.runtimeDirectory = runtimeRoot;
    }

    if (const auto minJobs = readEnvSizeT("ECHOES_MIN_CONCURRENT_JOBS")) {
        config.minConcurrentJobs = std::max<std::size_t>(1, *minJobs);
    }
    if (const auto maxJobs = readEnvSizeT("ECHOES_MAX_CONCURRENT_JOBS")) {
        config.maxConcurrentJobs = std::max(config.minConcurrentJobs, *maxJobs);
    }
    if (const auto retries = readEnvSizeT("ECHOES_MAX_RETRIES")) {
        config.maxRetries = *retries;
    }
    if (const auto tickRate = readEnvDouble("ECHOES_AGENT_TICK_RATE")) {
        config.agentTickRate = std::max(1.0, *tickRate);
    }
    if (const auto apiEndpoint = readEnvString("ECHOES_API_ENDPOINT")) {
        config.apiEndpoint = *apiEndpoint;
    }
    if (const auto renderOutputDirectory = readEnvString("ECHOES_RENDER_OUTPUT_DIR")) {
        config.renderOutputDirectory = *renderOutputDirectory;
    }
    if (const auto autoJobEnabled = readEnvBool("ECHOES_AUTOJOB_ENABLED")) {
        config.autoJobEnabled = *autoJobEnabled;
    }
    if (const auto autoJobInterval = readEnvSizeT("ECHOES_AUTOJOB_INTERVAL_SECONDS")) {
        config.autoJobIntervalSeconds = std::max<std::size_t>(1, *autoJobInterval);
    }
    if (const auto autoJobAudio = readEnvString("ECHOES_AUTOJOB_AUDIO_FILE")) {
        config.autoJobAudioFile = *autoJobAudio;
    }
    if (const auto autoJobStyle = readEnvString("ECHOES_AUTOJOB_STYLE")) {
        config.autoJobStyle = *autoJobStyle;
    }
    if (const auto autoJobCamera = readEnvString("ECHOES_AUTOJOB_CAMERA_MOTION")) {
        config.autoJobCameraMotion = *autoJobCamera;
    }
    if (const auto autoJobDuration = readEnvDouble("ECHOES_AUTOJOB_DURATION_SECONDS")) {
        config.autoJobDurationSeconds = std::max(0.1, *autoJobDuration);
    }
    return config;
}

} // namespace echoes::platform
