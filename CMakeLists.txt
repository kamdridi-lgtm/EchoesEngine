# ═══════════════════════════════════════════════════════════════════════════════
# EchoesEngine — JUCE Plugin CMakeLists.txt
# Produces: VST3 · AU (Mac) · Standalone App
#
# Prerequisites:
#   1. JUCE 7.x   → https://github.com/juce-framework/JUCE
#   2. CMake 3.22+
#   3. MSVC 2022 (Windows) or Xcode 14+ (Mac)
#
# Build:
#   cmake -B build -DJUCE_PATH=/path/to/JUCE
#   cmake --build build --config Release
#
# Output:
#   build/EchoesPlugin_artefacts/VST3/EchoesEngine.vst3
#   build/EchoesPlugin_artefacts/Standalone/EchoesEngine.exe
# ═══════════════════════════════════════════════════════════════════════════════

cmake_minimum_required(VERSION 3.22)

project(EchoesEcosystem VERSION 0.3.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
option(USE_OPENCL "Enable OpenCL acceleration" OFF)

if(USE_OPENCL)
  set(ECHOES_OPENCL_VENDOR_DIR "${CMAKE_SOURCE_DIR}/engine/opencl/vendor")
  if(NOT DEFINED OpenCL_INCLUDE_DIR OR OpenCL_INCLUDE_DIR STREQUAL "")
    set(OpenCL_INCLUDE_DIR "${ECHOES_OPENCL_VENDOR_DIR}" CACHE PATH "OpenCL include directory" FORCE)
  endif()

  if(WIN32 AND (NOT DEFINED OpenCL_LIBRARY OR OpenCL_LIBRARY STREQUAL "" OR NOT EXISTS "${OpenCL_LIBRARY}"))
    if(NOT CMAKE_AR)
      message(FATAL_ERROR "CMAKE_AR is not set; cannot generate OpenCL import library on Windows")
    endif()

    set(ECHOES_OPENCL_IMPORT_LIB "${CMAKE_BINARY_DIR}/opencl/OpenCL.lib")
    file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/opencl")

    if(NOT EXISTS "${ECHOES_OPENCL_IMPORT_LIB}")
      file(TO_NATIVE_PATH "${CMAKE_SOURCE_DIR}/engine/opencl/vendor/OpenCL.def" ECHOES_OPENCL_DEF_FILE)
      file(TO_NATIVE_PATH "${ECHOES_OPENCL_IMPORT_LIB}" ECHOES_OPENCL_IMPORT_LIB_NATIVE)
      execute_process(
        COMMAND "${CMAKE_AR}"
                /def:${ECHOES_OPENCL_DEF_FILE}
                /machine:x64
                /out:${ECHOES_OPENCL_IMPORT_LIB_NATIVE}
        RESULT_VARIABLE ECHOES_OPENCL_LIB_RESULT
        OUTPUT_VARIABLE ECHOES_OPENCL_LIB_STDOUT
        ERROR_VARIABLE ECHOES_OPENCL_LIB_STDERR
      )

      if(NOT ECHOES_OPENCL_LIB_RESULT EQUAL 0)
        message(FATAL_ERROR
          "Failed to generate OpenCL import library.\n"
          "stdout:\n${ECHOES_OPENCL_LIB_STDOUT}\n"
          "stderr:\n${ECHOES_OPENCL_LIB_STDERR}")
      endif()
    endif()

    set(OpenCL_LIBRARY "${ECHOES_OPENCL_IMPORT_LIB}" CACHE FILEPATH "Generated OpenCL import library" FORCE)
  endif()

  find_package(OpenCL REQUIRED)
endif()

# ── JUCE path ──────────────────────────────────────────────────────────────────
# Pass via: cmake -DJUCE_PATH=/path/to/JUCE
if(NOT DEFINED JUCE_PATH)
  set(JUCE_PATH "${CMAKE_SOURCE_DIR}/JUCE" CACHE PATH "Path to JUCE framework")
endif()

if(EXISTS "${JUCE_PATH}/CMakeLists.txt")
  message(STATUS "JUCE found at: ${JUCE_PATH}")
  add_subdirectory("${JUCE_PATH}" JUCE EXCLUDE_FROM_ALL)
  set(HAS_JUCE TRUE)
else()
  message(WARNING
    "JUCE not found at ${JUCE_PATH}.\n"
    "Building EchoesEngine library + standalone only (no VST3).\n"
    "To build VST3: cmake -DJUCE_PATH=/path/to/JUCE .")
  set(HAS_JUCE FALSE)
endif()

# ── EchoesEngine Static Library ───────────────────────────────────────────────
add_library(EchoesEngine STATIC
  engine/src/CoreAudioPipeline.cpp
  engine/src/Engine.cpp
  engine/opencl/opencl_context.cpp
  engine/opencl/kernel_loader.cpp
)

target_include_directories(EchoesEngine
  PUBLIC  engine/include
          ${CMAKE_SOURCE_DIR}/engine
  PRIVATE engine/src
)

target_compile_features(EchoesEngine PUBLIC cxx_std_20)
target_compile_definitions(EchoesEngine PRIVATE
  ECHOES_OPENCL_KERNELS_DIR="${CMAKE_SOURCE_DIR}/engine/opencl/kernels"
)

if(USE_OPENCL)
  target_link_libraries(EchoesEngine PRIVATE OpenCL::OpenCL)
  target_compile_definitions(EchoesEngine PUBLIC ECHOES_USE_OPENCL=1)
else()
  target_compile_definitions(EchoesEngine PUBLIC ECHOES_USE_OPENCL=0)
endif()

# MSVC-specific
if(MSVC)
  # Flags communs aux deux configs
  target_compile_options(EchoesEngine PRIVATE
    /W4 /WX-          # Warnings: high, not as errors
    /arch:AVX2        # AVX2 SIMD pour le DSP
    /fp:fast          # Fast float (OK pour l'audio)
  )
  # Optimisation UNIQUEMENT en Release — jamais en Debug
  target_compile_options(EchoesEngine PRIVATE
    $<$<CONFIG:Release>:/O2>
    $<$<CONFIG:RelWithDebInfo>:/O2>
    $<$<CONFIG:Debug>:/Od /RTC1>   # Debug: pas d'optim, runtime checks
  )
  target_compile_definitions(EchoesEngine PRIVATE
    _CRT_SECURE_NO_WARNINGS
    NOMINMAX
  )
endif()

# Clang / GCC
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
  target_compile_options(EchoesEngine PRIVATE
    -Wall -Wextra
    -O3 -march=native
    -ffast-math
  )
endif()

# Export as a proper CMake target
add_library(EchoesEngine::Engine ALIAS EchoesEngine)

# —— ECHoesEngine PRO modules ————————————————————————————————————————————————————————
add_subdirectory(core)
add_subdirectory(audio)
add_subdirectory(scene)
add_subdirectory(camera)
add_subdirectory(render)
add_subdirectory(ai)
add_subdirectory(ai_prompt)
add_subdirectory(export)
add_subdirectory(api)
add_subdirectory(platform)

add_executable(ECHoesEnginePro
  main.cpp
)

target_link_libraries(ECHoesEnginePro PRIVATE
  EchoesEngine::Engine
  EchoesEngine::Core
  EchoesEngine::Audio
  EchoesEngine::Render
  EchoesEngine::Scene
  EchoesEngine::Camera
  EchoesEngine::AI
  EchoesEngine::PromptDirector
  EchoesEngine::Export
  EchoesEngine::Api
  EchoesEngine::Platform
)

target_compile_features(ECHoesEnginePro PRIVATE cxx_std_20)

if(MSVC)
  target_compile_options(ECHoesEnginePro PRIVATE
    /W3
    $<$<CONFIG:Release>:/O2>
    $<$<CONFIG:RelWithDebInfo>:/O2>
    $<$<CONFIG:Debug>:/Od>
  )
endif()

# ── Standalone Processor (no DAW, WAV→WAV) ────────────────────────────────────
add_executable(EchoesStandalone
  app/standalone_processor.cpp
)
target_link_libraries(EchoesStandalone PRIVATE EchoesEngine::Engine)
set_target_properties(EchoesStandalone PROPERTIES OUTPUT_NAME "EchoesEngine")

if(MSVC)
  target_compile_options(EchoesStandalone PRIVATE
    /W3
    $<$<CONFIG:Release>:/O2>
    $<$<CONFIG:RelWithDebInfo>:/O2>
    $<$<CONFIG:Debug>:/Od>
  )
endif()

# ── VST3 / AU Plugin (requires JUCE) ─────────────────────────────────────────
if(HAS_JUCE)

  juce_add_plugin(EchoesPlugin
    # ── Identity ────────────────────────────────────────────────────────────
    COMPANY_NAME              "Kam Dridi Audio"
    COMPANY_WEBSITE           "https://kamdridiaudio.com"
    COMPANY_EMAIL             "contact@kamdridiaudio.com"
    PLUGIN_MANUFACTURER_CODE  KDRA          # 4-char, unique to your company
    PLUGIN_CODE               ECHV          # 4-char, unique to this plugin
    PRODUCT_NAME              "EchoesEngine"
    VERSION                   "0.3.0"

    # ── Formats ─────────────────────────────────────────────────────────────
    FORMATS                   VST3 AU Standalone
    # AAX requires PACE/Avid license — add when ready:
    # FORMATS                 VST3 AU AAX Standalone

    # ── Plugin type ──────────────────────────────────────────────────────────
    IS_SYNTH                  FALSE
    NEEDS_MIDI_INPUT          FALSE
    NEEDS_MIDI_OUTPUT         FALSE
    IS_MIDI_EFFECT            FALSE
    EDITOR_WANTS_KEYBOARD_FOCUS FALSE

    # ── I/O ─────────────────────────────────────────────────────────────────
    PLUGIN_NAME               "EchoesEngine"

    # ── Category ─────────────────────────────────────────────────────────────
    VST3_CATEGORIES           "Fx|Dynamics|EQ"
    AU_MAIN_TYPE              "kAudioUnitType_Effect"

    # ── Description ─────────────────────────────────────────────────────────
    DESCRIPTION               "Hard Rock Vocal Processor — Gate · Comp · 5-band EQ · AI Voice"

    # ── Resources ────────────────────────────────────────────────────────────
    # ICON_BIG   resources/icon_512.png
    # ICON_SMALL resources/icon_64.png
  )

  # Source files for the JUCE plugin
  target_sources(EchoesPlugin PRIVATE
    # Plugin processor + editor
    plugin/EchoesPluginProcessor.cpp
    plugin/EchoesPluginEditor.cpp

    # All other sources come from the engine library
  )

  target_compile_features(EchoesPlugin PRIVATE cxx_std_20)

  target_include_directories(EchoesPlugin PRIVATE
    engine/include
    plugin/
  )

  target_link_libraries(EchoesPlugin
    PRIVATE
      EchoesEngine::Engine

      # JUCE modules
      juce::juce_audio_basics
      juce::juce_audio_devices
      juce::juce_audio_formats
      juce::juce_audio_plugin_client
      juce::juce_audio_processors
      juce::juce_audio_utils
      juce::juce_core
      juce::juce_data_structures
      juce::juce_dsp
      juce::juce_events
      juce::juce_graphics
      juce::juce_gui_basics
      juce::juce_gui_extra

    PUBLIC
      juce::juce_recommended_config_flags
      juce::juce_recommended_lto_flags
      juce::juce_recommended_warning_flags
  )

  # Preprocessor
  target_compile_definitions(EchoesPlugin
    PUBLIC
      JUCE_WEB_BROWSER=0
      JUCE_USE_CURL=0
      JUCE_VST3_CAN_REPLACE_VST2=0
      JUCE_DISPLAY_SPLASH_SCREEN=0
  )

  if(MSVC)
    target_compile_options(EchoesPlugin PRIVATE
      /W3 /arch:AVX2 /fp:fast
      $<$<CONFIG:Release>:/O2>
      $<$<CONFIG:RelWithDebInfo>:/O2>
      $<$<CONFIG:Debug>:/Od>
    )
  endif()

  message(STATUS "✓ VST3/AU/Standalone targets configured")
  message(STATUS "  Build: cmake --build . --config Release")
  message(STATUS "  Output: EchoesPlugin_artefacts/VST3/EchoesEngine.vst3")

else()
  message(STATUS "⚠  VST3 skipped — JUCE not found")
  message(STATUS "  Standalone 'EchoesEngine' still builds without JUCE")
endif()

# ── ONNX Runtime (optional, for AI voice) ────────────────────────────────────
option(ECHOES_WITH_ONNX "Build with ONNX Runtime for AI voice" OFF)

if(ECHOES_WITH_ONNX)
  find_package(onnxruntime QUIET)
  if(onnxruntime_FOUND)
    target_link_libraries(EchoesEngine PUBLIC onnxruntime::onnxruntime)
    target_compile_definitions(EchoesEngine PUBLIC ECHOES_HAS_ONNX=1)
    message(STATUS "✓ ONNX Runtime found — AI voice enabled")
  else()
    message(WARNING "ONNX Runtime not found — AI voice will use stub backend")
  endif()
endif()

# ── Summary ───────────────────────────────────────────────────────────────────
message(STATUS "")
message(STATUS "═══ EchoesEcosystem Build Summary ═══")
message(STATUS "  Engine library : EchoesEngine (static)")
message(STATUS "  Pro runtime    : ECHoesEnginePro")
message(STATUS "  Standalone app : EchoesEngine.exe (WAV→WAV)")
message(STATUS "  VST3 plugin    : ${HAS_JUCE}")
message(STATUS "  ONNX AI voice  : ${ECHOES_WITH_ONNX}")
message(STATUS "  OpenCL         : ${USE_OPENCL}")
message(STATUS "  C++ standard   : C++20")
message(STATUS "  Compiler       : ${CMAKE_CXX_COMPILER_ID}")
message(STATUS "═════════════════════════════════════")
