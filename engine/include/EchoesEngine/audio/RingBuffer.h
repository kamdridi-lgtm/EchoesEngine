#pragma once
#include <vector>
#include <atomic>
#include <cstdint>
#include <cstring>

namespace echoes {

/// Lock-free single-producer / single-consumer ring buffer for audio.
/// Safe to use between audio thread (producer) and DSP thread (consumer).
template<typename T = float>
class RingBuffer {
public:
    explicit RingBuffer(size_t capacity)
        : m_buffer(capacity + 1)   // +1 to distinguish full vs empty
        , m_capacity(capacity + 1)
        , m_readPos(0)
        , m_writePos(0)
    {}

    /// Push up to `count` samples. Returns number actually written.
    size_t write(const T* src, size_t count) noexcept {
        size_t available = writeAvailable();
        size_t n = std::min(count, available);
        size_t wp = m_writePos.load(std::memory_order_relaxed);

        size_t firstChunk = std::min(n, m_capacity - wp);
        std::memcpy(m_buffer.data() + wp, src, firstChunk * sizeof(T));

        if (n > firstChunk) {
            std::memcpy(m_buffer.data(), src + firstChunk, (n - firstChunk) * sizeof(T));
        }

        m_writePos.store((wp + n) % m_capacity, std::memory_order_release);
        return n;
    }

    /// Read up to `count` samples. Returns number actually read.
    size_t read(T* dst, size_t count) noexcept {
        size_t available = readAvailable();
        size_t n = std::min(count, available);
        size_t rp = m_readPos.load(std::memory_order_relaxed);

        size_t firstChunk = std::min(n, m_capacity - rp);
        std::memcpy(dst, m_buffer.data() + rp, firstChunk * sizeof(T));

        if (n > firstChunk) {
            std::memcpy(dst + firstChunk, m_buffer.data(), (n - firstChunk) * sizeof(T));
        }

        m_readPos.store((rp + n) % m_capacity, std::memory_order_release);
        return n;
    }

    [[nodiscard]] size_t readAvailable() const noexcept {
        size_t wp = m_writePos.load(std::memory_order_acquire);
        size_t rp = m_readPos.load(std::memory_order_relaxed);
        return (wp + m_capacity - rp) % m_capacity;
    }

    [[nodiscard]] size_t writeAvailable() const noexcept {
        return m_capacity - 1 - readAvailable();
    }

    void reset() noexcept {
        m_readPos.store(0, std::memory_order_release);
        m_writePos.store(0, std::memory_order_release);
    }

private:
    std::vector<T>     m_buffer;
    size_t             m_capacity;
    std::atomic<size_t> m_readPos;
    std::atomic<size_t> m_writePos;
};

} // namespace echoes
