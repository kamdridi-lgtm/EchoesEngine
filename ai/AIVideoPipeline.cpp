#include "AIVideoPipeline.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <sstream>
#include <thread>

namespace echoes::ai {

namespace {

const char* stage_name(StageType stage) {
    switch (stage) {
        case StageType::PromptToVideo:
            return "prompt";
        case StageType::FrameGeneration:
            return "frames";
        case StageType::FrameInterpolation:
            return "interpolation";
        case StageType::StyleTransfer:
            return "style";
        case StageType::ImageToVideo:
            return "image-to-video";
        case StageType::Composition:
            return "composition";
        default:
            return "unknown";
    }
}

const char* model_name(ModelFamily family) {
    switch (family) {
        case ModelFamily::StableVideoDiffusion:
            return "stable-diffusion";
        case ModelFamily::AnimateDiff:
            return "animatediff";
        case ModelFamily::FrameInterpolator:
            return "frame-interpolator";
        case ModelFamily::StyleTransfer:
            return "style-transfer";
        case ModelFamily::ImageToVideo:
            return "image-to-video";
        default:
            return "generic";
    }
}

} // namespace

AIVideoPipeline::AIVideoPipeline(size_t workerCount) {
    const size_t hardware = std::thread::hardware_concurrency();
    const size_t count = workerCount == 0 ? std::max<size_t>(1, hardware == 0 ? 1 : hardware) : workerCount;
    for (size_t i = 0; i < count; ++i) {
        workers_.emplace_back([this] { worker_loop(); });
    }
}

AIVideoPipeline::~AIVideoPipeline() {
    shutdown();
}

std::string AIVideoPipeline::generate_scene_video(const std::string& prompt,
                                                  const std::string& audioPath,
                                                  const std::string& style,
                                                  const SceneVideoConfig& config) {
    if (!validate_prompt(prompt) || audioPath.empty()) {
        return {};
    }

    auto job = std::make_shared<JobContext>();
    job->id = "aip-" + std::to_string(idCounter_.fetch_add(1, std::memory_order_relaxed));
    job->prompt = prompt;
    job->audioFile = audioPath;
    job->style = style;
    job->config = config;

    {
        std::lock_guard lock(jobsMutex_);
        jobs_[job->id] = job;
    }

    StagePlan plan;
    plan.stage = StageType::PromptToVideo;
    plan.model = select_model_for_prompt(style);
    plan.tag = "scene-generation";
    plan.resolution = config.resolution;
    plan.frameCount = static_cast<size_t>(config.durationSeconds * config.framesPerSecond);
    plan.metadata["duration"] = std::to_string(config.durationSeconds);
    plan.metadata["fps"] = std::to_string(config.framesPerSecond);

    enqueue_task({job, plan});
    return job->id;
}

bool AIVideoPipeline::apply_style_transfer(const std::string& jobId, const StyleTransferConfig& config) {
    auto job = get_job(jobId);
    if (!job || !validate_prompt(config.stylePrompt)) {
        return false;
    }

    StagePlan plan;
    plan.stage = StageType::StyleTransfer;
    plan.model = ModelFamily::StyleTransfer;
    plan.tag = "style-transfer";
    plan.resolution = job->config.resolution;
    plan.metadata["strength"] = std::to_string(config.strength);
    plan.metadata["style_prompt"] = config.stylePrompt;
    plan.metadata["temporal"] = config.enableTemporalSmoothing ? "on" : "off";

    enqueue_task({job, plan});
    return true;
}

std::vector<FrameDescriptor> AIVideoPipeline::generate_frames(const std::string& jobId, size_t frameCount) {
    auto job = get_job(jobId);
    if (!job) {
        return {};
    }

    StagePlan plan;
    plan.stage = StageType::FrameGeneration;
    plan.model = ModelFamily::FrameInterpolator;
    plan.tag = "frame-generation";
    plan.resolution = job->config.resolution;
    plan.frameCount = frameCount == 0 ? job->config.framesPerSecond * 10 : frameCount;
    plan.metadata["fps"] = std::to_string(job->config.framesPerSecond);
    plan.metadata["interpolate"] = "true";

    enqueue_task({job, plan});
    return build_frame_descriptors(*job, plan);
}

bool AIVideoPipeline::compose_final_video(const std::string& jobId) {
    auto job = get_job(jobId);
    if (!job) {
        return false;
    }

    StagePlan plan;
    plan.stage = StageType::Composition;
    plan.model = ModelFamily::ImageToVideo;
    plan.tag = "final-composition";
    plan.resolution = job->config.resolution;
    plan.metadata["fps"] = std::to_string(job->config.framesPerSecond);
    plan.metadata["format"] = "mp4";
    plan.metadata["bitrate"] = "35M";

    enqueue_task({job, plan});
    return true;
}

std::shared_ptr<JobContext> AIVideoPipeline::get_job(const std::string& jobId) const {
    std::lock_guard lock(jobsMutex_);
    auto it = jobs_.find(jobId);
    if (it == jobs_.end()) {
        return nullptr;
    }
    return it->second;
}

void AIVideoPipeline::shutdown() {
    if (!running_.exchange(false)) {
        return;
    }
    taskQueue_.shutdown();
    for (auto& worker : workers_) {
        if (worker.joinable()) {
            worker.join();
        }
    }
}

void AIVideoPipeline::enqueue_task(JobTask task) {
    if (!task.job || !running_) {
        return;
    }
    taskQueue_.push(std::make_shared<JobTask>(std::move(task)));
}

void AIVideoPipeline::worker_loop() {
    while (running_) {
        auto task = taskQueue_.pop();
        if (!task) {
            if (!running_) {
                break;
            }
            continue;
        }

        {
            std::lock_guard lock(task->job->mutex);
            if (task->job->state == JobState::Failed || task->job->state == JobState::Completed) {
                continue;
            }
            task->job->state = JobState::Running;
        }

        StageResult result = execute_stage(*task);

        std::lock_guard lock(task->job->mutex);
        task->job->stageResults.push_back(result);
        if (!result.success) {
            task->job->state = JobState::Failed;
        } else if (result.stage == StageType::Composition) {
            task->job->state = JobState::Completed;
        }
    }
}

StageResult AIVideoPipeline::execute_stage(const JobTask& task) {
    StageResult result;
    result.stage = task.plan.stage;
    result.command = build_command(*task.job, task.plan);
    result.durationMs = result.command.estimatedDurationMs;
    result.artifactPath = build_artifact_path(*task.job, task.plan.stage);

    std::this_thread::sleep_for(std::chrono::milliseconds(2));

    switch (task.plan.stage) {
        case StageType::PromptToVideo:
            result.note = "Latent space seeded by prompt";
            break;
        case StageType::FrameGeneration: {
            auto frames = build_frame_descriptors(*task.job, task.plan);
            std::lock_guard lock(task.job->mutex);
            task.job->frames = frames;
            result.note = "Frames generated";

            if (task.plan.metadata.find("interpolate") != task.plan.metadata.end() &&
                task.plan.metadata.at("interpolate") == "true") {
                StagePlan interpPlan = task.plan;
                interpPlan.stage = StageType::FrameInterpolation;
                interpPlan.tag = "frame-interpolation";
                interpPlan.model = ModelFamily::FrameInterpolator;
                interpPlan.frameCount = task.plan.frameCount / 2;
                enqueue_task({task.job, interpPlan});
            }
            break;
        }
        case StageType::FrameInterpolation: {
            auto extra = build_frame_descriptors(*task.job, task.plan);
            std::lock_guard lock(task.job->mutex);
            int offset = static_cast<int>(task.job->frames.size());
            for (auto& frame : extra) {
                frame.index += offset;
                task.job->frames.push_back(frame);
            }
            result.note = "Interpolated frames appended";
            break;
        }
        case StageType::StyleTransfer: {
            std::lock_guard lock(task.job->mutex);
            task.job->style = task.plan.metadata.at("style_prompt");
            result.note = "Style transfer applied";
            break;
        }
        case StageType::Composition: {
            std::lock_guard lock(task.job->mutex);
            task.job->finalVideo = result.artifactPath;
            task.job->state = JobState::Completed;
            result.note = "Final MP4 assembled";
            break;
        }
        default:
            result.note = "Stage executed";
            break;
    }

    result.success = true;
    return result;
}

CommandSpec AIVideoPipeline::build_command(const JobContext& job, const StagePlan& plan) const {
    CommandSpec spec;
    spec.executable = std::string("camdra-ai-") + stage_name(plan.stage);
    spec.args = {"--model", model_name(plan.model),
                 "--prompt", job.prompt,
                 "--style", job.style,
                 "--width", std::to_string(plan.resolution.width),
                 "--height", std::to_string(plan.resolution.height)};

    if (plan.frameCount) {
        spec.args.push_back("--frames");
        spec.args.push_back(std::to_string(plan.frameCount));
    }

    for (const auto& [key, value] : plan.metadata) {
        spec.args.push_back("--" + key);
        spec.args.push_back(value);
    }

    spec.estimatedDurationMs = 10.0 + plan.frameCount * 0.5;
    return spec;
}

std::string AIVideoPipeline::build_artifact_path(const JobContext& job, StageType stage, size_t index) const {
    std::ostringstream oss;
    oss << job.id << "-" << stage_name(stage);

    if (stage == StageType::FrameGeneration || stage == StageType::FrameInterpolation) {
        oss << "-frame-" << index << ".png";
    } else if (stage == StageType::Composition) {
        oss << ".mp4";
    } else {
        oss << ".bin";
    }

    return oss.str();
}

ModelFamily AIVideoPipeline::select_model_for_prompt(const std::string& style) const {
    std::string lower = style;
    std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });

    if (lower.find("anime") != std::string::npos || lower.find("animate") != std::string::npos) {
        return ModelFamily::AnimateDiff;
    }

    if (lower.find("interpolate") != std::string::npos || lower.find("smooth") != std::string::npos) {
        return ModelFamily::FrameInterpolator;
    }

    return ModelFamily::StableVideoDiffusion;
}

bool AIVideoPipeline::validate_prompt(const std::string& prompt) const {
    return !prompt.empty() && prompt.size() <= 512;
}

std::vector<FrameDescriptor> AIVideoPipeline::build_frame_descriptors(const JobContext& job,
                                                                     const StagePlan& plan) const {
    std::vector<FrameDescriptor> frames;
    size_t count = std::max<size_t>(1, plan.frameCount);
    frames.reserve(count);
    double frameDurationMs = 1000.0 / std::max<size_t>(1, job.config.framesPerSecond);

    for (size_t index = 0; index < count; ++index) {
        FrameDescriptor descriptor;
        descriptor.index = static_cast<int>(index);
        descriptor.timestampMs = index * frameDurationMs;
        descriptor.path = build_artifact_path(job, plan.stage, index);
        descriptor.resolution = plan.resolution;
        frames.push_back(descriptor);
    }

    return frames;
}

} // namespace echoes::ai
