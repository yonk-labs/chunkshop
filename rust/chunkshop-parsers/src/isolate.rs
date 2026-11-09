//! Optional process isolation (`isolate` feature): each parse runs in `chunkshop-parsers-worker`
//! with a timeout, an output cap and a hard memory ceiling, so a hostile PDF/Office file that
//! overflows the stack, aborts, hangs or exhausts memory costs one Skip, not the ingest run.
//! Spawns only our own worker binary; still no network.
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use crate::{Parsed, ParseError};

#[derive(Clone, Debug)]
pub struct Isolation {
    pub worker: PathBuf,
    pub timeout: Duration,
    /// Worker heap ceiling in MiB (input + working set + output).
    pub max_mem_mib: usize,
    pub max_output: usize,
}

impl Isolation {
    pub fn new(worker: impl Into<PathBuf>) -> Self {
        Self { worker: worker.into(), timeout: Duration::from_secs(120), max_mem_mib: 1024, max_output: 256 << 20 }
    }

    /// Find `chunkshop-parsers-worker` next to the running executable (or one level up, as in
    /// `target/debug/deps/<test>`).
    pub fn locate() -> Option<Self> {
        let exe = std::env::current_exe().ok()?;
        let name = format!("chunkshop-parsers-worker{}", std::env::consts::EXE_SUFFIX);
        exe.ancestors().skip(1).take(2).map(|d| d.join(&name)).find(|p| p.is_file()).map(Self::new)
    }
}

/// Formats routed through the worker (everything with a third-party or binary parser).
pub(crate) fn needs_isolation(ext: &str) -> bool {
    matches!(ext, "pdf" | "xlsx" | "xls" | "xlsb" | "ods" | "doc" | "ppt" | "docx" | "pptx" | "odt" | "odp")
}

fn skip(ext: &str, why: impl std::fmt::Display) -> ParseError {
    ParseError::Skip(format!("isolated .{ext} parse failed: {why}"))
}

pub fn parse_isolated(ext: &str, bytes: &[u8], iso: &Isolation) -> Result<Parsed, ParseError> {
    let ext = ext.to_lowercase();
    let mut child = Command::new(&iso.worker)
        .arg(&ext)
        .arg(iso.max_mem_mib.to_string())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| skip(&ext, format!("cannot start worker {}: {e}", iso.worker.display())))?;

    let mut stdin = child.stdin.take().unwrap();
    let (mut stdout, mut stderr) = (child.stdout.take().unwrap(), child.stderr.take().unwrap());
    let cap = iso.max_output;
    // Detached threads + channels: if a killed worker leaves a grandchild holding the pipes open,
    // we stop waiting after a grace period instead of hanging on `join`.
    let input = bytes.to_vec();
    std::thread::spawn(move || {
        let _ = stdin.write_all(&input); // worker may exit early; ignore broken pipe
    });
    let (otx, orx) = std::sync::mpsc::channel();
    let (etx, erx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut b = Vec::new();
        let _ = (&mut stdout).take(cap as u64 + 1).read_to_end(&mut b);
        let _ = otx.send(b);
    });
    std::thread::spawn(move || {
        let mut b = Vec::new();
        let _ = (&mut stderr).take(4096).read_to_end(&mut b);
        let _ = etx.send(b);
    });
    let deadline = Instant::now() + iso.timeout;
    let over = loop {
        match child.try_wait() {
            Ok(Some(st)) => break Some(st),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(5)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };
    let grace = Duration::from_secs(2);
    let out = orx.recv_timeout(grace).unwrap_or_default();
    let err = erx.recv_timeout(grace).unwrap_or_default();

    let Some(status) = over else {
        return Err(skip(&ext, format!("timed out after {:?}", iso.timeout)));
    };
    match status.code() {
        Some(0) if out.len() <= cap => {
            let s = String::from_utf8(out).map_err(|e| skip(&ext, e))?;
            let (name, text) = s.split_once('\n').ok_or_else(|| skip(&ext, "malformed worker output"))?;
            Ok(Parsed { text: text.to_string(), parser: crate::intern_parser(name) })
        }
        Some(0) => Err(skip(&ext, format!("output exceeds {cap} bytes"))),
        Some(3) => Err(ParseError::Skip(String::from_utf8_lossy(&err).into_owned())),
        Some(4) => Err(ParseError::FeatureDisabled {
            ext: ext.clone(),
            feature: crate::intern_feature(&String::from_utf8_lossy(&err)),
        }),
        // killed by a signal (SIGABRT from the memory cap, SIGSEGV from stack overflow, ...) or other exit
        other => Err(skip(&ext, format!("worker died ({})", other.map_or("signal".into(), |c| format!("exit {c}"))))),
    }
}
