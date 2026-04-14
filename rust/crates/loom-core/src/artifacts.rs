//! Task artifact normalization. Ported from `loom/artifacts.py`.
//!
//! Artifacts are user-provided blobs attached to board tasks: logs, diffs,
//! reports, snippets, docs, refs. Stored as a `Vec<Artifact>` on `BoardTask`.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Artifact {
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub mime_type: String,
    #[serde(default)]
    pub created_at: String,
}

const VALID_KINDS: &[&str] = &["log", "diff", "report", "snippet", "doc", "ref"];

pub fn normalize_artifacts(raw: &[serde_json::Value]) -> Vec<Artifact> {
    raw.iter().filter_map(normalize_artifact).collect()
}

fn normalize_artifact(value: &serde_json::Value) -> Option<Artifact> {
    let obj = value.as_object()?;
    let kind = obj.get("kind").and_then(|v| v.as_str()).unwrap_or("").trim().to_lowercase();
    if !VALID_KINDS.contains(&kind.as_str()) {
        return None;
    }
    let mut a = Artifact::default();
    a.kind = kind;
    a.title = obj.get("title").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    a.body = obj.get("body").and_then(|v| v.as_str()).unwrap_or("").to_string();
    a.path = obj.get("path").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    a.url = obj.get("url").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    a.mime_type = obj.get("mime_type").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    a.created_at = obj.get("created_at").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    Some(a)
}

impl Default for Artifact {
    fn default() -> Self {
        Self {
            kind: String::new(),
            title: String::new(),
            body: String::new(),
            path: String::new(),
            url: String::new(),
            mime_type: String::new(),
            created_at: String::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Attachment {
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub filename: String,
    #[serde(default)]
    pub mime_type: String,
}

pub fn normalize_attachments(raw: &[serde_json::Value]) -> Vec<Attachment> {
    raw.iter()
        .filter_map(|v| {
            let o = v.as_object()?;
            let path = o.get("path").and_then(|v| v.as_str())?.trim().to_string();
            if path.is_empty() {
                return None;
            }
            Some(Attachment {
                path,
                filename: o.get("filename").and_then(|v| v.as_str()).unwrap_or("").trim().to_string(),
                mime_type: o.get("mime_type").and_then(|v| v.as_str()).unwrap_or("").trim().to_string(),
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn filters_unknown_kinds() {
        let input = vec![
            json!({"kind": "log", "body": "x"}),
            json!({"kind": "bogus"}),
        ];
        let out = normalize_artifacts(&input);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].kind, "log");
    }

    #[test]
    fn attachment_requires_path() {
        let input = vec![json!({"filename": "x"}), json!({"path": "/tmp/a"})];
        let out = normalize_attachments(&input);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].path, "/tmp/a");
    }
}
