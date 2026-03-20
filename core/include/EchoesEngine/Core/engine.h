#pragma once

#include "EchoesEngine/Core/job_system.h"
#include "EchoesEngine/Core/module.h"

#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace echoes::core {

class Engine {
public:
    struct Config {
        std::string name = "ECHoesEngine PRO";
        std::size_t workerThreads = 0;
        double targetFps = 60.0;
    };

    explicit Engine(Config config = Config());
    ~Engine();

    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    bool init();
    void shutdown();

    void registerModule(std::shared_ptr<Module> module);
    void run();
    void stop();

    bool isRunning() const noexcept;
    Config const& getConfig() const noexcept;
    JobSystem& jobs() noexcept;

private:
    void tick(double deltaSeconds);
    void invokeModuleStart();
    void invokeModuleStop();

    Config config_;
    JobSystem jobSystem_;
    ModuleContext context_;
    std::vector<std::shared_ptr<Module>> modules_;
    std::mutex modulesMutex_;

    std::atomic<bool> running_{false};
    std::atomic<bool> initialized_{false};
    std::chrono::microseconds frameDuration_{0};
};

} // namespace echoes::core
