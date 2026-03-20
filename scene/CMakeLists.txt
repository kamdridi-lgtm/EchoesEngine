add_library(EchoesScene STATIC
    src/SceneFactory.cpp
)

target_include_directories(EchoesScene
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(EchoesScene PUBLIC cxx_std_20)

add_library(EchoesEngine::Scene ALIAS EchoesScene)
