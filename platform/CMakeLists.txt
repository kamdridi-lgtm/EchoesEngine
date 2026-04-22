add_library(EchoesPlatform STATIC
  src/AutonomousPlatform.cpp
)

target_include_directories(EchoesPlatform
  PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(EchoesPlatform
  PUBLIC
    EchoesEngine::Core
    EchoesEngine::Api
)

target_compile_features(EchoesPlatform PUBLIC cxx_std_20)

add_library(EchoesEngine::Platform ALIAS EchoesPlatform)
