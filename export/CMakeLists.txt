add_library(EchoesExport STATIC
    src/FrameCapture.cpp
    src/FFmpegPlan.cpp
    src/ExportJob.cpp
    src/VideoExporter.cpp
)

target_include_directories(EchoesExport
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(EchoesExport PUBLIC cxx_std_20)

add_library(EchoesEngine::Export ALIAS EchoesExport)
