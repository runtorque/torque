//! Slug generation. Ported from `loom/state.py::_slugify` + `_unique_slug`.

use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::HashSet;

static NON_ALNUM: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^a-z0-9]+").unwrap());
static DASHES: Lazy<Regex> = Lazy::new(|| Regex::new(r"-+").unwrap());

const DEFAULT_MAX_LEN: usize = 40;

pub fn slugify(name: &str) -> String {
    slugify_with_limit(name, DEFAULT_MAX_LEN)
}

pub fn slugify_with_limit(name: &str, max_len: usize) -> String {
    let lower = name.to_lowercase();
    let normalized = NON_ALNUM.replace_all(&lower, "-").into_owned();
    let collapsed = DASHES.replace_all(&normalized, "-").into_owned();
    let trimmed = collapsed.trim_matches('-').to_string();
    let clipped = if trimmed.len() > max_len {
        // Mirror Python's `slug[:max_len].rsplit("-", 1)[0]` trick —
        // avoid leaving a trailing partial word.
        let head = &trimmed[..max_len];
        match head.rsplit_once('-') {
            Some((prefix, _)) if !prefix.is_empty() => prefix.to_string(),
            _ => head.to_string(),
        }
    } else {
        trimmed
    };
    if clipped.is_empty() {
        "unnamed".to_string()
    } else {
        clipped
    }
}

pub fn unique_slug(base: &str, existing: &HashSet<String>) -> String {
    if !existing.contains(base) {
        return base.to_string();
    }
    let mut i = 2;
    loop {
        let candidate = format!("{base}-{i}");
        if !existing.contains(&candidate) {
            return candidate;
        }
        i += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugify_basics() {
        assert_eq!(slugify("My Agent"), "my-agent");
        assert_eq!(slugify("hello_world"), "hello-world");
        assert_eq!(slugify("  trimmed  "), "trimmed");
        assert_eq!(slugify("UPPER CASE"), "upper-case");
        assert_eq!(slugify("!!!"), "unnamed");
        assert_eq!(slugify(""), "unnamed");
        assert_eq!(slugify("multi---dash"), "multi-dash");
    }

    #[test]
    fn slugify_truncates_at_word_boundary() {
        let long = "a".repeat(30) + " " + &"b".repeat(30);
        let out = slugify(&long);
        assert!(out.len() <= DEFAULT_MAX_LEN);
        assert!(!out.ends_with('-'));
    }

    #[test]
    fn unique_slug_appends_suffix() {
        let mut existing: HashSet<String> = HashSet::new();
        existing.insert("foo".into());
        existing.insert("foo-2".into());
        assert_eq!(unique_slug("foo", &existing), "foo-3");
        assert_eq!(unique_slug("bar", &existing), "bar");
    }
}
