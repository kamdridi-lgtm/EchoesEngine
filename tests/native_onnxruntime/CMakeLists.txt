cmake_minimum_required(VERSION 3.22)

project(EchoesOnnxRuntimeInferenceContract LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT DEFINED ORT_ROOT)
  message(FATAL_ERROR "ORT_ROOT must point to the extracted Microsoft.ML.OnnxRuntime package")
endif()

get_filename_component(ECHOES_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(ORT_INCLUDE_DIR "${ORT_ROOT}/build/native/include")

if(WIN32)
  set(ORT_LIBRARY "${ORT_ROOT}/runtimes/win-x64/native/onnxruntime.lib")
  set(ORT_RUNTIME "${ORT_ROOT}/runtimes/win-x64/native/onnxruntime.dll")
else()
  set(ORT_LIBRARY "${ORT_ROOT}/runtimes/linux-x64/native/libonnxruntime.so")
  set(ORT_RUNTIME "${ORT_LIBRARY}")
endif()

foreach(required_path IN ITEMS "${ORT_INCLUDE_DIR}/onnxruntime_cxx_api.h" "${ORT_LIBRARY}" "${ORT_RUNTIME}")
  if(NOT EXISTS "${required_path}")
    message(FATAL_ERROR "Required ONNX Runtime file missing: ${required_path}")
  endif()
endforeach()

add_executable(echoes_onnxruntime_contract
  native_onnxruntime_contract.cpp
)

target_include_directories(echoes_onnxruntime_contract PRIVATE
  "${ECHOES_ROOT}/engine/include"
  "${ORT_INCLUDE_DIR}"
)

target_link_libraries(echoes_onnxruntime_contract PRIVATE "${ORT_LIBRARY}")
target_compile_definitions(echoes_onnxruntime_contract PRIVATE ECHOES_HAS_ONNXRUNTIME=1)

if(MSVC)
  target_compile_options(echoes_onnxruntime_contract PRIVATE /W4 /WX /EHsc)
  add_custom_command(TARGET echoes_onnxruntime_contract POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
      "${ORT_RUNTIME}"
      "$<TARGET_FILE_DIR:echoes_onnxruntime_contract>/onnxruntime.dll"
  )
else()
  target_compile_options(echoes_onnxruntime_contract PRIVATE -Wall -Wextra -Wpedantic -Werror)
  set_target_properties(echoes_onnxruntime_contract PROPERTIES
    BUILD_RPATH "${ORT_ROOT}/runtimes/linux-x64/native"
  )
endif()
