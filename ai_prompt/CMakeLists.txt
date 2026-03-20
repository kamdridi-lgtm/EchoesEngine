add_library(EchoesPrompt STATIC
    src/PromptDirector.cpp
)

target_include_directories(EchoesPrompt
    PUBLIC include
)

target_compile_features(EchoesPrompt PUBLIC cxx_std_20)

add_library(EchoesEngine::PromptDirector ALIAS EchoesPrompt)
