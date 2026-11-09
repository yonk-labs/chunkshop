//! HTML via a small tokenizer (no deps). Mirrors Python `HTMLParser`:
//! `BeautifulSoup(..).get_text(separator="\n", strip=True)` minus script/style (comments excluded).
use crate::ParseError;

fn named(name: &str) -> Option<&'static str> {
    let t = crate::html_entities::ENTITIES;
    t.binary_search_by(|(k, _)| (*k).cmp(name)).ok().map(|i| t[i].1)
}

/// Mirrors bs4/html.parser: `&name;` or `&name` + non-alnum (the `;` is consumed, any other
/// terminator is not); unknown names keep `&name` but still drop the `;`; bad numeric refs -> U+FFFD.
fn decode(s: &str) -> String {
    let (mut out, mut rest) = (String::with_capacity(s.len()), s);
    while let Some(i) = rest.find('&') {
        out.push_str(&rest[..i]);
        let b = &rest.as_bytes()[i + 1..];
        let (decoded, used): (Option<String>, usize) = match b.first() {
            Some(b'#') => {
                let hex = matches!(b.get(1), Some(b'x' | b'X'));
                let d = &b[1 + hex as usize..];
                let n = d.iter().take_while(|c| if hex { c.is_ascii_hexdigit() } else { c.is_ascii_digit() }).count();
                if n == 0 {
                    (None, 0)
                } else {
                    let code = d[..n].iter().fold(0u32, |a, &c| a.saturating_mul(if hex { 16 } else { 10 }).saturating_add((c as char).to_digit(16).unwrap()));
                    let ch = char::from_u32(code).filter(|&c| c != '\0').unwrap_or('\u{fffd}');
                    (Some(ch.to_string()), 1 + hex as usize + n + (d.get(n) == Some(&b';')) as usize)
                }
            }
            Some(c) if c.is_ascii_alphabetic() => {
                let n = b.iter().take_while(|c| c.is_ascii_alphanumeric() || **c == b'-' || **c == b'.').count();
                let name = &rest[i + 1..i + 1 + n];
                let text = named(name).map_or_else(|| format!("&{name}"), String::from);
                (Some(text), n + (b.get(n) == Some(&b';')) as usize)
            }
            _ => (None, 0),
        };
        match decoded {
            Some(t) => out.push_str(&t),
            None => out.push('&'),
        }
        rest = &rest[i + 1 + used..];
    }
    out.push_str(rest);
    out
}

/// Index just past the `>` closing the tag that starts at `s[0] == '<'`, honoring quoted attributes.
fn tag_end(s: &str) -> Option<usize> {
    let (mut quote, mut prev) = (None, ' ');
    for (i, c) in s.char_indices() {
        match (quote, c) {
            (Some(q), c) if c == q => quote = None,
            (Some(_), _) => {}
            // html.parser only treats a quote as opening an attribute value right after `=`
            (None, '"' | '\'') if prev == '=' => quote = Some(c),
            (None, '>') => return Some(i + 1),
            _ => {}
        }
        if !c.is_whitespace() {
            prev = c;
        }
    }
    None
}

pub(crate) fn parse(bytes: &[u8]) -> Result<String, ParseError> {
    let src = String::from_utf8_lossy(bytes);
    let (mut rest, mut nodes, mut text) = (&*src, Vec::<String>::new(), String::new());
    let flush = |text: &mut String, nodes: &mut Vec<String>| {
        let t = decode(text);
        let t = t.trim();
        if !t.is_empty() {
            nodes.push(t.to_string());
        }
        text.clear();
    };
    while let Some(i) = rest.find('<') {
        text.push_str(&rest[..i]);
        rest = &rest[i..];
        let next = rest[1..].chars().next();
        if rest.starts_with("<![CDATA[") {
            // bs4 keeps CDATA content as a text node, undecoded
            flush(&mut text, &mut nodes);
            let end = rest.find("]]>");
            let t = rest[9..end.unwrap_or(rest.len())].trim();
            if end.is_some() && !t.is_empty() {
                nodes.push(t.to_string());
            }
            rest = end.map_or("", |e| &rest[e + 3..]);
        } else if rest.starts_with("<!--") {
            flush(&mut text, &mut nodes);
            rest = rest.find("-->").map_or("", |e| &rest[e + 3..]);
        } else if matches!(next, Some(c) if c.is_ascii_alphabetic() || matches!(c, '/' | '!' | '?')) {
            let Some(end) = tag_end(rest) else {
                rest = ""; // unterminated tag at EOF is dropped, as bs4 does
                break;
            };
            let tag = &rest[1..end];
            let name: String = tag.chars().take_while(char::is_ascii_alphanumeric).collect::<String>().to_lowercase();
            rest = &rest[end..];
            flush(&mut text, &mut nodes);
            if (name == "script" || name == "style") && !tag.ends_with("/>") {
                let close = format!("</{name}");
                let at = rest.as_bytes().windows(close.len()).position(|w| w.eq_ignore_ascii_case(close.as_bytes()));
                rest = at.map_or("", |e| rest[e..].find('>').map_or("", |g| &rest[e + g + 1..]));
            }
        } else {
            text.push('<'); // stray `<` ("a < b") is text, as in html.parser
            rest = &rest[1..];
        }
    }
    text.push_str(rest);
    flush(&mut text, &mut nodes);
    Ok(nodes.join("\n"))
}
