//! Python source-code symbol extractor via tree-sitter.
//!
//! Mirrors `python/src/chunkshop/codeparse/langs/python.py` (the
//! `extract_symbols` function and the tree-sitter Query pattern at line
//! 170). Output `Symbol`s feed `SymbolAwareChunker` and have identical
//! `fqn` / `node_id` to Python's emission for the same input.

use crate::codeparse::{build_fqn, Symbol};

/// Tree-sitter tags-style query for Python. Captures function defs, class
/// defs, and method defs (functions nested inside classes).
const PYTHON_TAGS_QUERY: &str = r#"
(function_definition
  name: (identifier) @function.name) @function.def

(class_definition
  name: (identifier) @class.name) @class.def

(class_definition
  body: (block
    (function_definition
      name: (identifier) @method.name) @method.def))
"#;

/// Extract symbols from Python source. Returns symbols with `fqn` +
/// `parent_name` set; `node_id` derivation is up to the caller (the
/// chunker stamps it onto chunk metadata).
///
/// Note: tree-sitter is error-tolerant — it returns a (partial) tree for
/// malformed Python. The chunker layer is responsible for falling back to
/// `sentence_aware` via `has_syntax_errors` (see Task 14).
pub fn extract_symbols(file_path: &str, source: &str) -> Vec<Symbol> {
    use tree_sitter::{Parser, Query, QueryCursor, StreamingIterator};

    let mut parser = Parser::new();
    let language = tree_sitter_python::LANGUAGE.into();
    if parser.set_language(&language).is_err() {
        return Vec::new();
    }
    let Some(tree) = parser.parse(source.as_bytes(), None) else {
        return Vec::new();
    };
    let root = tree.root_node();

    let Ok(query) = Query::new(&language, PYTHON_TAGS_QUERY) else {
        return Vec::new();
    };
    let mut cursor = QueryCursor::new();

    let mut symbols: Vec<Symbol> = Vec::new();
    let source_bytes = source.as_bytes();

    let mut matches = cursor.matches(&query, root, source_bytes);
    while let Some(m) = matches.next() {
        for capture in m.captures {
            let capture_name = query.capture_names()[capture.index as usize];
            let node = capture.node;
            let name = node
                .utf8_text(source_bytes)
                .unwrap_or("")
                .to_string();

            let (symbol_type, parent_name) = match capture_name {
                "function.name" => {
                    // Skip if this is also a method (already captured below).
                    if is_inside_class(node) {
                        continue;
                    }
                    ("function", None)
                }
                "class.name" => ("class", None),
                "method.name" => {
                    let parent = enclosing_class_name(node, source_bytes);
                    ("method", parent)
                }
                _ => continue,
            };

            let fqn = build_fqn(file_path, &name, parent_name.as_deref());
            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;

            symbols.push(Symbol {
                name,
                fqn,
                symbol_type: symbol_type.to_string(),
                line_start,
                line_end,
                parent_name,
            });
        }
    }

    symbols
}

/// Returns `true` if tree-sitter's parse tree contains any ERROR or MISSING
/// nodes. Mirror of Python's `ast.parse(content)` SyntaxError check at
/// `python/src/chunkshop/chunkers/symbol_aware.py:120-132`. Used by the
/// chunker (Task 14) to trigger fallback to sentence_aware.
pub fn has_syntax_errors(source: &str) -> bool {
    use tree_sitter::Parser;

    let mut parser = Parser::new();
    let language = tree_sitter_python::LANGUAGE.into();
    if parser.set_language(&language).is_err() {
        return true;  // Can't parse → treat as error
    }
    parser
        .parse(source.as_bytes(), None)
        .map(|tree| tree.root_node().has_error())
        .unwrap_or(true)
}

fn is_inside_class(node: tree_sitter::Node) -> bool {
    let mut current = node.parent();
    while let Some(p) = current {
        if p.kind() == "class_definition" {
            return true;
        }
        current = p.parent();
    }
    false
}

fn enclosing_class_name(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    let mut current = node.parent();
    while let Some(p) = current {
        if p.kind() == "class_definition" {
            let name_node = p.child_by_field_name("name")?;
            return name_node.utf8_text(source).ok().map(String::from);
        }
        current = p.parent();
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_top_level_function() {
        let src = "def hello():\n    pass\n";
        let syms = extract_symbols("test.py", src);
        assert_eq!(syms.len(), 1);
        assert_eq!(syms[0].name, "hello");
        assert_eq!(syms[0].symbol_type, "function");
        assert_eq!(syms[0].parent_name, None);
        assert_eq!(syms[0].fqn, "test.hello");
    }

    #[test]
    fn extracts_class_and_methods() {
        let src = "class Foo:\n    def bar(self):\n        pass\n    def baz(self):\n        pass\n";
        let syms = extract_symbols("test.py", src);
        let names: Vec<&str> = syms.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"Foo"), "expected Foo in {names:?}");
        assert!(names.contains(&"bar"), "expected bar in {names:?}");
        assert!(names.contains(&"baz"), "expected baz in {names:?}");

        let method_bar = syms.iter().find(|s| s.name == "bar").unwrap();
        assert_eq!(method_bar.symbol_type, "method");
        assert_eq!(method_bar.parent_name.as_deref(), Some("Foo"));
        assert_eq!(method_bar.fqn, "test.Foo.bar");
    }

    #[test]
    fn empty_source_returns_no_symbols() {
        let syms = extract_symbols("empty.py", "");
        assert!(syms.is_empty());
    }

    #[test]
    fn has_syntax_errors_detects_unbalanced() {
        // Missing close paren — tree-sitter will produce ERROR nodes
        assert!(has_syntax_errors("def hello(\n    pass\n"));
    }

    #[test]
    fn has_syntax_errors_negative_on_valid() {
        assert!(!has_syntax_errors("def hello():\n    pass\n"));
    }
}
