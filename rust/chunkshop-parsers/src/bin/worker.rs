//! Worker for `isolate` mode: `chunkshop-parsers-worker <ext> <mem_mib>`; file bytes on stdin.
//! Success: exit 0, stdout = `<parser>\n<text>`. Skip: exit 3, reason on stderr.
//! FeatureDisabled: exit 4, feature name on stderr. Anything else (abort, signal, timeout) is the
//! parent's problem: it reports a Skip, so a hostile file can crash only this process.
use std::alloc::{GlobalAlloc, Layout, System};
use std::io::{Read, Write};
use std::sync::atomic::{AtomicUsize, Ordering::Relaxed};

use chunkshop_parsers::{parse, ParseError};

static LIMIT: AtomicUsize = AtomicUsize::new(usize::MAX);
static USED: AtomicUsize = AtomicUsize::new(0);

/// Hard memory ceiling without libc/setrlimit: allocations past LIMIT fail, and Rust then aborts the
/// process (handle_alloc_error). Counts input + parser working set + output.
struct Capped;

// SAFETY: every method delegates to `System` with the caller's layout; the counters are bookkeeping only.
unsafe impl GlobalAlloc for Capped {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        if USED.fetch_add(l.size(), Relaxed) + l.size() > LIMIT.load(Relaxed) {
            USED.fetch_sub(l.size(), Relaxed);
            return std::ptr::null_mut();
        }
        let p = unsafe { System.alloc(l) };
        if p.is_null() {
            USED.fetch_sub(l.size(), Relaxed);
        }
        p
    }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        unsafe { System.dealloc(p, l) };
        USED.fetch_sub(l.size(), Relaxed);
    }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, new: usize) -> *mut u8 {
        if new > l.size() {
            let add = new - l.size();
            if USED.fetch_add(add, Relaxed) + add > LIMIT.load(Relaxed) {
                USED.fetch_sub(add, Relaxed);
                return std::ptr::null_mut();
            }
        } else {
            USED.fetch_sub(l.size() - new, Relaxed);
        }
        let q = unsafe { System.realloc(p, l, new) };
        if q.is_null() && new > l.size() {
            USED.fetch_sub(new - l.size(), Relaxed);
        }
        q
    }
}

#[global_allocator]
static A: Capped = Capped;

fn main() {
    let mut args = std::env::args().skip(1);
    let ext = args.next().unwrap_or_default();
    let mib: usize = args.next().and_then(|m| m.parse().ok()).unwrap_or(1024);
    LIMIT.store(mib << 20, Relaxed);
    let mut bytes = Vec::new();
    if std::io::stdin().lock().read_to_end(&mut bytes).is_err() {
        eprint!("worker: cannot read stdin");
        std::process::exit(3);
    }
    match parse(&ext, &bytes) {
        Ok(p) => {
            let mut out = std::io::stdout().lock();
            let _ = writeln!(out, "{}", p.parser);
            let _ = out.write_all(p.text.as_bytes());
        }
        Err(ParseError::Skip(m)) => {
            eprint!("{m}");
            std::process::exit(3);
        }
        Err(ParseError::FeatureDisabled { feature, .. }) => {
            eprint!("{feature}");
            std::process::exit(4);
        }
    }
}
