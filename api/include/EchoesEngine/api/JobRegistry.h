#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace echoes::api {

enum class JobStatus {
    Pending,
    Processing,
    ANALYSIS,
    SCENE_GENERATION,
    RENDERING,
    ENCODING,
    FINISHED,
    Ready,
    Failed
};

struct JobInfo {
    std::string jobId;
    JobStatus status = JobStatus::Pending;
    std::string prompt;
    std::string audioReference;
    std::string style;
    std::string videoUri;
    std::string failureReason;
    double requestedDurationSeconds = 0.0;
    std::size_t renderedFrames = 0;
    std::size_t totalFrames = 0;
    double audioDurationSeconds = 0.0;
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t fps = 0;
    uint32_t retryCount = 0;
    std::chrono::system_clock::time_point updatedAt;
};

class JobRegistry {
public:
    std::string createJob(std::string prompt,
                          std::string audioRef,
                          std::string style,
                          double requestedDurationSeconds,
                          uint32_t requestedWidth,
                          uint32_t requestedHeight);
    std::optional<JobInfo> getJob(std::string_view jobId) const;
    std::vector<JobInfo> listJobs() const;
    void markProcessing(std::string_view jobId);
    void markStatus(std::string_view jobId, JobStatus status);
    void configureRender(std::string_view jobId,
                         double audioDurationSeconds,
                         std::size_t totalFrames,
                         uint32_t width,
                         uint32_t height,
                         uint32_t fps);
    void updateRenderedFrames(std::string_view jobId, std::size_t renderedFrames);
    void incrementRetry(std::string_view jobId);
    void markReady(std::string_view jobId, std::string videoUri);
    void markFailed(std::string_view jobId, std::string reason);

private:
    mutable std::mutex mutex_;
    std::unordered_map<std::string, JobInfo> jobs_;
    std::atomic<uint64_t> sequence_{1};
};

} // namespace echoes::api
