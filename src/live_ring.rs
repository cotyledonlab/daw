//! Fixed-capacity SPSC transport for callback-owned live audio.
//!
//! Construct the handles before entering the callback. The producer and
//! consumer must each remain on a single thread for the lifetime of the ring.

use std::cell::UnsafeCell;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};

pub(crate) const CAPACITY: usize = 1024;

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Frame {
    pub(crate) audio: [f64; 2],
    pub(crate) timeline: u64,
}

struct Shared {
    frames: Box<[UnsafeCell<Frame>; CAPACITY]>,
    write: AtomicUsize,
    read: AtomicUsize,
    stop: AtomicBool,
    failed: AtomicBool,
    done: AtomicBool,
    underruns: AtomicU64,
}

// Each slot is written only by the producer when outside the unread interval,
// and read only by the consumer after observing the producer's Release store.
// The Release/Acquire index handoffs also ensure a reused slot is not written
// until the consumer has finished reading it.
unsafe impl Sync for Shared {}

pub(crate) struct Producer {
    shared: Arc<Shared>,
}

pub(crate) struct Consumer {
    shared: Arc<Shared>,
}

#[derive(Clone)]
pub(crate) struct Control {
    shared: Arc<Shared>,
}

pub(crate) fn pair() -> (Producer, Consumer, Control) {
    let frames = Box::new(std::array::from_fn(|_| {
        UnsafeCell::new(Frame {
            audio: [0.0; 2],
            timeline: 0,
        })
    }));
    let shared = Arc::new(Shared {
        frames,
        write: AtomicUsize::new(0),
        read: AtomicUsize::new(0),
        stop: AtomicBool::new(false),
        failed: AtomicBool::new(false),
        done: AtomicBool::new(false),
        underruns: AtomicU64::new(0),
    });
    (
        Producer {
            shared: Arc::clone(&shared),
        },
        Consumer {
            shared: Arc::clone(&shared),
        },
        Control { shared },
    )
}

impl Producer {
    pub(crate) fn push(&mut self, frame: Frame) -> Result<(), Frame> {
        let write = self.shared.write.load(Ordering::Relaxed);
        let read = self.shared.read.load(Ordering::Acquire);
        if write.wrapping_sub(read) >= CAPACITY {
            return Err(frame);
        }
        let slot = write % CAPACITY;
        // SAFETY: only the producer writes this slot, and the capacity check
        // guarantees it is not part of the consumer's unread interval.
        unsafe { *self.shared.frames[slot].get() = frame };
        self.shared
            .write
            .store(write.wrapping_add(1), Ordering::Release);
        Ok(())
    }

    pub(crate) fn stop_requested(&self) -> bool {
        self.shared.stop.load(Ordering::Acquire)
    }

    pub(crate) fn set_failed(&self) {
        self.shared.failed.store(true, Ordering::Release);
    }

    pub(crate) fn set_done(&self) {
        self.shared.done.store(true, Ordering::Release);
    }
}

impl Consumer {
    pub(crate) fn pop(&mut self) -> Option<Frame> {
        let read = self.shared.read.load(Ordering::Relaxed);
        let write = self.shared.write.load(Ordering::Acquire);
        if write.wrapping_sub(read) == 0 {
            return None;
        }
        let slot = read % CAPACITY;
        // SAFETY: Acquire observed publication of this frame; only the
        // consumer reads it, and its Release read-index update permits reuse.
        let frame = unsafe { *self.shared.frames[slot].get() };
        self.shared
            .read
            .store(read.wrapping_add(1), Ordering::Release);
        Some(frame)
    }

    #[cfg(test)]
    pub(crate) fn request_stop(&self) {
        self.shared.stop.store(true, Ordering::Release);
    }

    pub(crate) fn failed(&self) -> bool {
        self.shared.failed.load(Ordering::Acquire)
    }

    pub(crate) fn done(&self) -> bool {
        self.shared.done.load(Ordering::Acquire)
    }

    #[cfg(test)]
    pub(crate) fn queued_frames(&self) -> usize {
        queued(&self.shared)
    }

