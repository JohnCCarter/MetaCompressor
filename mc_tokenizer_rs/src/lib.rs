// Native tokenizer for MetaCompressor template extraction.
//
// Mirrors the EXACT semantics of `_scan_text_line` and
// `_normalized_skeleton` in `metacompressor/corpus_template.py` so
// archive bytes are byte-identical between the Python and Rust paths.
//
// Provides three Python-facing functions:
//   - scan_line(line) -> (parts, values, kinds)
//       Mirrors _find_next_variable + the _scan_text_line walk.
//   - normalize_text_part(part) -> String
//       Mirrors _normalize_text_part (11 regex .sub passes).
//   - analyze_text(line) -> (parts, values, kinds, skeleton)
//       Combined: scan + normalized_skeleton in one FFI call.
//
// The win over Python comes from each individual regex call being
// C-fast (Rust regex crate uses DFA/lazy DFA), and from doing
// scan + skeleton in one FFI call to amortise the boundary cost.

use once_cell::sync::Lazy;
use pyo3::prelude::*;
use regex::Regex;

// ---------- Pattern definitions (mirror corpus_template.py) ----------

const KIND_NAMES: &[&str] = &[
    "timestamp", "uuid", "url", "query", "email", "ipv4", "ipv6", "path",
    "hex", "id", "number",
];

const PATTERNS: &[&str] = &[
    // timestamp
    r"\[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}\]|\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b",
    // uuid
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
    // url
    r#"https?://[^\s"'>]+"#,
    // query
    r"\?[A-Za-z0-9_.%+\-]+=[^&\s]*(?:&[A-Za-z0-9_.%+\-]+=[^&\s]*)+",
    // email
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    // ipv4 (with optional :port)
    r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b",
    // ipv6
    r"\b(?:[0-9A-Fa-f]{1,4}:){2,}[0-9A-Fa-f:.]+\b",
    // path
    r#"(?:(?:[A-Za-z]:)?(?:\.\.?/|/))[^\s"'<>|,;]*[A-Za-z0-9_\-/]"#,
    // hex
    r"\b(?:0x[0-9A-Fa-f]+|[0-9A-Fa-f]{16,})\b",
    // id (case-insensitive request/trace/user/session)
    r"(?i)\b(?:req(?:uest)?|trace|user|session)[\-_]?(?:id[\-_:]?)?[A-Za-z0-9]{4,}(?:\-[A-Za-z0-9]{2,})*\b",
    // number
    r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?",
];

const KV_PATTERN: &str =
    r#"(?P<key>\b[A-Za-z_][A-Za-z0-9_.\-]*)=(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\[[^\]\n]*\]|[^\s,;|]+)"#;

// Whitespace pattern for normalize.
const WHITESPACE_PATTERN: &str = r"\s+";

fn build_combined_pattern() -> String {
    let parts: Vec<String> = PATTERNS
        .iter()
        .zip(KIND_NAMES.iter())
        .map(|(p, k)| format!("(?P<{}>{})", k, p))
        .collect();
    parts.join("|")
}

static COMBINED: Lazy<Regex> =
    Lazy::new(|| Regex::new(&build_combined_pattern()).expect("combined regex"));
static KV_RE: Lazy<Regex> = Lazy::new(|| Regex::new(KV_PATTERN).expect("kv regex"));
static WHITESPACE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(WHITESPACE_PATTERN).expect("whitespace regex"));

// Patterns used by _normalize_text_part — applied in this exact
// order with these placeholders so output strings match Python.
//
// The number pattern uses placeholder "<num>" (NOT "<number>") and
// the request-ish id pattern uses "<id>"; this matches the Python
// _normalize_text_part exactly.
struct NormSub {
    re: Lazy<Regex>,
    placeholder: &'static str,
}

static NORM_PATTERNS_ORDER: &[(&str, &str)] = &[
    // Same patterns as PATTERNS above, in the order Python applies
    // them in _normalize_text_part.
    (PATTERNS[1], "<uuid>"),      // uuid
    (PATTERNS[0], "<timestamp>"), // timestamp
    (PATTERNS[4], "<email>"),     // email
    (PATTERNS[2], "<url>"),       // url
    (PATTERNS[3], "<query>"),     // query
    (PATTERNS[5], "<ipv4>"),      // ipv4
    (PATTERNS[6], "<ipv6>"),      // ipv6
    (PATTERNS[7], "<path>"),      // path
    (PATTERNS[8], "<hex>"),       // hex
    (PATTERNS[9], "<id>"),        // id (request-ish)
    (PATTERNS[10], "<num>"),      // number → "<num>" placeholder
];

