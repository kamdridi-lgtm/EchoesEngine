#include "EchoesEngine/export/VideoExporter.h"

#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <utility>

namespace echoes::export_ {

RenderResult render_video(FrameCaptureSource& source,
                          const ExportSettings& settings,
                          std::size_t frameCount) {
    RenderResult result;
    if (settings.resolution.width == 0 || settings.resolution.height == 0 || frameCount == 0) {
        result.message = "invalid export settings";
        return result;
    }

    result.frameFolder = settings.workingDirectory / "frames";
    std::filesystem::create_directories(result.frameFolder);
    result.frames.reserve(frameCount);

    for (std::size_t index = 0; index < frameCount; ++index) {
        CapturedFrame frame;
        frame.timestamp = std::chrono::steady_clock::now();
        if (!source.capture(settings.resolution.width, settings.resolution.height, frame.pixels)) {
            result.message = "frame capture failed";
            return result;
        }

        std::ostringstream frameName;
        frameName << "frame_" << std::setw(4) << std::setfill('0') << index << ".ppm";
        const auto framePath = result.frameFolder / frameName.str();
        std::ofstream out(framePath, std::ios::binary);
        if (!out) {
            result.message = "could not write frame file";
            return result;
        }

        out << "P6\n"
            << settings.resolution.width << " " << settings.resolution.height << "\n255\n";
        for (std::size_t pixel = 0; pixel + 3 < frame.pixels.size(); pixel += 4) {
            out.put(static_cast<char>(frame.pixels[pixel + 0]));
            out.put(static_cast<char>(frame.pixels[pixel + 1]));
            out.put(static_cast<char>(frame.pixels[pixel + 2]));
        }

        result.frames.push_back(std::move(frame));
    }

    result.success = true;
    result.message = "frames captured";
    return result;
}

FFmpegPlan encode_video(const ExportSettings& settings, std::string_view frameDirectory) {
    return build_ffmpeg_plan(settings, std::string(frameDirectory));
}

ExportJob export_mp4(ExportJobManager& manager,
                     FrameCaptureSource& source,
                     const ExportSettings& settings,
                     std::size_t frameCount) {
    const auto jobId = manager.scheduleJob(settings);
    manager.updateStatus(jobId, "rendering", "capturing frames");

    auto renderResult = render_video(source, settings, frameCount);
    if (!renderResult.success) {
        manager.updateStatus(jobId, "failed", renderResult.message);
        return manager.queryJob(jobId);
    }

    const auto ffmpegPlan = encode_video(settings, renderResult.frameFolder.string());
    if (!ffmpegPlan.isValid()) {
        manager.updateStatus(jobId, "failed", "ffmpeg plan generation failed");
        return manager.queryJob(jobId);
    }

    manager.updateStatus(jobId, "ready", ffmpegPlan.commandLine);
    return manager.queryJob(jobId);
}

} // namespace echoes::export_
