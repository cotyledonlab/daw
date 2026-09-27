//! Counts heap activity on this test thread only; engine preparation is excluded.
use daw::{
    audio_buffer::PlaybackBuffer,
    engine::Engine,
    session::{Device, Session, Track},
};
use std::{
    alloc::{GlobalAlloc, Layout, System},
    cell::Cell,
};

struct CountedAllocator;
thread_local! {
    static WATCH: Cell<bool> = const { Cell::new(false) };
    static OPERATIONS: Cell<usize> = const { Cell::new(0) };
}

#[test]
fn device_buffer_adapter_does_not_allocate_or_free() {
    let session = Session {
        tracks: vec![Track {
            id: "tone".into(),
            device: Device::Sine {
                frequency_hz: 440.0,
                gain: 0.1,
            },
        }],
        ..Session::default()
    };
    let mut playback = PlaybackBuffer::prepare(&session, 44_100, 2, 1.0, 0.25).unwrap();
    let mut output = [0.0_f32; 1024];
    OPERATIONS.set(0);
    WATCH.set(true);
    for _ in 0..100 {
        let _ = std::hint::black_box(playback.fill(&mut output, |x| x as f32));
    }
    WATCH.set(false);
    assert_eq!(OPERATIONS.get(), 0);
    assert_eq!(playback.remaining_frames(), 0);
    assert!(output.iter().all(|&sample| sample == 0.0));
}
fn count() {
    if WATCH.try_with(Cell::get).unwrap_or(false) {
        let _ = OPERATIONS.try_with(|count| count.set(count.get() + 1));
    }
}
// SAFETY: every operation delegates its original pointer/layout to System.
unsafe impl GlobalAlloc for CountedAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc(layout) }
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc_zeroed(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        count();
        unsafe { System.dealloc(ptr, layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        count();
        unsafe { System.realloc(ptr, layout, size) }
    }
}
#[global_allocator]
static ALLOCATOR: CountedAllocator = CountedAllocator;

#[test]
fn prepared_render_blocks_do_not_allocate_or_free() {
    let session = Session {
        tracks: (0..64)
            .map(|i| Track {
                id: i.to_string(),
                device: Device::Sine {
                    frequency_hz: 100.0 + i as f64,
                    gain: 0.01,
                },
            })
            .collect(),
        ..Session::default()
    };
    let mut engine = Engine::prepare(&session).unwrap();
    let mut output = [[0.0; 2]; 257];
    OPERATIONS.set(0);
    WATCH.set(true);
    for _ in 0..100 {
        std::hint::black_box(engine.render_block(&mut output));
    }
    WATCH.set(false);
    assert_eq!(OPERATIONS.get(), 0, "rendering performed heap operations");
    assert!(output.iter().any(|frame| frame[0] != 0.0));
}
