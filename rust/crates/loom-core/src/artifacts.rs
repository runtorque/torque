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
    let kind = obj
        .get("kind")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_lowercase();
    if !VALID_KINDS.contains(&kind.as_str()) {
        return None;
    }
    let mut a = Artifact::default();
    a.kind = kind;
    a.title = obj
        .get("title")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    a.body = obj
        .get("body")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    a.path = obj
        .get("path")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    a.url = obj
        .get("url")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    a.mime_type = obj
        .get("mime_type")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    a.created_at = obj
        .get("created_at")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
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
                filename: o
                    .get("filename")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string(),
                mime_type: o
                    .get("mime_type")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string(),
            })
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Prompt blocks — ported from `loom/artifacts.py`.
// Python tracks each artifact as a dict keyed by `type`, `title`, `summary`,
// `content`, `path`, `metadata`, `prompt.mode`, etc. Rust stores them as
// raw `serde_json::Value` on `BoardTask.artifacts`, so these helpers access
// the JSON directly instead of going through the simplified `Artifact`
// normalization above. Matches Python behavior for downstream prompt
// assembly.
// ---------------------------------------------------------------------------

const PROMPT_MODE_AUTO: &str = "auto";
const PROMPT_MODE_NONE: &str = "none";
const PROMPT_MODE_PATH: &str = "path";
const PROMPT_MODE_SUMMARY: &str = "summary";
const PROMPT_MODE_INLINE: &str = "inline";
const ARTIFACT_IMAGE: &str = "image";

fn type_label(atype: &str) -> String {
    match atype {
        "image" => "Image".into(),
        "file_ref" => "File reference".into(),
        "snippet" => "Snippet".into(),
        "log" => "Log".into(),
        "diff" => "Diff".into(),
        "test_report" => "Test report".into(),
        "generated_doc" => "Generated doc".into(),
        _ if atype.is_empty() => "artifact".into(),
        _ => atype.replace('_', " "),
    }
}

fn default_prompt_mode(atype: &str) -> &'static str {
    match atype {
        "image" | "file_ref" => PROMPT_MODE_PATH,
        "snippet" => PROMPT_MODE_INLINE,
        "log" | "diff" | "test_report" | "generated_doc" => PROMPT_MODE_SUMMARY,
        _ => PROMPT_MODE_SUMMARY,
    }
}

fn inline_limit(atype: &str) -> usize {
    match atype {
        "snippet" => 2000,
        "log" => 1500,
        "diff" => 2000,
        _ => 1200,
    }
}

fn clip_text(text: &str, limit: usize) -> String {
    if limit == 0 || text.chars().count() <= limit {
        return text.to_string();
    }
    let cutoff = limit.saturating_sub(1);
    let mut out: String = text.chars().take(cutoff).collect();
    out.push('…');
    out
}

fn jstr<'a>(obj: &'a serde_json::Map<String, serde_json::Value>, key: &str) -> &'a str {
    obj.get(key).and_then(|v| v.as_str()).unwrap_or("")
}

fn markdown_fenced(body: &str) -> String {
    format!("```\n{}\n```", body.trim_end())
}

fn effective_mode(artifact: &serde_json::Map<String, serde_json::Value>) -> &'static str {
    let atype = jstr(artifact, "type");
    let mode = artifact
        .get("prompt")
        .and_then(|v| v.as_object())
        .and_then(|p| p.get("mode"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let mode = if mode.is_empty() {
        default_prompt_mode(atype)
    } else {
        mode
    };
    match mode {
        PROMPT_MODE_NONE => PROMPT_MODE_NONE,
        PROMPT_MODE_PATH => PROMPT_MODE_PATH,
        PROMPT_MODE_SUMMARY => PROMPT_MODE_SUMMARY,
        PROMPT_MODE_INLINE => PROMPT_MODE_INLINE,
        PROMPT_MODE_AUTO => {
            let content = jstr(artifact, "content");
            if !content.is_empty() && content.chars().count() <= inline_limit(atype) {
                PROMPT_MODE_INLINE
            } else {
                default_prompt_mode(atype)
            }
        }
        _ => default_prompt_mode(atype),
    }
}

fn artifact_reference(artifact: &serde_json::Map<String, serde_json::Value>) -> String {
    // Path first, then URL. Trim whitespace. Matches `_artifact_reference`.
    let path = jstr(artifact, "path").trim();
    if !path.is_empty() {
        return path.to_string();
    }
    let url = jstr(artifact, "url").trim();
    url.to_string()
}

fn format_single_line(
    artifact: &serde_json::Map<String, serde_json::Value>,
    prefix: &str,
) -> Option<String> {
    let mode = effective_mode(artifact);
    if mode == PROMPT_MODE_NONE {
        return None;
    }
    let atype = jstr(artifact, "type");
    let default_title = type_label(atype);
    let title = {
        let t = jstr(artifact, "title");
        if t.is_empty() {
            default_title
        } else {
            t.to_string()
        }
    };
    let ref_ = artifact_reference(artifact);
    let summary = jstr(artifact, "summary");
    let content = jstr(artifact, "content");
    let metadata = artifact
        .get("metadata")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();

    let base_type = if atype.is_empty() { "artifact" } else { atype };
    let mut line = format!("{prefix}[{base_type}] {title}");

    let mut extras: Vec<String> = Vec::new();
    if !ref_.is_empty() {
        extras.push(format!("ref: `{ref_}`"));
    }
    match atype {
        "test_report" => {
            let total = metadata.get("total").and_then(|v| v.as_i64());
            if let Some(total) = total {
                let passed = metadata.get("passed").and_then(|v| v.as_i64()).unwrap_or(0);
                let failed = metadata.get("failed").and_then(|v| v.as_i64()).unwrap_or(0);
                extras.push(format!(
                    "results: {passed} passed, {failed} failed, {total} total"
                ));
            }
        }
        "diff" => {
            let files = metadata.get("files").and_then(|v| v.as_i64());
            if let Some(files) = files {
                let ins = metadata
                    .get("insertions")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0);
                let dels = metadata
                    .get("deletions")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0);
                extras.push(format!("stats: {files} files, +{ins}/-{dels}"));
            }
        }
        _ => {}
    }

    if mode == PROMPT_MODE_PATH {
        if !extras.is_empty() {
            line.push_str(" (");
            line.push_str(&extras.join("; "));
            line.push(')');
        }
        return Some(line);
    }

    if !summary.is_empty() {
        extras.push(format!("summary: {}", clip_text(summary, 240)));
    }
    if mode == PROMPT_MODE_INLINE && !content.is_empty() {
        extras.push("inline excerpt follows".into());
    }
    if !extras.is_empty() {
        line.push_str(" (");
        line.push_str(&extras.join("; "));
        line.push(')');
    }
    if mode == PROMPT_MODE_INLINE && !content.is_empty() {
        line.push('\n');
        line.push_str(&markdown_fenced(&clip_text(content, inline_limit(atype))));
    }
    Some(line)
}

