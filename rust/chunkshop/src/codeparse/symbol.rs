//! Stub — filled in Task 13.
#[derive(Debug, Clone)]
pub struct Symbol {
    pub name: String,
    pub fqn: String,
    pub symbol_type: String,
    pub line_start: u32,
    pub line_end: u32,
    pub parent_name: Option<String>,
}
