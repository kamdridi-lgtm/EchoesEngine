cmake_minimum_required(VERSION 3.22)

project(EchoesPromptDirectorCI VERSION 0.3.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

get_filename_component(ECHOES_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)

add_library(EchoesPromptDirector STATIC
    "${ECHOES_ROOT}/ai_prompt/src/PromptDirector.cpp"
    "${ECHOES_ROOT}/ai_prompt/src/ShotPlanner.cpp"
    "${ECHOES_ROOT}/ai_prompt/src/ShotPlanJson.cpp"
)

target_include_directories(EchoesPromptDirector
    PUBLIC
        "${ECHOES_ROOT}/ai_prompt/include"
)

target_compile_features(EchoesPromptDirector PUBLIC cxx_std_20)

if(MSVC)
    target_compile_options(EchoesPromptDirector PRIVATE /W4 /WX-)
else()
    target_compile_options(EchoesPromptDirector PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_executable(PromptDirectorSmoke
    "${ECHOES_ROOT}/tests/prompt_director_smoke.cpp"
)

target_link_libraries(PromptDirectorSmoke PRIVATE EchoesPromptDirector)

add_executable(ShotPlannerSmoke
    "${ECHOES_ROOT}/tests/shot_planner_smoke.cpp"
)

target_link_libraries(ShotPlannerSmoke PRIVATE EchoesPromptDirector)

if(MSVC)
    target_compile_options(PromptDirectorSmoke PRIVATE /W4 /WX-)
    target_compile_options(ShotPlannerSmoke PRIVATE /W4 /WX-)
else()
    target_compile_options(PromptDirectorSmoke PRIVATE -Wall -Wextra -Wpedantic)
    target_compile_options(ShotPlannerSmoke PRIVATE -Wall -Wextra -Wpedantic)
endif()

enable_testing()
add_test(NAME PromptDirectorSmoke COMMAND PromptDirectorSmoke)
add_test(NAME ShotPlannerSmoke COMMAND ShotPlannerSmoke)
