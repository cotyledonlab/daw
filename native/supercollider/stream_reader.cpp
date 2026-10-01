#include "stream_queue.hpp"

#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <new>
#include <string>
#include <signal.h>
#include <time.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

namespace {
using daw_sc::Queue;
constexpr uint32_t max_blocks = 1024;
constexpr useconds_t poll_us = 1000;
constexpr uint64_t deadline_us = 5'000'000;

bool parse_u64(const char* s, uint64_t& out) {
    if (!s || !*s || *s == '-') return false;
    errno = 0;
    char* end = nullptr;
    const unsigned long long n = std::strtoull(s, &end, 10);
    if (errno || !end || *end || n == 0) return false;
    out = static_cast<uint64_t>(n);
    return true;
}

bool parse_blocks(const char* s, uint32_t& out) {
    uint64_t value = 0;
    if (!parse_u64(s, value) || value > max_blocks) return false;
    out = static_cast<uint32_t>(value);
    return true;
}

uint64_t now_us() {
    timespec ts{};
    if (::clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return static_cast<uint64_t>(ts.tv_sec) * 1'000'000ULL +
           static_cast<uint64_t>(ts.tv_nsec) / 1'000ULL;
}

void report(const char* message) { std::fprintf(stderr, "%s: %s\n", message, std::strerror(errno)); }

bool unlink_if_owned_path(const char* path, int fd) {
    struct stat opened{};
    struct stat current{};
    if (::fstat(fd, &opened) != 0 || ::lstat(path, &current) != 0) return false;
    if (opened.st_uid != ::geteuid() || current.st_uid != ::geteuid() ||
        opened.st_dev != current.st_dev || opened.st_ino != current.st_ino) return false;
    return ::unlink(path) == 0;
}

int create_queue(const char* path, uint64_t nonce) {
    const int fd = ::open(path, O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd < 0) { report("open queue"); return 1; }
    bool ok = false;
    if (::fchmod(fd, 0600) != 0) {
        report("chmod queue");
        unlink_if_owned_path(path, fd);
        ::close(fd);
        return 1;
    }
    void* mapping = MAP_FAILED;
    if (::ftruncate(fd, static_cast<off_t>(sizeof(Queue))) != 0) {
        report("ftruncate queue");
    } else {
        mapping = ::mmap(nullptr, sizeof(Queue), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (mapping == MAP_FAILED) report("mmap queue");
        else {
            auto* queue = new (mapping) Queue;
            queue->nonce = nonce;
            if (::msync(mapping, sizeof(Queue), MS_SYNC) != 0) report("msync queue");
            else ok = true;
            // The initialized shared object persists for the owned server and
            // drainer; do not end its lifetime when this mapping is released.
            ::munmap(mapping, sizeof(Queue));
        }
    }
    if (!ok && !unlink_if_owned_path(path, fd))
        std::fprintf(stderr, "could not safely unlink failed queue\n");
    if (::close(fd) != 0) { report("close queue"); ok = false; }
    return ok ? 0 : 1;
}

bool owned_private_queue(int fd, struct stat& st) {
    if (::fstat(fd, &st) != 0) { report("fstat queue"); return false; }
    if (!S_ISREG(st.st_mode) || st.st_uid != ::geteuid() || (st.st_mode & 0777) != 0600 ||
        st.st_size != static_cast<off_t>(sizeof(Queue))) {
        std::fprintf(stderr, "queue must be an owned regular 0600 file of the exact ABI size\n");
        return false;
    }
    return true;
}

int drain_queue(const char* path, uint64_t nonce, uint32_t blocks, const char* output_path) {
    const int queue_fd = ::open(path, O_RDWR | O_NOFOLLOW);
    if (queue_fd < 0) { report("open queue"); return 1; }
    struct stat queue_stat{};
    if (!owned_private_queue(queue_fd, queue_stat)) { ::close(queue_fd); return 1; }
    void* mapping = ::mmap(nullptr, sizeof(Queue), PROT_READ | PROT_WRITE, MAP_SHARED, queue_fd, 0);
    if (mapping == MAP_FAILED) { report("mmap queue"); ::close(queue_fd); return 1; }
    auto* queue = static_cast<Queue*>(mapping);
    if (!daw_sc::valid(*queue, nonce)) {
        std::fprintf(stderr, "queue ABI or nonce mismatch\n");
        ::munmap(mapping, sizeof(Queue)); ::close(queue_fd); return 1;
    }
    uint32_t available = 0;
    if (!queue->consumer.compare_exchange_strong(available, 1, std::memory_order_acq_rel)) {
        std::fprintf(stderr, "queue already has a consumer\n");
        ::munmap(mapping, sizeof(Queue)); ::close(queue_fd); return 1;
    }
    const int out_fd = ::open(output_path, O_CREAT | O_EXCL | O_WRONLY, 0600);
    if (out_fd < 0) {
        report("open destination");
        queue->consumer.store(0, std::memory_order_release);
        ::munmap(mapping, sizeof(Queue)); ::close(queue_fd); return 1;
    }
    bool ok = ::fchmod(out_fd, 0600) == 0;
    if (!ok) report("chmod destination");
    uint32_t completed = 0;
    double peak = 0.0;
    float buffer[daw_sc::frames * daw_sc::channels];
    const uint64_t start = now_us();
    while (ok && completed < blocks) {
        if (queue->fault.load(std::memory_order_acquire) != daw_sc::none) {
            std::fprintf(stderr, "producer reported queue fault %u\n", queue->fault.load(std::memory_order_relaxed));
            ok = false; break;
        }
        if (daw_sc::pop(*queue, buffer)) {
            for (float sample : buffer) {
                if (!std::isfinite(sample)) {
                    std::fprintf(stderr, "nonfinite sample received\n"); ok = false; break;
                }
                const double magnitude = std::fabs(static_cast<double>(sample));
                if (magnitude > peak) peak = magnitude;
            }
            if (!ok) break;
            const auto bytes = static_cast<ssize_t>(sizeof(buffer));
            ssize_t written = 0;
            while (written < bytes) {
                const ssize_t n = ::write(out_fd, reinterpret_cast<const char*>(buffer) + written,
                                          static_cast<size_t>(bytes - written));
                if (n < 0 && errno == EINTR) continue;
                if (n <= 0) { report("write destination"); ok = false; break; }
                written += n;
            }
            if (!ok) break;
            ++completed;
        } else {
            if (now_us() - start >= deadline_us) {
                std::fprintf(stderr, "drain deadline exceeded after %u of %u blocks\n", completed, blocks);
                ok = false; break;
            }
            ::usleep(poll_us);
        }
    }
    if (ok && ::fsync(out_fd) != 0) { report("fsync destination"); ok = false; }
    if (!ok && !unlink_if_owned_path(output_path, out_fd))
        std::fprintf(stderr, "could not safely unlink failed destination\n");
    if (::close(out_fd) != 0) { report("close destination"); ok = false; }
    const uint32_t rate = queue->rate;
    queue->consumer.store(0, std::memory_order_release);
    ::munmap(mapping, sizeof(Queue));
    ::close(queue_fd);
    if (!ok) return 1;
    std::printf("{\"count\":%u,\"rate\":%u,\"peak\":%.9g}\n", completed, rate, peak);
    return 0;
}

float expected(uint32_t sequence, uint32_t frame, uint32_t channel) {
    return static_cast<float>(sequence) + static_cast<float>(frame) * 0.25F +
           static_cast<float>(channel) * 0.125F;
}

int self_test() {
    constexpr uint32_t count = 12'000;
    const uint64_t nonce = 0x9a3b7c51ULL;
    void* shared = ::mmap(nullptr, sizeof(Queue), PROT_READ | PROT_WRITE,
                          MAP_SHARED | MAP_ANON, -1, 0);
    if (shared == MAP_FAILED) { report("mmap selftest"); return 1; }
    auto* queue = new (shared) Queue;
    queue->nonce = nonce;
    constexpr uint32_t start_index = std::numeric_limits<uint32_t>::max() - 100;
    queue->written.store(start_index, std::memory_order_relaxed);
    queue->read.store(start_index, std::memory_order_relaxed);
    if (!daw_sc::valid(*queue, nonce) || daw_sc::valid(*queue, nonce + 1)) {
        std::fprintf(stderr, "validity/nonce selftest failed\n"); ::munmap(shared, sizeof(Queue)); return 1;
    }
    const pid_t child = ::fork();
    if (child < 0) { report("fork selftest"); ::munmap(shared, sizeof(Queue)); return 1; }
    if (child == 0) {
        float left[daw_sc::frames], right[daw_sc::frames];
        for (uint32_t seq = 0; seq < count; ++seq) {
            for (uint32_t frame = 0; frame < daw_sc::frames; ++frame) {
                left[frame] = expected(seq, frame, 0);
                right[frame] = expected(seq, frame, 1);
            }
            while (uint32_t(queue->written.load(std::memory_order_relaxed) -
                            queue->read.load(std::memory_order_acquire)) >= daw_sc::slots) {
                if (queue->fault.load(std::memory_order_acquire) != daw_sc::none) _exit(11);
                ::usleep(100);
            }
            if (!daw_sc::push(*queue, left, right)) _exit(12);
        }
        _exit(0);
    }
    const uint64_t started = now_us();
    float block[daw_sc::frames * daw_sc::channels];
    uint32_t received = 0;
    bool ok = true;
    int child_status = 0;
    bool reaped = false;
    while (received < count || !reaped) {
        if (received < count && daw_sc::pop(*queue, block)) {
            for (uint32_t frame = 0; frame < daw_sc::frames; ++frame) {
                if (block[2 * frame] != expected(received, frame, 0) ||
                    block[2 * frame + 1] != expected(received, frame, 1)) {
                    std::fprintf(stderr, "FIFO pattern mismatch at block %u frame %u\n", received, frame);
                    ok = false; break;
                }
            }
            if (!ok) break;
            ++received;
        }
        if (!reaped) {
            const pid_t result = ::waitpid(child, &child_status, WNOHANG);
            if (result == child) reaped = true;
            else if (result < 0 && errno != EINTR) { report("waitpid selftest"); ok = false; break; }
        }
        if (now_us() - started >= deadline_us) {
            std::fprintf(stderr, "selftest deadline exceeded\n"); ok = false; break;
        }
        if (received < count) ::usleep(100);
    }
    if (!ok && !reaped) {
        ::kill(child, SIGKILL);
        while (::waitpid(child, &child_status, 0) < 0 && errno == EINTR) {}
        reaped = true;
    }
    if (!reaped || !WIFEXITED(child_status) || WEXITSTATUS(child_status) != 0 || received != count) ok = false;
    ::munmap(shared, sizeof(Queue));
    if (!ok) { std::fprintf(stderr, "fork FIFO selftest failed\n"); return 1; }

    void* saturated_mem = ::mmap(nullptr, sizeof(Queue), PROT_READ | PROT_WRITE,
                                 MAP_SHARED | MAP_ANON, -1, 0);
    if (saturated_mem == MAP_FAILED) { report("mmap saturation selftest"); return 1; }
    auto* saturated = new (saturated_mem) Queue;
    float zeros[daw_sc::frames]{};
    for (uint32_t i = 0; i < daw_sc::slots; ++i)
        if (!daw_sc::push(*saturated, zeros, zeros)) { ok = false; break; }
    if (daw_sc::push(*saturated, zeros, zeros) ||
        saturated->fault.load(std::memory_order_acquire) != daw_sc::overflow) ok = false;
    saturated->~Queue(); ::munmap(saturated_mem, sizeof(Queue));
    if (!ok) { std::fprintf(stderr, "saturation overflow selftest failed\n"); return 1; }
    std::puts("{\"selftest\":\"ok\",\"blocks\":12000,\"wrap_start\":4294967195}");
    return 0;
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "self-test") == 0) return self_test();
    if (argc == 4 && std::strcmp(argv[1], "create") == 0) {
        uint64_t nonce = 0;
        if (!parse_u64(argv[3], nonce)) { std::fprintf(stderr, "nonce must be a nonzero uint64 decimal\n"); return 2; }
        return create_queue(argv[2], nonce);
    }
    if (argc == 6 && std::strcmp(argv[1], "drain") == 0) {
        uint64_t nonce = 0;
        uint32_t blocks = 0;
        if (!parse_u64(argv[3], nonce) || !parse_blocks(argv[4], blocks)) {
            std::fprintf(stderr, "nonce must be nonzero uint64 and blocks must be 1..%u\n", max_blocks);
            return 2;
        }
        return drain_queue(argv[2], nonce, blocks, argv[5]);
    }
    std::fprintf(stderr, "usage: %s create PATH NONCE | drain PATH NONCE BLOCKS DESTINATION | self-test\n", argv[0]);
    return 2;
}
