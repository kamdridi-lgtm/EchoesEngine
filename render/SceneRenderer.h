#pragma once

#include "RenderPipeline.h"

namespace echoes::render {

struct SceneRenderData {
    std::string sceneName;
    echoes::scene::SceneDescriptor scene;
    echoes::camera::CameraTransform camera;
    echoes::audio::RealtimeFeatures audio;
    FrameBufferDesc target;
    uint32_t frameIndex = 0;
    float timeSeconds = 0.0f;
};

class SceneRenderer {
public:
    explicit SceneRenderer(RenderPipeline& pipeline);

    void scheduleScene(const SceneRenderData& data);
    void renderFrame();

private:
    RenderPipeline& pipeline_;
};

} // namespace echoes::render
