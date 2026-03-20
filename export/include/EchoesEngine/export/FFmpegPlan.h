#pragma once

#include "EchoesEngine/export/FrameCapture.h"

#include <string>

namespace echoes::export_ {

struct FFmpegPlan {
    std::string commandLine;
    std::string codec = "libx264";
    bool        valid = false;

    bool isValid() const noexcept {
        return valid && !commandLine.empty();
    }
};

FFmpegPlan build_ffmpeg_plan(const ExportSettings& settings, const std::string& frameDir);

} // namespace echoes::export_
