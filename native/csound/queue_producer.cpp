// Csound-owned worker producer for the existing fixed queue ABI.
#include "../supercollider/stream_queue.hpp"
#include <cmath>
#include <cstdio>
#include <fcntl.h>
#include <new>
#include <limits>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {
struct Producer {
    int fd = -1;
    daw_sc::Queue* queue = nullptr;
    bool leased = false;
};
void release(Producer* handle) noexcept {
    if (!handle) return;
    if (handle->queue) {
        if (handle->leased) handle->queue->producer.store(0, std::memory_order_release);
        ::munmap(handle->queue, sizeof(daw_sc::Queue));
    }
    if (handle->fd >= 0) ::close(handle->fd);
    delete handle;
}
void error(char* message, size_t capacity, const char* text) noexcept {
    if (message && capacity) std::snprintf(message, capacity, "%s", text);
}
}
extern "C" void* daw_cs_queue_open(const char* path, uint64_t nonce,
                                  char* message, size_t capacity) noexcept {
    error(message, capacity, "invalid producer path or nonce");
    if (!path || path[0] != '/' || !nonce) return nullptr;
    auto* handle = new (std::nothrow) Producer;
    if (!handle) return nullptr;
    handle->fd = ::open(path, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    struct stat st{};
    if (handle->fd < 0 || ::fstat(handle->fd, &st) || !S_ISREG(st.st_mode)
        || st.st_uid != ::geteuid() || (st.st_mode & 0777) != 0600
        || st.st_size != static_cast<off_t>(sizeof(daw_sc::Queue))) {
        error(message, capacity, "producer queue must be owned regular 0600 file of exact ABI size");
        release(handle); return nullptr;
    }
    void* mapping = ::mmap(nullptr, sizeof(daw_sc::Queue), PROT_READ | PROT_WRITE,
                          MAP_SHARED, handle->fd, 0);
    if (mapping == MAP_FAILED) { release(handle); return nullptr; }
    handle->queue = static_cast<daw_sc::Queue*>(mapping);
    if (!daw_sc::valid(*handle->queue, nonce)
        || handle->queue->fault.load(std::memory_order_acquire) != daw_sc::none) {
        error(message, capacity, "producer queue ABI, nonce or fault mismatch");
        release(handle); return nullptr;
    }
    uint32_t expected = 0;
    if (!handle->queue->producer.compare_exchange_strong(expected, 1,
            std::memory_order_acq_rel, std::memory_order_acquire)) {
        error(message, capacity, "queue already has a producer");
        release(handle); return nullptr;
    }
    handle->leased = true;
    error(message, capacity, "");
    return handle;
}
// Called only on the owning DSP worker. Full means wait/retry, never overwrite.
extern "C" int daw_cs_queue_push(void* opaque, const double* interleaved) noexcept {
    auto* handle = static_cast<Producer*>(opaque);
    if (!handle || !handle->leased || !interleaved) return -1;
    auto& queue = *handle->queue;
    if (queue.fault.load(std::memory_order_acquire) != daw_sc::none) return -1;
    const auto written = queue.written.load(std::memory_order_relaxed);
    const auto read = queue.read.load(std::memory_order_acquire);
    const auto distance = uint32_t(written - read);
    if (distance > daw_sc::slots) return -1;
    if (distance == daw_sc::slots) return 0;
    float block[daw_sc::frames * daw_sc::channels];
    for (uint32_t i = 0; i < daw_sc::frames * daw_sc::channels; ++i) {
        if (!std::isfinite(interleaved[i])
            || std::abs(interleaved[i]) > std::numeric_limits<float>::max()) return -1;
        block[i] = static_cast<float>(interleaved[i]);
        if (!std::isfinite(block[i])) return -1;
    }
    std::memcpy(queue.samples[written % daw_sc::slots], block, sizeof(block));
    queue.written.store(written + 1, std::memory_order_release);
    return 1;
}
extern "C" void daw_cs_queue_close(void* opaque) noexcept {
    release(static_cast<Producer*>(opaque));
}
