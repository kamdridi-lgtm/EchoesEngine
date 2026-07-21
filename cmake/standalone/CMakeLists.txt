cmake_minimum_required(VERSION 3.22)

project(EchoesStandaloneCI VERSION 0.5.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

get_filename_component(ECHOES_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)

add_library(EchoesEngineCore STATIC
  "${ECHOES_ROOT}/engine/src/CoreAudioPipeline.cpp"
  "${ECHOES_ROOT}/engine/src/Engine.cpp"
  "${ECHOES_ROOT}/engine/opencl/opencl_context.cpp"
  "${ECHOES_ROOT}/engine/opencl/kernel_loader.cpp"
)

target_include_directories(EchoesEngineCore
  PUBLIC
    "${ECHOES_ROOT}/engine/include"
    "${ECHOES_ROOT}/engine"
  PRIVATE
    "${ECHOES_ROOT}/engine/src"
)

target_compile_definitions(EchoesEngineCore
  PRIVATE
    ECHOES_OPENCL_KERNELS_DIR="${ECHOES_ROOT}/engine/opencl/kernels"
    ECHOES_USE_OPENCL=0
)

if(MSVC)
  target_compile_options(EchoesEngineCore PRIVATE /W4 /WX- /arch:AVX2 /fp:fast)
  target_compile_definitions(EchoesEngineCore PRIVATE _CRT_SECURE_NO_WARNINGS NOMINMAX)
else()
  target_compile_options(EchoesEngineCore PRIVATE -Wall -Wextra)
endif()

add_executable(EchoesStandaloneCI
  "${ECHOES_ROOT}/app/standalone_processor.cpp"
)

target_link_libraries(EchoesStandaloneCI PRIVATE EchoesEngineCore)
set_target_properties(EchoesStandaloneCI PROPERTIES OUTPUT_NAME "EchoesEngine")

if(MSVC)
  target_compile_options(EchoesStandaloneCI PRIVATE /W3)
endif()

message(STATUS "EchoesEngine minimal standalone CI build configured")
message(STATUS "Source root: ${ECHOES_ROOT}")
