#include "EchoesEngine/api/JobRegistry.h"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <sstream>

namespace echoes::api {

namespace {

std::string escapeJson(std::string_view value) {
    std::string escaped;
    escaped.reserve(value.size() + 16);
    for (char ch : value) {
        switch (ch) {
            case '\\':
                escaped += "\\\\";
                break;
            case '"':
                escaped += "\\\"";
                break;
            case '\n':
                escaped += "\\n";
                break;
            case '\r':
                escaped += "\\r";
                break;
            default:
                escaped.push_back(ch);
                break;
        }
    }
    return escaped;
}

std::string queueFolderName(JobStatus status) {
    switch (status) {
        case JobStatus::Pending:
            return "queued";
        case JobStatus::Processing:
        case JobStatus::ANALYSIS:
        case JobStatus::SCENE_GENERATION:
        case JobStatus::RENDERING:
            return "generating";
        case JobStatus::ENCODING:
            return "encoding";
        case JobStatus::FINISHED:
        case JobStatus::Ready:
            return "finished";
        case JobStatus::Failed:
            return "finished";
    }
    return "queued";
}

std::string publicStatus(JobStatus status) {
    switch (status) {
        case JobStatus::Pending:
            return "queued";
        case JobStatus::Processing:
        case JobStatus::ANALYSIS:
        case JobStatus::SCENE_GENERATION:
        case JobStatus::RENDERING:
            return "generating";
        case JobStatus::ENCODING:
            return "encoding";
        case JobStatus::FINISHED:
        case JobStatus::Ready:
            return "finished";
        case JobStatus::Failed:
            return "failed";
    }
    return "queued";
}

std::filesystem::path queueRoot() {
    return std::filesystem::current_path() / "jobs";
}

double publicProgress(const JobInfo& info) {
    switch (info.status) {
        case JobStatus::Pending:
            return 5.0;
        case JobStatus::Processing:
            return 10.0;
        case JobStatus::ANALYSIS:
            return 20.0;
        case JobStatus::SCENE_GENERATION:
            return 40.0;
        case JobStatus::RENDERING:
            if (info.totalFrames == 0) {
                return 60.0;
            }
            return 40.0 + (static_cast<double>(info.renderedFrames) / static_cast<double>(info.totalFrames)) * 45.0;
        case JobStatus::ENCODING:
            return 92.0;
        case JobStatus::FINISHED:
        case JobStatus::Ready:
            return 100.0;
        case JobStatus::Failed:
            return info.renderedFrames > 0 ? 95.0 : 0.0;
    }
    return 0.0;
}

} // namespace

std::string JobRegistry::createJob(std::string prompt,
                                   std::string audioRef,
                                   std::string style,
                                   double requestedDurationSeconds,
                                   uint32_t requestedWidth,
                                   uint32_t requestedHeight) {
    const auto now = std::chrono::system_clock::now();
    const auto id = std::string{"job-"} + std::to_string(sequence_.fetch_add(1, std::memory_order_relaxed));

    JobInfo info;
    info.jobId = id;
    info.status = JobStatus::Pending;
    info.prompt = std::move(prompt);
    info.audioReference = std::move(audioRef);
    info.style = std::move(style);
    info.requestedDurationSeconds = requestedDurationSeconds;
    info.width = requestedWidth;
    info.height = requestedHeight;
    info.updatedAt = now;

    std::lock_guard lock(mutex_);
    jobs_.emplace(id, std::move(info));
    const auto queued = queueRoot() / "queued";
    const auto generating = queueRoot() / "generating";
    const auto encoding = queueRoot() / "encoding";
    const auto finished = queueRoot() / "finished";
    std::filesystem::create_directories(queued);
    std::filesystem::create_directories(generating);
    std::filesystem::create_directories(encoding);
    std::filesystem::create_directories(finished);

    const auto& job = jobs_.at(id);
    std::ofstream output(queued / (id + ".json"));
    output << "{"
           << "\"jobId\":\"" << escapeJson(job.jobId) << "\""
           << ",\"prompt\":\"" << escapeJson(job.prompt) << "\""
           << ",\"style\":\"" << escapeJson(job.style) << "\""
           << ",\"duration\":" << job.requestedDurationSeconds
           << ",\"resolution\":\"" << job.width << "x" << job.height << "\""
           << ",\"status\":\"" << publicStatus(job.status) << "\""
           << ",\"videoPath\":\"" << escapeJson(job.videoUri) << "\""
           << ",\"progress\":" << publicProgress(job)
           << "}";
    return id;
}

std::optional<JobInfo> JobRegistry::getJob(std::string_view jobId) const {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(std::string(jobId)); it != jobs_.end()) {
        return it->second;
    }
    return std::nullopt;
}

