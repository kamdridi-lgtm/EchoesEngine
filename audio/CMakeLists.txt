add_library(EchoesAudio STATIC
    src/AudioAnalyzer.cpp
)

target_include_directories(EchoesAudio PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(EchoesAudio PUBLIC cxx_std_20)

add_library(EchoesEngine::Audio ALIAS EchoesAudio)
