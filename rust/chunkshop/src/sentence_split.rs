//! Sentence splitting helpers for the semantic chunker.
//!
//! Mirrors `python/src/chunkshop/chunkers/_sentence_split.py`. Only the
//! `naive` splitter is implemented in Rust — `nltk` is Python-only.

/// Split text into sentences on terminator-then-whitespace boundaries.
/// Mirrors Python's regex `(?<=[.!?])\s+` (lookbehind keeps the terminator
/// attached to the preceding sentence). Rust's `regex` crate doesn't support
/// lookbehind, so this walks the byte sequence directly. ASCII terminator
/// scan + UTF-8-safe slicing.
pub fn naive_sentences(text: &str) -> Vec<String> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }
    let bytes = trimmed.as_bytes();
    let mut out: Vec<String> = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < bytes.len() {
        let b = bytes[i];
        if b == b'.' || b == b'!' || b == b'?' {
            let after_term = i + 1;
            // Walk over a run of whitespace bytes (ASCII whitespace only — fine
            // for this regex's `\s+` since Python's `\s` matches ASCII whitespace
            // by default and the corpus is mostly text).
            let mut j = after_term;
            while j < bytes.len() && (bytes[j] as char).is_whitespace() {
                j += 1;
            }
            if j > after_term {
                let sent = std::str::from_utf8(&bytes[start..after_term])
                    .expect("UTF-8 boundary safe at ASCII terminator")
                    .trim();
                if !sent.is_empty() {
                    out.push(sent.to_string());
                }
                start = j;
                i = j;
                continue;
            }
        }
        i += 1;
    }
    if start < bytes.len() {
        let tail = std::str::from_utf8(&bytes[start..])
            .expect("UTF-8 boundary safe — start is at split point")
            .trim();
        if !tail.is_empty() {
            out.push(tail.to_string());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_returns_empty() {
        assert!(naive_sentences("").is_empty());
        assert!(naive_sentences("   \n  ").is_empty());
    }

    #[test]
    fn single_sentence_no_terminator() {
        assert_eq!(
            naive_sentences("just words"),
            vec!["just words".to_string()]
        );
    }

    #[test]
    fn three_sentences_split() {
        assert_eq!(
            naive_sentences("Hello world. This is two. And three!"),
            vec![
                "Hello world.".to_string(),
                "This is two.".to_string(),
                "And three!".to_string()
            ]
        );
    }

    #[test]
    fn abbreviation_with_trailing_space_does_split() {
        // Python's `(?<=[.!?])\s+` matches any `.!?` followed by whitespace, so
        // abbreviations like "U.S.A." DO get split when followed by a space.
        // This is why the file's docstring calls the splitter "approximate" —
        // exact sentence identity isn't needed for the boundary signal.
        assert_eq!(
            naive_sentences("U.S.A. is here."),
            vec!["U.S.A.".to_string(), "is here.".to_string()]
        );
    }

    #[test]
    fn preserves_internal_punctuation() {
        assert_eq!(
            naive_sentences("Hello, world! How are you?"),
            vec!["Hello, world!".to_string(), "How are you?".to_string()]
        );
    }
}