    #[cfg(test)]
    pub(crate) fn underruns(&self) -> u64 {
        self.shared.underruns.load(Ordering::Relaxed)
    }

    pub(crate) fn note_underrun(&self) {
        self.shared.underruns.fetch_add(1, Ordering::Relaxed);
    }
}

impl Control {
    pub(crate) fn request_stop(&self) {
        self.shared.stop.store(true, Ordering::Release);
    }

    #[cfg(test)]
    pub(crate) fn failed(&self) -> bool {
        self.shared.failed.load(Ordering::Acquire)
    }

    #[cfg(test)]
    pub(crate) fn done(&self) -> bool {
        self.shared.done.load(Ordering::Acquire)
    }

    pub(crate) fn underruns(&self) -> u64 {
        self.shared.underruns.load(Ordering::Relaxed)
    }

    #[cfg(test)]
    pub(crate) fn queued_frames(&self) -> usize {
        queued(&self.shared)
    }
}

#[cfg(test)]
fn queued(shared: &Shared) -> usize {
    let write = shared.write.load(Ordering::Acquire);
    let read = shared.read.load(Ordering::Acquire);
    write.wrapping_sub(read).min(CAPACITY)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;

    fn frame(timeline: u64) -> Frame {
        Frame {
            audio: [timeline as f64, -(timeline as f64)],
            timeline,
        }
    }

    #[test]
    fn ordered_frames_and_status_controls() {
        let (mut producer, mut consumer, control) = pair();
        for i in 0..CAPACITY as u64 {
            producer.push(frame(i)).unwrap();
        }
        assert_eq!(consumer.queued_frames(), CAPACITY);
        assert_eq!(producer.push(frame(9000)), Err(frame(9000)));
        for i in 0..CAPACITY as u64 {
            assert_eq!(consumer.pop(), Some(frame(i)));
        }
        assert_eq!(consumer.pop(), None);
        consumer.request_stop();
        assert!(producer.stop_requested());
        control.request_stop();
        assert!(producer.stop_requested());
        producer.set_failed();
        producer.set_done();
        assert!(control.failed());
        assert!(consumer.failed());
        assert!(control.done());
        assert!(consumer.done());
    }

    #[test]
    fn counters_wrap_without_losing_order_or_capacity() {
        let (mut producer, mut consumer, _) = pair();
        producer
            .shared
            .write
            .store(usize::MAX - 1, Ordering::Relaxed);
        producer
            .shared
            .read
            .store(usize::MAX - 1, Ordering::Relaxed);
        for i in 0..CAPACITY as u64 {
            producer.push(frame(i)).unwrap();
        }
        assert_eq!(producer.push(frame(5000)), Err(frame(5000)));
        for i in 0..CAPACITY as u64 {
            assert_eq!(consumer.pop(), Some(frame(i)));
        }
        assert_eq!(consumer.queued_frames(), 0);
    }

    #[test]
    fn producer_consumer_thread_race_preserves_each_frame_once() {
        const COUNT: u64 = 100_000;
        let (mut producer, mut consumer, control) = pair();
        let writer = thread::spawn(move || {
            for i in 0..COUNT {
                let mut pending = frame(i);
                loop {
                    match producer.push(pending) {
                        Ok(()) => break,
                        Err(frame) => {
                            pending = frame;
                            thread::yield_now();
                        }
                    }
                }
            }
            producer.set_done();
        });
        let reader = thread::spawn(move || {
            for expected in 0..COUNT {
                loop {
                    if let Some(actual) = consumer.pop() {
                        assert_eq!(actual, frame(expected));
                        break;
                    }
                    thread::yield_now();
                }
            }
        });
        writer.join().unwrap();
        reader.join().unwrap();
        assert!(control.done());
        assert_eq!(control.queued_frames(), 0);
    }

    #[test]
    fn underrun_counter_is_shared_and_monotonic() {
        let (_, consumer, control) = pair();
        consumer.note_underrun();
        consumer.note_underrun();
        assert_eq!(consumer.underruns(), 2);
        assert_eq!(control.underruns(), 2);
    }
}
