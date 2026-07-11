//! Sample Rust module for codeparse tests.
use std::fmt;

pub fn target(v: i32) -> i32 {
    v * 2
}

pub struct Calculator {
    pub base: i32,
}

pub trait Op {
    fn apply(&self, v: i32) -> i32;

    // A DEFAULT method with a body — real code with a call inside. Its call to
    // target() must attribute to a symbol we actually emit (no orphan edge).
    fn twice(&self, v: i32) -> i32 {
        target(v) + target(v)
    }
}

impl Calculator {
    pub fn add(&self, a: i32, b: i32) -> i32 {
        // A nested function that makes a call — the orphan-edge trigger.
        fn helper(x: i32) -> i32 {
            target(x)
        }
        helper(a + b)
    }
}
