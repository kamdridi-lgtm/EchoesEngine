#include "EchoesEngine/export/ExportJob.h"

#include <sstream>

namespace echoes::export_ {

std::string ExportJobManager::scheduleJob(const ExportSettings& settings) {
    std::lock_guard lock(mutex_);
    std::ostringstream id;
    id << "EXP-" << nextId_++;
    ExportJob job{ id.str(), settings, "queued", "job scheduled" };
    jobs_.emplace(job.jobId, job);
    return job.jobId;
}

void ExportJobManager::updateStatus(const std::string& jobId, std::string_view status, std::string_view message) {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(jobId); it != jobs_.end()) {
        it->second.status = status;
        it->second.message = message;
    }
}

ExportJob ExportJobManager::queryJob(const std::string& jobId) const {
    std::lock_guard lock(mutex_);
    if (auto it = jobs_.find(jobId); it != jobs_.end()) {
        return it->second;
    }
    ExportJob placeholder;
    placeholder.jobId = jobId;
    placeholder.status = "unknown";
    placeholder.message = "job id not found";
    return placeholder;
}

} // namespace echoes::export_