static NORM_REGEXES: Lazy<Vec<(Regex, &'static str)>> = Lazy::new(|| {
    NORM_PATTERNS_ORDER
        .iter()
        .map(|(pat, ph)| (Regex::new(pat).expect("norm regex"), *ph))
        .collect()
});

// ---------- normalize_text_part: _normalize_text_part in Python ----------

fn normalize_text_part(part: &str) -> String {
    let lowered = part.trim().to_lowercase();
    let collapsed = WHITESPACE_RE.replace_all(&lowered, " ").into_owned();
    if collapsed.is_empty() {
        return String::new();
    }
    let mut s = collapsed;
    for (re, placeholder) in NORM_REGEXES.iter() {
        s = re.replace_all(&s, *placeholder).into_owned();
    }
    s
}

// ---------- scan_one: _find_next_variable + _scan_text_line walk ----------

fn scan_one(line: &str) -> (Vec<String>, Vec<String>, Vec<String>) {
    let mut parts: Vec<String> = Vec::new();
    let mut values: Vec<String> = Vec::new();
    let mut kinds: Vec<String> = Vec::new();
    let mut cursor: usize = 0;
    let bytes = line.as_bytes();
    let n = bytes.len();

    while cursor < n {
        let mut best: Option<(usize, usize, String)> = None;

        // KV first — sets initial best.
        if let Some(caps) = KV_RE.captures_at(line, cursor) {
            let key = caps.name("key").unwrap().as_str().to_lowercase();
            let val = caps.name("value").unwrap();
            best = Some((val.start(), val.end(), format!("kv:{}", key)));
        }

        // Combined generic patterns.
        if let Some(caps) = COMBINED.captures_at(line, cursor) {
            for name in KIND_NAMES.iter() {
                if let Some(m) = caps.name(name) {
                    let cand = (m.start(), m.end(), name.to_string());
                    let take = match &best {
                        None => true,
                        Some((bs, be, _)) => {
                            cand.0 < *bs || (cand.0 == *bs && cand.1 > *be)
                        }
                    };
                    if take {
                        best = Some(cand);
                    }
                    break;
                }
            }
        }

        match best {
            None => break,
            Some((s, e, k)) => {
                if s < cursor {
                    break;
                }
                parts.push(std::str::from_utf8(&bytes[cursor..s]).unwrap().to_string());
                values.push(std::str::from_utf8(&bytes[s..e]).unwrap().to_string());
                kinds.push(k);
                cursor = e;
            }
        }
    }
    parts.push(std::str::from_utf8(&bytes[cursor..]).unwrap().to_string());
    (parts, values, kinds)
}

// ---------- analyze_one: scan + normalized_skeleton ----------
//
// Mirrors _scan_text_line + _normalized_skeleton (with empty
// json_structure_key — text mode only).

fn analyze_one(line: &str) -> (Vec<String>, Vec<String>, Vec<String>, Vec<String>) {
    let (parts, values, kinds) = scan_one(line);
    // _normalized_skeleton: interleave normalize(part) and "<kind>"
    let mut skeleton: Vec<String> = Vec::with_capacity(parts.len() + kinds.len());
    for (i, part) in parts.iter().enumerate() {
        skeleton.push(normalize_text_part(part));
        if i < kinds.len() {
            skeleton.push(format!("<{}>", kinds[i]));
        }
    }
    (parts, values, kinds, skeleton)
}

// ---------- Python bindings ----------

#[pyfunction]
fn scan_line(line: &str) -> (Vec<String>, Vec<String>, Vec<String>) {
    scan_one(line)
}

#[pyfunction]
fn analyze_text(
    line: &str,
) -> (Vec<String>, Vec<String>, Vec<String>, Vec<String>) {
    analyze_one(line)
}

#[pyfunction]
fn normalize_part(part: &str) -> String {
    normalize_text_part(part)
}

#[pymodule]
fn mc_tokenizer_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(scan_line, m)?)?;
    m.add_function(wrap_pyfunction!(analyze_text, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_part, m)?)?;
    m.add("__version__", "0.1.0")?;
    Ok(())
}
