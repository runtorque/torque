//! Canonical task ID format. Ported from `loom/task_ids.py`.
//!
//! Examples: `general-1`, `general-1a`, `general-1a1`. The group prefix is
//! a slug; the trunk is a numeric counter; the letter suffix tracks derived
//! tasks along the pipeline chain.

use once_cell::sync::Lazy;
use regex::Regex;

static CANONICAL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^(?P<group>[a-z0-9\-]+)-(?P<trunk>\d+)(?P<derived>[a-z0-9]*)$").unwrap()
});

static DRAFT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[a-z0-9\-]+-draft-[0-9a-f]+$").unwrap());

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParsedTaskId {
    pub group: String,
    pub trunk: u64,
    pub derived_suffix: String,
}

pub fn parse_task_id(id: &str) -> Option<ParsedTaskId> {
    let caps = CANONICAL_RE.captures(id)?;
    let group = caps.name("group")?.as_str().to_string();
    let trunk = caps.name("trunk")?.as_str().parse().ok()?;
    let derived_suffix = caps
        .name("derived")
        .map(|m| m.as_str().to_string())
        .unwrap_or_default();
    Some(ParsedTaskId {
        group,
        trunk,
        derived_suffix,
    })
}

pub fn is_canonical_task_id(id: &str) -> bool {
    parse_task_id(id).is_some()
}

pub fn is_draft_task_token(id: &str) -> bool {
    DRAFT_RE.is_match(id)
}

pub fn normalize_group_prefix(group: &str) -> String {
    crate::slug::slugify(group)
}

pub fn format_root_task_id(group: &str, trunk: u64) -> String {
    format!("{}-{}", normalize_group_prefix(group), trunk)
}

pub fn format_derived_task_id(root_id: &str, derived_suffix: &str) -> String {
    format!("{root_id}{derived_suffix}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_canonical() {
        let parsed = parse_task_id("general-42").unwrap();
        assert_eq!(parsed.group, "general");
        assert_eq!(parsed.trunk, 42);
        assert_eq!(parsed.derived_suffix, "");

        let derived = parse_task_id("general-42a1").unwrap();
        assert_eq!(derived.derived_suffix, "a1");
    }

    #[test]
    fn is_draft() {
        assert!(is_draft_task_token("general-draft-abc123"));
        assert!(!is_draft_task_token("general-42"));
    }

    #[test]
    fn format_roundtrip() {
        let root = format_root_task_id("my group", 7);
        assert_eq!(root, "my-group-7");
        assert!(is_canonical_task_id(&root));
    }
}
