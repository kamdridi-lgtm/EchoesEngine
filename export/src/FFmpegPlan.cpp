#include "EchoesEngine/export/FFmpegPlan.h"

#include <sstream>

namespace echoes::export_ {

FFmpegPlan build_ffmpeg_plan(const ExportSettings& settings, const std::string& frameDir) {
    FFmpegPlan plan;
    std::ostringstream oss;

    oss << "ffmpeg -y";
    oss << " -framerate " << settings.frameRate;
    oss << " -start_number 0";
    oss << " -i \"" << frameDir << "/frame_%04d.ppm\"";
    oss << " -c:v " << plan.codec;
    oss << " -pix_fmt yuv420p";
    oss << " -preset medium";
    oss << " -r " << settings.frameRate;
    oss << " -g " << settings.keyframeInterval;
    oss << " \"" << settings.outputFile << "\"";

    plan.commandLine = oss.str();
    plan.valid = true;
    return plan;
}

} // namespace echoes::export_
