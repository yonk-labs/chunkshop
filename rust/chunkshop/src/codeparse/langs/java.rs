//! Java source-code symbol extractor via tree-sitter.
//!
//! Mirrors `python/src/chunkshop/codeparse/langs/java.py`. Captures class
//! defs, interface defs, and method defs (with parent class/interface for
//! the parent_name field).

use crate::codeparse::{build_fqn, Symbol};

/// Tree-sitter tags-style query for Java. Captures class defs, interface
/// defs, and method defs. Method `parent_name` comes from walking up to the
/// enclosing class_declaration / interface_declaration via
/// `enclosing_class_or_interface_name`.
const JAVA_TAGS_QUERY: &str = r#"
(class_declaration
  name: (identifier) @class.name) @class.def

(interface_declaration
  name: (identifier) @interface.name) @interface.def

(method_declaration
  name: (identifier) @method.name) @method.def
"#;

/// Extract symbols from Java source. Returns symbols with `fqn` +
/// `parent_name` set; `node_id` derivation is up to the caller (the chunker
/// stamps it onto chunk metadata).
pub fn extract_symbols(file_path: &str, source: &str) -> Vec<Symbol> {
    use tree_sitter::{Parser, Query, QueryCursor, StreamingIterator};

    let mut parser = Parser::new();
    let language = tree_sitter_java::LANGUAGE.into();
    if parser.set_language(&language).is_err() {
        return Vec::new();
    }
    let Some(tree) = parser.parse(source.as_bytes(), None) else {
        return Vec::new();
    };
    let root = tree.root_node();

    let Ok(query) = Query::new(&language, JAVA_TAGS_QUERY) else {
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
                "class.name" => ("class", None),
                "interface.name" => ("interface", None),
                "method.name" => {
                    let parent = enclosing_class_or_interface_name(node, source_bytes);
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

/// Returns `true` if tree-sitter's parse tree contains ERROR or MISSING nodes.
/// Mirror of Python's syntax-error check; consumed by the chunker fallback
/// path in Task 14.
pub fn has_syntax_errors(source: &str) -> bool {
    use tree_sitter::Parser;

    let mut parser = Parser::new();
    let language = tree_sitter_java::LANGUAGE.into();
    if parser.set_language(&language).is_err() {
        return true;
    }
    parser
        .parse(source.as_bytes(), None)
        .map(|tree| tree.root_node().has_error())
        .unwrap_or(true)
}

fn enclosing_class_or_interface_name(
    node: tree_sitter::Node,
    source: &[u8],
) -> Option<String> {
    let mut current = node.parent();
    while let Some(p) = current {
        let kind = p.kind();
        if kind == "class_declaration" || kind == "interface_declaration" {
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
    fn extracts_class_and_method() {
        let src = "public class Foo {\n    public void bar() {}\n}\n";
        let syms = extract_symbols("Foo.java", src);
        let names: Vec<&str> = syms.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"Foo"), "expected Foo in {names:?}");
        assert!(names.contains(&"bar"), "expected bar in {names:?}");

        let method_bar = syms.iter().find(|s| s.name == "bar").unwrap();
        assert_eq!(method_bar.symbol_type, "method");
        assert_eq!(method_bar.parent_name.as_deref(), Some("Foo"));
        assert_eq!(method_bar.fqn, "Foo.Foo.bar");
    }

    #[test]
    fn extracts_interface() {
        let src = "public interface Greeter {\n    void greet();\n}\n";
        let syms = extract_symbols("Greeter.java", src);
        let names: Vec<&str> = syms.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"Greeter"));
        let greet = syms.iter().find(|s| s.name == "greet").unwrap();
        assert_eq!(greet.parent_name.as_deref(), Some("Greeter"));
    }

    #[test]
    fn has_syntax_errors_detects_unbalanced() {
        assert!(has_syntax_errors("public class Foo { void bar(\n"));
    }
}
