#pragma once

#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>

namespace echoes::ai {

template <typename T>
class JobQueue {
public:
    void push(std::shared_ptr<T> job) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!running_) {
            return;
        }
        queue_.push_back(std::move(job));
        cv_.notify_one();
    }

    std::shared_ptr<T> pop() {
        std::unique_lock<std::mutex> lock(mutex_);
        cv_.wait(lock, [this] {
            return !queue_.empty() || !running_;
        });

        if (!queue_.empty()) {
            auto job = queue_.front();
            queue_.pop_front();
            return job;
        }

        return nullptr;
    }

    void shutdown() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            running_ = false;
        }
        cv_.notify_all();
    }

private:
    std::deque<std::shared_ptr<T>> queue_;
    std::mutex mutex_;
    std::condition_variable cv_;
    bool running_ = true;
};

} // namespace echoes::ai
