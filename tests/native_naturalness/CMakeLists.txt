cmake_minimum_required(VERSION 3.20)
project(EchoesNaturalnessContract LANGUAGES CXX)

add_executable(EchoesNaturalnessContract native_naturalness_contract.cpp)
target_compile_features(EchoesNaturalnessContract PRIVATE cxx_std_17)
target_include_directories(
    EchoesNaturalnessContract
    PRIVATE
        "${CMAKE_CURRENT_LIST_DIR}/../../engine/include"
)

if(MSVC)
    target_compile_options(EchoesNaturalnessContract PRIVATE /W4 /permissive-)
else()
    target_compile_options(EchoesNaturalnessContract PRIVATE -Wall -Wextra -Wpedantic)
endif()