std::vector<JobInfo> JobRegistry::listJobs() const {
    std::lock_guard lock(mutex_);
    std::vector<JobInfo> jobs;
    jobs.reserve(jobs_.size());
    for (const auto& [_, info] : jobs_) {
        jobs.push_back(info);
    }
    return jobs;
}

void JobRegistry::markProcessing(std::string_view jobId) {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(std::string(jobId)); it != jobs_.end()) {
        it->second.status = JobStatus::Processing;
        it->second.failureReason.clear();
        it->second.updatedAt = std::chrono::system_clock::now();
        const auto root = queueRoot();
        std::filesystem::remove(root / "queued" / (it->second.jobId + ".json"));
        std::filesystem::remove(root / "encoding" / (it->second.jobId + ".json"));
        std::filesystem::remove(root / "finished" / (it->second.jobId + ".json"));
        std::ofstream output(root / "generating" / (it->second.jobId + ".json"));
        output << "{"
               << "\"jobId\":\"" << escapeJson(it->second.jobId) << "\""
               << ",\"prompt\":\"" << escapeJson(it->second.prompt) << "\""
               << ",\"style\":\"" << escapeJson(it->second.style) << "\""
               << ",\"duration\":" << it->second.requestedDurationSeconds
               << ",\"resolution\":\"" << it->second.width << "x" << it->second.height << "\""
               << ",\"status\":\"" << publicStatus(it->second.status) << "\""
               << ",\"videoPath\":\"" << escapeJson(it->second.videoUri) << "\""
               << ",\"progress\":" << publicProgress(it->second)
               << "}";
    }
}

void JobRegistry::markStatus(std::string_view jobId, JobStatus status) {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(std::string(jobId)); it != jobs_.end()) {
        it->second.status = status;
        it->second.updatedAt = std::chrono::system_clock::now();
        const auto root = queueRoot();
        std::filesystem::remove(root / "queued" / (it->second.jobId + ".json"));
        std::filesystem::remove(root / "generating" / (it->second.jobId + ".json"));
        std::filesystem::remove(root / "encoding" / (it->second.jobId + ".json"));
        std::filesystem::remove(root / "finished" / (it->second.jobId + ".json"));
        std::ofstream output(root / queueFolderName(status) / (it->second.jobId + ".json"));
        output << "{"
               << "\"jobId\":\"" << escapeJson(it->second.jobId) << "\""
               << ",\"prompt\":\"" << escapeJson(it->second.prompt) << "\""
               << ",\"style\":\"" << escapeJson(it->second.style) << "\""
               << ",\"duration\":" << it->second.requestedDurationSeconds
               << ",\"resolution\":\"" << it->second.width << "x" << it->second.height << "\""
               << ",\"status\":\"" << publicStatus(it->second.status) << "\""
               << ",\"videoPath\":\"" << escapeJson(it->second.videoUri) << "\""
               << ",\"progress\":" << publicProgress(it->second)
               << "}";
    }
}

void JobRegistry::configureRender(std::string_view jobId,
                                  double audioDurationSeconds,
                                  std::size_t totalFrames,
                                  uint32_t width,
                                  uint32_t height,
                                  uint32_t fps) {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(std::string(jobId)); it != jobs_.end()) {
        it->second.audioDurationSeconds = audioDurationSeconds;
        it->second.totalFrames = totalFrames;
        it->second.width = width;
        it->second.height = height;
        it->second.fps = fps;
        it->second.updatedAt = std::chrono::system_clock::now();
    }
}

