add_library(EchoesRender STATIC
    RenderDevice.cpp
    RenderPipeline.cpp
    ShaderManager.cpp
    ParticleSystem.cpp
    LightingEngine.cpp
    SceneRenderer.cpp
)

target_include_directories(EchoesRender
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)

target_compile_features(EchoesRender PUBLIC cxx_std_20)
target_link_libraries(EchoesRender
  PUBLIC
    EchoesEngine::Audio
    EchoesEngine::Scene
    EchoesEngine::Camera
)

add_library(EchoesEngine::Render ALIAS EchoesRender)
