cmake_minimum_required(VERSION 3.22)
project(EchoesSileroKCoreContract LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

get_filename_component(ECHOES_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)

add_executable(echoes_silero_kcore_contract
  "${CMAKE_CURRENT_LIST_DIR}/silero_kcore_contract.cpp"
)

target_include_directories(echoes_silero_kcore_contract PRIVATE
  "${ECHOES_ROOT}/engine/include"
)

if(MSVC)
  target_compile_options(echoes_silero_kcore_contract PRIVATE /W4 /WX /permissive-)
  target_compile_definitions(echoes_silero_kcore_contract PRIVATE _CRT_SECURE_NO_WARNINGS NOMINMAX)
else()
  target_compile_options(echoes_silero_kcore_contract PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
