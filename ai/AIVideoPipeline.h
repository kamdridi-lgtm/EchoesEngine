#pragma once

#include "job_queue.h"

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

namespace echoes::ai {

enum class StageType {
    PromptToVideo,
    FrameGeneration,
    FrameInterpolation,
    StyleTransfer,
    ImageToVideo,
    Composition
};

enum class ModelFamily {
    StableVideoDiffusion,
    AnimateDiff,
    FrameInterpolator,
    StyleTransfer,
    ImageToVideo
};

struct Resolution {
    size_t width = 3840;
    size_t height = 2160;
};

struct SceneVideoConfig {
    Resolution resolution;
    double durationSeconds = 30.0;
    size_t framesPerSecond = 60;
};

struct StyleTransferConfig {
    double strength = 0.75;
    std::string stylePrompt = "cinematic";
    bool enableTemporalSmoothing = true;
};

struct CommandSpec {
    std::string executable;
    std::vector<std::string> args;
    double estimatedDurationMs = 0.0;
};

struct FrameDescriptor {
    int index = 0;
    double timestampMs = 0.0;
    std::string path;
    Resolution resolution;
};

enum class JobState {
    Pending,
    Running,
    Failed,
    Completed
};

struct StagePlan {
    StageType stage = StageType::PromptToVideo;
    ModelFamily model = ModelFamily::StableVideoDiffusion;
    std::string tag;
    Resolution resolution;
    size_t frameCount = 0;
    std::unordered_map<std::string, std::string> metadata;
};

struct StageResult {
    StageType stage = StageType::PromptToVideo;
    bool success = true;
    std::string artifactPath;
    CommandSpec command;
    double durationMs = 0.0;
    std::string note;
};

struct JobContext {
    std::string id;
    std::string prompt;
    std::string audioFile;
    std::string style;
    SceneVideoConfig config;
    JobState state = JobState::Pending;
    std::vector<StageResult> stageResults;
    std::vector<FrameDescriptor> frames;
    std::string finalVideo;
    mutable std::mutex mutex;
};

struct JobTask {
    std::shared_ptr<JobContext> job;
    StagePlan plan;
};

class AIVideoPipeline {
public:
    explicit AIVideoPipeline(size_t workerCount = 2);
    ~AIVideoPipeline();

    std::string generate_scene_video(const std::string& prompt,
                                     const std::string& audioPath,
                                     const std::string& style,
                                     const SceneVideoConfig& config = {});

    bool apply_style_transfer(const std::string& jobId, const StyleTransferConfig& config);

    std::vector<FrameDescriptor> generate_frames(const std::string& jobId, size_t frameCount = 60);

    bool compose_final_video(const std::string& jobId);

    std::shared_ptr<JobContext> get_job(const std::string& jobId) const;

    void shutdown();

private:
    void enqueue_task(JobTask task);
    void worker_loop();
    StageResult execute_stage(const JobTask& task);
    CommandSpec build_command(const JobContext& job, const StagePlan& plan) const;
    std::string build_artifact_path(const JobContext& job, StageType stage, size_t index = 0) const;
    ModelFamily select_model_for_prompt(const std::string& style) const;
    bool validate_prompt(const std::string& prompt) const;
    std::vector<FrameDescriptor> build_frame_descriptors(const JobContext& job, const StagePlan& plan) const;

    mutable std::mutex jobsMutex_;
    std::unordered_map<std::string, std::shared_ptr<JobContext>> jobs_;
    JobQueue<JobTask> taskQueue_;
    std::vector<std::thread> workers_;
    std::atomic<bool> running_ = true;
    std::atomic<uint64_t> idCounter_ = 1;
};

} // namespace echoes::ai
