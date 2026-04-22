add_library(EchoesCore STATIC
    src/job_system.cpp
    src/module.cpp
    src/engine.cpp
)

target_include_directories(EchoesCore
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(EchoesCore PUBLIC cxx_std_20)

add_library(EchoesEngine::Core ALIAS EchoesCore)
