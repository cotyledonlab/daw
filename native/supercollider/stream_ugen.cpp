// Diagnostic SC 3.14.1 bridge. Resources are prepared at plugin load,
// before the scsynth hardware driver starts; constructors only claim a lease.
#include "SC_PlugIn.h"
#include "stream_queue.hpp"
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static InterfaceTable* ft;
static daw_sc::Queue* queue;
struct DawStream : Unit { bool owns; };

static void DawStream_next(DawStream* unit, int count) {
    for (int i = 0; i < count; ++i) OUT(0)[i] = 0;
    if (!unit->owns || queue->fault.load(std::memory_order_relaxed)) return;
    if (count != int(daw_sc::frames)) {
        queue->fault.store(daw_sc::bad_unit, std::memory_order_release);
        return;
    }
    const float* left = IN(0);
    const float* right = IN(1);
    for (int i = 0; i < count; ++i) {
        if (!std::isfinite(left[i]) || !std::isfinite(right[i])) {
            queue->fault.store(daw_sc::nonfinite, std::memory_order_release);
            return;
        }
    }
    daw_sc::push(*queue, left, right);
}
static void DawStream_Ctor(DawStream* unit) {
    unit->owns = false;
    SETCALC(DawStream_next);
    for (int i = 0; i < unit->mBufLength; ++i) OUT(0)[i] = 0;
    if (!queue) return;
    if (unit->mNumInputs != 2 || unit->mNumOutputs != 1 || FULLRATE != 48000
        || FULLBUFLENGTH != int(daw_sc::frames) || INRATE(0) != calc_FullRate
        || INRATE(1) != calc_FullRate || unit->mCalcRate != calc_FullRate) {
        queue->fault.store(daw_sc::bad_unit, std::memory_order_release);
        return;
    }
    uint32_t available = 0;
    unit->owns = queue->producer.compare_exchange_strong(available, 1, std::memory_order_acq_rel);
    if (!unit->owns) queue->fault.store(daw_sc::duplicate_producer, std::memory_order_release);
}
static void DawStream_Dtor(DawStream* unit) {
    if (unit->owns) queue->producer.store(0, std::memory_order_release);
}

static bool prepare_queue() {
    const char* path = std::getenv("DAW_SC_STREAM_PATH");
    const char* token = std::getenv("DAW_SC_STREAM_NONCE");
    if (!path || path[0] != '/' || !token || !*token) return false;
    char* end = nullptr;
    errno = 0;
    const auto nonce = std::strtoull(token, &end, 10);
    if (errno || !end || *end || !nonce || token[0] == '-') return false;
    const int fd = open(path, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return false;
    struct stat status{};
    if (fstat(fd, &status) || !S_ISREG(status.st_mode) || status.st_uid != getuid()
        || (status.st_mode & 0777) != 0600 || status.st_size != sizeof(daw_sc::Queue)) {
        close(fd); return false;
    }
    void* mapping = mmap(nullptr, sizeof(daw_sc::Queue), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (mapping == MAP_FAILED) return false;
    auto* candidate = static_cast<daw_sc::Queue*>(mapping);
    if (!daw_sc::valid(*candidate, nonce) || candidate->written.load() || candidate->read.load()
        || candidate->fault.load() || candidate->producer.load()
        || candidate->consumer.load()
        || mlock(mapping, sizeof(daw_sc::Queue))) {
        munmap(mapping, sizeof(daw_sc::Queue)); return false;
    }
    // Fault in every payload page during startup, never from a unit callback.
    for (auto& block : candidate->samples) for (float& value : block) value = 0;
    queue = candidate;
    return true;
}
PluginLoad(DawStream) {
    ft = inTable;
    if (!prepare_queue()) {
        std::fprintf(stderr, "DawStream: invalid or unlocked shared queue; unit unavailable\n");
        return;
    }
    // Clearing this unused output must never alias (and erase) an input bus.
    DefineDtorCantAliasUnit(DawStream);
}
