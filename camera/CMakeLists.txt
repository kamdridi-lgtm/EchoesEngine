add_library(EchoesCamera STATIC
    CameraDirector.cpp
)

target_include_directories(EchoesCamera
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)

target_compile_features(EchoesCamera PUBLIC cxx_std_20)

add_library(EchoesEngine::Camera ALIAS EchoesCamera)
