#include "stream_queue.hpp"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <new>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {
struct QueueHandle {
    int fd = -1;
    daw_sc::Queue* queue = nullptr;
    bool owns_consumer = false;
};

void set_error(char* error, size_t capacity, const char* message) noexcept {
    if (error && capacity) {
        std::snprintf(error, capacity, "%s", message ? message : "unknown error");
        error[capacity - 1] = '\0';
    }
}

void release(QueueHandle* handle) noexcept {
    if (!handle) return;
    if (handle->queue) {
        if (handle->owns_consumer)
            handle->queue->consumer.store(0, std::memory_order_release);
        ::munmap(handle->queue, sizeof(daw_sc::Queue));
        handle->queue = nullptr;
    }
    if (handle->fd >= 0) {
        ::close(handle->fd);
        handle->fd = -1;
    }
    delete handle;
}
} // namespace

extern "C" void* daw_sc_queue_open(const char* absolute_path, uint64_t nonce,
                                   char* error, size_t capacity) noexcept {
    set_error(error, capacity, "invalid queue path or nonce");
    if (!absolute_path || absolute_path[0] != '/' || nonce == 0) return nullptr;
    QueueHandle* handle = nullptr;
    try {
        handle = new (std::nothrow) QueueHandle;
        if (!handle) { set_error(error, capacity, "out of memory opening queue"); return nullptr; }

        handle->fd = ::open(absolute_path, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
        if (handle->fd < 0) {
            set_error(error, capacity, "cannot open queue file");
            release(handle);
            return nullptr;
        }
        struct stat st{};
        if (::fstat(handle->fd, &st) != 0 || !S_ISREG(st.st_mode) ||
            st.st_uid != ::geteuid() || (st.st_mode & 0777) != 0600 ||
            st.st_size != static_cast<off_t>(sizeof(daw_sc::Queue))) {
            set_error(error, capacity, "queue must be an owned regular 0600 file of exact ABI size");
            release(handle);
            return nullptr;
        }
        void* mapping = ::mmap(nullptr, sizeof(daw_sc::Queue), PROT_READ | PROT_WRITE,
                               MAP_SHARED, handle->fd, 0);
        if (mapping == MAP_FAILED) {
            set_error(error, capacity, "cannot map queue file");
            release(handle);
            return nullptr;
        }
        handle->queue = static_cast<daw_sc::Queue*>(mapping);
        if (!daw_sc::valid(*handle->queue, nonce)) {
            set_error(error, capacity, "queue ABI or nonce mismatch");
            release(handle);
            return nullptr;
        }
        uint32_t expected = 0;
        if (!handle->queue->consumer.compare_exchange_strong(expected, 1,
                    std::memory_order_acq_rel, std::memory_order_acquire)) {
            set_error(error, capacity, "queue already has a consumer");
            release(handle);
            return nullptr;
        }
        handle->owns_consumer = true;
        set_error(error, capacity, "");
        return handle;
    } catch (...) {
        release(handle);
        set_error(error, capacity, "unexpected queue open failure");
        return nullptr;
    }
}

extern "C" int daw_sc_queue_pop(void* opaque, float* interleaved128) noexcept {
    auto* handle = static_cast<QueueHandle*>(opaque);
    if (!handle || !handle->queue || !handle->owns_consumer || !interleaved128) return -1;
    daw_sc::Queue& queue = *handle->queue;
    if (queue.fault.load(std::memory_order_acquire) != daw_sc::none) return -1;
    const uint32_t read = queue.read.load(std::memory_order_relaxed);
    const uint32_t written = queue.written.load(std::memory_order_acquire);
    const uint32_t distance = written - read;
    if (distance > daw_sc::slots) return -1;
    if (distance == 0) return 0;
    std::memcpy(interleaved128, queue.samples[read % daw_sc::slots],
                sizeof(float) * daw_sc::frames * daw_sc::channels);
    queue.read.store(read + 1, std::memory_order_release);
    return 1;
}

extern "C" uint32_t daw_sc_queue_fault(void* opaque) noexcept {
    const auto* handle = static_cast<const QueueHandle*>(opaque);
    if (!handle || !handle->queue) return UINT32_MAX;
    return handle->queue->fault.load(std::memory_order_acquire);
}

extern "C" void daw_sc_queue_close(void* opaque) noexcept {
    release(static_cast<QueueHandle*>(opaque));
}