void JobRegistry::updateRenderedFrames(std::string_view jobId, std::size_t renderedFrames) {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(std::string(jobId)); it != jobs_.end()) {
        it->second.renderedFrames = renderedFrames;
        it->second.updatedAt = std::chrono::system_clock::now();
        const auto root = queueRoot();
        if (it->second.status == JobStatus::RENDERING || it->second.status == JobStatus::Processing ||
            it->second.status == JobStatus::ANALYSIS || it->second.status == JobStatus::SCENE_GENERATION) {
            std::ofstream output(root / "generating" / (it->second.jobId + ".json"));
            output << "{"
                   << "\"jobId\":\"" << escapeJson(it->second.jobId) << "\""
                   << ",\"prompt\":\"" << escapeJson(it->second.prompt) << "\""
                   << ",\"style\":\"" << escapeJson(it->second.style) << "\""
                   << ",\"duration\":" << it->second.requestedDurationSeconds
                   << ",\"resolution\":\"" << it->second.width << "x" << it->second.height << "\""
                   << ",\"status\":\"" << publicStatus(it->second.status) << "\""
                   << ",\"videoPath\":\"" << escapeJson(it->second.videoUri) << "\""
                   << ",\"progress\":" << publicProgress(it->second)
                   << "}";
        }
    }
}

void JobRegistry::incrementRetry(std::string_view jobId) {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(std::string(jobId)); it != jobs_.end()) {
        ++it->second.retryCount;
        it->second.updatedAt = std::chrono::system_clock::now();
    }
}

void JobRegistry::markReady(std::string_view jobId, std::string videoUri) {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(std::string(jobId)); it != jobs_.end()) {
        it->second.status = JobStatus::FINISHED;
        it->second.videoUri = std::move(videoUri);
        it->second.renderedFrames = it->second.totalFrames;
        it->second.failureReason.clear();
        it->second.updatedAt = std::chrono::system_clock::now();
        const auto root = queueRoot();
        std::filesystem::remove(root / "queued" / (it->second.jobId + ".json"));
        std::filesystem::remove(root / "generating" / (it->second.jobId + ".json"));
        std::filesystem::remove(root / "encoding" / (it->second.jobId + ".json"));
        std::ofstream output(root / "finished" / (it->second.jobId + ".json"));
        output << "{"
               << "\"jobId\":\"" << escapeJson(it->second.jobId) << "\""
               << ",\"prompt\":\"" << escapeJson(it->second.prompt) << "\""
               << ",\"style\":\"" << escapeJson(it->second.style) << "\""
               << ",\"duration\":" << it->second.requestedDurationSeconds
               << ",\"resolution\":\"" << it->second.width << "x" << it->second.height << "\""
               << ",\"status\":\"" << publicStatus(it->second.status) << "\""
               << ",\"videoPath\":\"" << escapeJson(it->second.videoUri) << "\""
               << ",\"progress\":100"
               << "}";
    }
}

void JobRegistry::markFailed(std::string_view jobId, std::string reason) {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(std::string(jobId)); it != jobs_.end()) {
        it->second.status = JobStatus::Failed;
        it->second.failureReason = std::move(reason);
        it->second.updatedAt = std::chrono::system_clock::now();
        const auto root = queueRoot();
        std::filesystem::remove(root / "queued" / (it->second.jobId + ".json"));
        std::filesystem::remove(root / "generating" / (it->second.jobId + ".json"));
        std::filesystem::remove(root / "encoding" / (it->second.jobId + ".json"));
        std::ofstream output(root / "finished" / (it->second.jobId + ".json"));
        output << "{"
               << "\"jobId\":\"" << escapeJson(it->second.jobId) << "\""
               << ",\"prompt\":\"" << escapeJson(it->second.prompt) << "\""
               << ",\"style\":\"" << escapeJson(it->second.style) << "\""
               << ",\"duration\":" << it->second.requestedDurationSeconds
               << ",\"resolution\":\"" << it->second.width << "x" << it->second.height << "\""
               << ",\"status\":\"" << publicStatus(it->second.status) << "\""
               << ",\"videoPath\":\"" << escapeJson(it->second.videoUri) << "\""
               << ",\"progress\":" << publicProgress(it->second)
               << "}";
    }
}

} // namespace echoes::api
