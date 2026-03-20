add_library(EchoesEngineLib STATIC
    src/CoreAudioPipeline.cpp
    src/Engine.cpp
)

# Public include path — consumers use #include "EchoesEngine/Engine.h"
target_include_directories(EchoesEngineLib
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_compile_features(EchoesEngineLib PUBLIC cxx_std_20)

# Friendly alias used by app/
add_library(EchoesEngine::Engine ALIAS EchoesEngineLib)
