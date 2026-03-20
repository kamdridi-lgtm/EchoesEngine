#include "EchoesEngine/Core/engine.h"

#include <chrono>
#include <thread>
#include <utility>

namespace echoes::core {

Engine::Engine(Config config)
    : config_(std::move(config))
    , jobSystem_(config_.workerThreads)
    , context_{this, &jobSystem_, config_.targetFps} {
    if (config_.targetFps > 0.0) {
        frameDuration_ = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::duration<double>(1.0 / config_.targetFps));
    }
}

Engine::~Engine() {
    shutdown();
}

bool Engine::init() {
    if (initialized_.exchange(true)) {
        return true;
    }

    if (!jobSystem_.start()) {
        initialized_ = false;
        return false;
    }

    std::lock_guard lock(modulesMutex_);
    for (auto& module : modules_) {
        if (module) {
            module->onRegister(context_);
        }
    }

    return true;
}

void Engine::shutdown() {
    stop();
    jobSystem_.stop();
    initialized_ = false;
}

void Engine::registerModule(std::shared_ptr<Module> module) {
    if (!module) {
        return;
    }

    std::lock_guard lock(modulesMutex_);
    modules_.push_back(std::move(module));

    if (initialized_) {
        if (auto& registered = modules_.back()) {
            registered->onRegister(context_);
        }
    }
}

void Engine::run() {
    if (!initialized_) {
        return;
    }

    if (running_.exchange(true)) {
        return;
    }

    invokeModuleStart();
    auto lastTick = std::chrono::steady_clock::now();

    while (running_) {
        auto now = std::chrono::steady_clock::now();
        const double deltaSeconds = std::chrono::duration<double>(now - lastTick).count();
        lastTick = now;

        tick(deltaSeconds);

        if (frameDuration_.count() > 0) {
            const auto workDuration = std::chrono::steady_clock::now() - now;
            const auto sleepDuration =
                frameDuration_ - std::chrono::duration_cast<std::chrono::microseconds>(workDuration);
            if (sleepDuration.count() > 0) {
                std::this_thread::sleep_for(sleepDuration);
            }
        }
    }

    invokeModuleStop();
}

void Engine::stop() {
    running_ = false;
}

bool Engine::isRunning() const noexcept {
    return running_.load(std::memory_order_relaxed);
}

Engine::Config const& Engine::getConfig() const noexcept {
    return config_;
}

JobSystem& Engine::jobs() noexcept {
    return jobSystem_;
}

void Engine::tick(double deltaSeconds) {
    std::lock_guard lock(modulesMutex_);
    for (auto& module : modules_) {
        if (module) {
            module->update(deltaSeconds);
        }
    }
}

void Engine::invokeModuleStart() {
    std::lock_guard lock(modulesMutex_);
    for (auto& module : modules_) {
        if (module) {
            module->start();
        }
    }
}

void Engine::invokeModuleStop() {
    std::lock_guard lock(modulesMutex_);
    for (auto& module : modules_) {
        if (module) {
            module->stop();
        }
    }
}

} // namespace echoes::core
