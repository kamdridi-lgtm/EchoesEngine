add_library(EchoesAI STATIC
    AIVideoPipeline.cpp
)

target_include_directories(EchoesAI
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)

target_compile_features(EchoesAI PUBLIC cxx_std_20)

add_library(EchoesEngine::AI ALIAS EchoesAI)
