#include "EchoesEngine/Core/job_system.h"

#include <algorithm>

namespace echoes::core {

namespace {
std::size_t normalizeWorkerCount(std::size_t count) {
    if (count == 0) {
        const std::size_t hardware = std::thread::hardware_concurrency();
        return std::max<std::size_t>(1, hardware == 0 ? 1 : hardware);
    }
    return count;
}
} // namespace

JobSystem::JobSystem(std::size_t workerCount) noexcept
    : targetWorkerCount_(normalizeWorkerCount(workerCount)) {}

JobSystem::~JobSystem() {
    stop();
}

bool JobSystem::start() {
    if (running_.exchange(true)) {
        return true;
    }

    workers_.reserve(targetWorkerCount_);
    for (std::size_t i = 0; i < targetWorkerCount_; ++i) {
        workers_.emplace_back(&JobSystem::workerLoop, this);
    }

    return true;
}

void JobSystem::stop() {
    if (!running_) {
        return;
    }

    running_ = false;
    queueCondition_.notify_all();
    waitForIdle();

    for (auto& worker : workers_) {
        if (worker.joinable()) {
            worker.join();
        }
    }
    workers_.clear();

    {
        std::lock_guard lock(queueMutex_);
        while (!queue_.empty()) {
            queue_.pop();
        }
    }
}

bool JobSystem::isRunning() const noexcept {
    return running_;
}

std::size_t JobSystem::workerCount() const noexcept {
    return targetWorkerCount_;
}

void JobSystem::waitForIdle() {
    std::unique_lock lock(idleMutex_);
    idleCondition_.wait(lock, [this]() {
        return pendingJobs_.load(std::memory_order_acquire) == 0;
    });
}

void JobSystem::workerLoop() {
    while (true) {
        TaskType task;
        {
            std::unique_lock lock(queueMutex_);
            queueCondition_.wait(lock, [this]() {
                return !queue_.empty() || !running_;
            });

            if (!running_ && queue_.empty()) {
                break;
            }

            if (!queue_.empty()) {
                task = std::move(queue_.front());
                queue_.pop();
            }
        }

        if (!task) {
            continue;
        }

        task();

        if (pendingJobs_.fetch_sub(1, std::memory_order_acq_rel) == 1) {
            std::lock_guard idleLock(idleMutex_);
            idleCondition_.notify_all();
        }
    }
}

} // namespace echoes::core
