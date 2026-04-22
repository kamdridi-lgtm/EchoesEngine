#pragma once

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <functional>
#include <memory>
#include <future>
#include <mutex>
#include <queue>
#include <thread>
#include <vector>
#include <type_traits>

namespace echoes::core {

class JobSystem {
public:
    explicit JobSystem(std::size_t workerCount = 0) noexcept;
    ~JobSystem();
    JobSystem(const JobSystem&) = delete;
    JobSystem& operator=(const JobSystem&) = delete;

    bool start();
    void stop();
    bool isRunning() const noexcept;
    std::size_t workerCount() const noexcept;

    template <typename Func, typename... Args>
    auto enqueue(Func&& func, Args&&... args);

    void waitForIdle();

private:
    using TaskType = std::function<void()>;

    void workerLoop();

    std::vector<std::thread> workers_;
    std::queue<TaskType> queue_;
    std::mutex queueMutex_;
    std::condition_variable queueCondition_;

    std::mutex idleMutex_;
    std::condition_variable idleCondition_;

    std::atomic<bool> running_{false};
    std::atomic<std::size_t> pendingJobs_{0};
    std::size_t targetWorkerCount_{0};
};

template <typename Func, typename... Args>
auto JobSystem::enqueue(Func&& func, Args&&... args) {
    using ResultType = std::invoke_result_t<Func, Args...>;
    auto task = std::make_shared<std::packaged_task<ResultType()>>(
        std::bind(std::forward<Func>(func), std::forward<Args>(args)...));

    auto future = task->get_future();

    {
        std::lock_guard lock(queueMutex_);
        queue_.emplace([task]() { (*task)(); });
        pendingJobs_.fetch_add(1, std::memory_order_relaxed);
    }

    queueCondition_.notify_one();
    return future;
}

} // namespace echoes::core
