#pragma once

#include "EchoesEngine/export/ExportJob.h"
#include "EchoesEngine/export/FFmpegPlan.h"
#include "EchoesEngine/export/FrameCapture.h"

#include <cstddef>
#include <string_view>

namespace echoes::export_ {

RenderResult render_video(FrameCaptureSource& source,
                          const ExportSettings& settings,
                          std::size_t frameCount);

FFmpegPlan encode_video(const ExportSettings& settings, std::string_view frameDirectory);

ExportJob export_mp4(ExportJobManager& manager,
                     FrameCaptureSource& source,
                     const ExportSettings& settings,
                     std::size_t frameCount);

} // namespace echoes::export_
