#pragma once

#include "EchoesEngine/export/FrameCapture.h"

#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>

namespace echoes::export_ {

struct ExportJob {
    std::string jobId;
    ExportSettings settings;
    std::string status = "queued";
    std::string message;
};

class ExportJobManager {
public:
    std::string scheduleJob(const ExportSettings& settings);
    void updateStatus(const std::string& jobId, std::string_view status, std::string_view message);
    ExportJob queryJob(const std::string& jobId) const;

private:
    mutable std::mutex mutex_;
    std::unordered_map<std::string, ExportJob> jobs_;
    uint64_t nextId_ = 1;
};

} // namespace echoes::export_
