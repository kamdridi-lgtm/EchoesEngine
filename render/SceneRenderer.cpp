#include "SceneRenderer.h"

namespace echoes::render {

SceneRenderer::SceneRenderer(RenderPipeline& pipeline)
    : pipeline_(pipeline) {
}

void SceneRenderer::scheduleScene(const SceneRenderData& data) {
    SceneRenderRequest request;
    request.sceneName = data.sceneName;
    request.scene = data.scene;
    request.camera = data.camera;
    request.audio = data.audio;
    request.target = data.target;
    request.frameIndex = data.frameIndex;
    request.timeSeconds = data.timeSeconds;
    pipeline_.submitScene(request);
}

void SceneRenderer::renderFrame() {
    pipeline_.tick();
}

} // namespace echoes::render
