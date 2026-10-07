// Diagnostic ABI v1, tested only on macOS arm64. Not a product session contract.
#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <type_traits>

namespace daw_sc {
constexpr uint32_t magic = 0x44534331;
constexpr uint32_t slots = 64;
constexpr uint32_t frames = 64;
constexpr uint32_t channels = 2;
enum Fault : uint32_t { none = 0, overflow = 1, bad_unit = 2, duplicate_producer = 3, nonfinite = 4 };
static_assert(std::atomic<uint32_t>::is_always_lock_free);
struct alignas(64) Queue {
    uint32_t signature = magic, version = 1, rate = 48000, block = frames;
    uint32_t channel_count = channels, capacity = slots;
    uint64_t nonce = 0;
    alignas(64) std::atomic<uint32_t> written{0};
    std::atomic<uint32_t> fault{none};
    std::atomic<uint32_t> producer{0};
    alignas(64) std::atomic<uint32_t> read{0};
    std::atomic<uint32_t> consumer{0};
    alignas(64) float samples[slots][frames * channels]{};
};
static_assert(std::is_standard_layout_v<Queue>);
static_assert(offsetof(Queue, written) == 64 && offsetof(Queue, read) == 128);
static_assert(offsetof(Queue, samples) == 192);
inline bool valid(const Queue& q, uint64_t nonce) {
    return q.signature == magic && q.version == 1 && q.rate == 48000 && q.block == frames
        && q.channel_count == channels && q.capacity == slots && q.nonce == nonce && nonce != 0;
}
// The caller owns the producer/consumer lease. Unsigned subtraction permits
// index wraparound; acquire/release pairs publish samples and reclaim slots.
inline bool push(Queue& q, const float* left, const float* right) {
    if (q.fault.load(std::memory_order_relaxed) != none) return false;
    const auto write = q.written.load(std::memory_order_relaxed);
    const auto read = q.read.load(std::memory_order_acquire);
    if (uint32_t(write - read) >= slots) {
        q.fault.store(overflow, std::memory_order_release);
        return false;
    }
    auto* dest = q.samples[write % slots];
    for (uint32_t i = 0; i < frames; ++i) {
        dest[2 * i] = left[i]; dest[2 * i + 1] = right[i];
    }
    q.written.store(write + 1, std::memory_order_release);
    return true;
}
inline bool pop(Queue& q, float* dest) {
    const auto read = q.read.load(std::memory_order_relaxed);
    if (q.written.load(std::memory_order_acquire) == read) return false;
    std::memcpy(dest, q.samples[read % slots], sizeof(float) * frames * channels);
    q.read.store(read + 1, std::memory_order_release);
    return true;
}
} // namespace daw_sc
