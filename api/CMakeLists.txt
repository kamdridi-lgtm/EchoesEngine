add_library(EchoesApi STATIC
    src/ApiServer.cpp
    src/GenerateModels.cpp
    src/Http.cpp
    src/JobRegistry.cpp
    src/Router.cpp
)

target_include_directories(EchoesApi
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(EchoesApi PUBLIC cxx_std_20)

add_library(EchoesEngine::Api ALIAS EchoesApi)