/// Render the attachments-and-images block that precedes artifact prompts.
/// Matches `legacy_image_prompt_block`.
pub fn legacy_image_prompt_block(
    attachments: &[serde_json::Value],
    artifacts: &[serde_json::Value],
) -> String {
    let mut lines = Vec::new();
    for att in attachments {
        if let Some(obj) = att.as_object() {
            let path = jstr(obj, "path").trim();
            if !path.is_empty() {
                lines.push(format!("- `{path}`"));
            }
        }
    }
    for art in artifacts {
        if let Some(obj) = art.as_object() {
            if jstr(obj, "type") == ARTIFACT_IMAGE {
                let path = jstr(obj, "path").trim();
                if !path.is_empty() {
                    lines.push(format!("- `{path}`"));
                }
            }
        }
    }
    if lines.is_empty() {
        return String::new();
    }
    format!("\n\n## Attached images\n{}", lines.join("\n"))
}

/// Render the task-scoped artifact prompt block. Matches
/// `artifact_prompt_block`.
pub fn artifact_prompt_block(artifacts: &[serde_json::Value]) -> String {
    let mut lines = Vec::new();
    for art in artifacts {
        if let Some(obj) = art.as_object() {
            if let Some(line) = format_single_line(obj, "- ") {
                lines.push(line);
            }
        }
    }
    if lines.is_empty() {
        return String::new();
    }
    format!("\n\n## Task artifacts\n{}", lines.join("\n"))
}

/// Render upstream artifacts handed off from a parent/dependency task.
/// Matches `upstream_artifact_prompt_block`.
pub fn upstream_artifact_prompt_block(artifacts: &[serde_json::Value]) -> String {
    let mut lines = Vec::new();
    for raw in artifacts {
        let Some(raw_obj) = raw.as_object() else {
            continue;
        };
        // Python passes the raw upstream entry (with `source_task_label` /
        // `source_task_id` on top) and normalizes into an artifact for the
        // inner rendering. We just access the fields directly.
        let source_label = jstr(raw_obj, "source_task_label").trim();
        let source_id = jstr(raw_obj, "source_task_id").trim();
        let prefix_label = if !source_label.is_empty() {
            source_label
        } else if !source_id.is_empty() {
            source_id
        } else {
            "upstream task"
        };
        let prefix_suffix = if !source_id.is_empty() {
            format!(" ({source_id})")
        } else {
            String::new()
        };
        let prefix = format!("- From `{prefix_label}`{prefix_suffix}: ");
        if let Some(line) = format_single_line(raw_obj, &prefix) {
            lines.push(line);
        }
    }
    if lines.is_empty() {
        return String::new();
    }
    format!("\n\n## Upstream handoff artifacts\n{}", lines.join("\n"))
}

/// One-stop helper matching `_append_task_artifacts` in `loom/server_agent.py`.
/// Appends image, artifact, and upstream-artifact sections to `prompt`.
pub fn append_task_artifacts(
    prompt: &str,
    attachments: &[serde_json::Value],
    artifacts: &[serde_json::Value],
    upstream_artifacts: &[serde_json::Value],
) -> String {
    let mut out = prompt.to_string();
    out.push_str(&legacy_image_prompt_block(attachments, artifacts));
    out.push_str(&artifact_prompt_block(artifacts));
    out.push_str(&upstream_artifact_prompt_block(upstream_artifacts));
    out
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
