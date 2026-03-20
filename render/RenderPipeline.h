#pragma once

#include "CameraMath.h"
#include "FrameBuffer.h"
#include "LightingEngine.h"
#include "ParticleSystem.h"
#include "RenderDevice.h"
#include "ShaderManager.h"
#include "EchoesEngine/audio/AudioAnalyzer.h"
#include "EchoesEngine/scene/SceneDescriptor.h"

#include <atomic>
#include <cstddef>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <thread>
#include <vector>

namespace echoes::render {

struct SceneRenderRequest {
    std::string sceneName;
    echoes::scene::SceneDescriptor scene;
    echoes::camera::CameraTransform camera;
    echoes::audio::RealtimeFeatures audio;
    FrameBufferDesc target;
    uint32_t frameIndex = 0;
    float timeSeconds = 0.0f;
};

struct RenderPass {
    std::string name;
    SceneRenderRequest request;
};

class JobQueue {
public:
    JobQueue();
    ~JobQueue();

    void start(std::size_t threads = 1);
    void stop();

    void enqueue(std::function<void()> job);

private:
    void worker();

    std::vector<std::thread> workers_;
    std::queue<std::function<void()>> jobs_;
    std::mutex mutex_;
    std::condition_variable cv_;
    bool running_ = false;
};

class RenderPipeline {
public:
    RenderPipeline(Backend backend);
    ~RenderPipeline();

    bool initialize();
    void shutdown();

    void submitScene(const SceneRenderRequest& request);
    void tick();

    const LightingEngine& lighting() const noexcept;
    LightingEngine& lighting() noexcept;

    ShaderManager& shaders();
    ParticleSystem& particles();

    std::string describe() const;
    bool renderToPixels(const SceneRenderRequest& request, std::vector<uint8_t>& outPixels);

private:
    void executePass(const RenderPass& pass);

    RenderDevice device_;
    ShaderManager shaderManager_;
    ParticleSystem particleSystem_;
    LightingEngine lighting_;
    JobQueue jobQueue_;
    std::vector<RenderPass> pendingPasses_;
    std::mutex pendingMutex_;
    std::atomic<bool> initialized_{false};
};

} // namespace echoes::render
