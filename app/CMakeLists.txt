# ── Test harness ──────────────────────────────────────────────────────────────
add_executable(EchoesTest main.cpp)
target_link_libraries(EchoesTest PRIVATE EchoesEngine::Engine)
target_compile_features(EchoesTest PRIVATE cxx_std_20)
set_target_properties(EchoesTest PROPERTIES OUTPUT_NAME "EchoesTest"
    VS_DEBUGGER_WORKING_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

# ── Standalone Processor (WAV → WAV, no DAW needed) ──────────────────────────
add_executable(EchoesStandalone standalone_processor.cpp)
target_link_libraries(EchoesStandalone PRIVATE EchoesEngine::Engine)
target_compile_features(EchoesStandalone PRIVATE cxx_std_20)
set_target_properties(EchoesStandalone PROPERTIES OUTPUT_NAME "EchoesEngine"
    VS_DEBUGGER_WORKING_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

message(STATUS "Targets: EchoesTest, EchoesEngine (standalone)")
message(STATUS "VST3 phase: add JUCE via CMake and target_link_libraries(... juce::juce_audio_processors)")
