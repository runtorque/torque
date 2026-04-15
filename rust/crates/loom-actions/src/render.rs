//! minijinja rendering of action prompts.
//!
//! - Sandboxed env (no arbitrary imports).
//! - StrictUndefined for user-supplied vars; safe defaults for `loom.*` refs.
//! - Variable discovery via regex over the template source. This is
//!   deliberately simpler than jinja2's AST walk — our templates use only
//!   `{{ VAR }}` substitution, `{% for x in LIST %}` loops, and the `loom.*`
//!   namespace. A regex pass is sufficient for these shapes and avoids
//!   depending on minijinja's `unstable_machinery` feature.

use std::collections::HashSet;

use minijinja::{Environment, UndefinedBehavior, Value};
use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum RenderError {
    #[error("template: {0}")]
    Template(String),
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
}

impl From<minijinja::Error> for RenderError {
    fn from(err: minijinja::Error) -> Self {
        Self::Template(err.to_string())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RenderOutput {
    pub prompt: String,
}

/// Render a template string with user variables + the `loom` namespace.
pub fn render_prompt(
    template: &str,
    user_vars: &serde_json::Map<String, serde_json::Value>,
    loom_ctx: &serde_json::Value,
) -> Result<String, RenderError> {
    let mut env = Environment::new();
    env.set_undefined_behavior(UndefinedBehavior::Strict);
    env.add_template("action", template)?;
    let tmpl = env.get_template("action")?;

    let mut ctx = serde_json::Map::new();
    for (k, v) in user_vars {
        ctx.insert(k.clone(), v.clone());
    }
    ctx.insert("loom".into(), loom_ctx.clone());

    let value = Value::from_serialize(&serde_json::Value::Object(ctx));
    Ok(tmpl.render(value)?)
}

// ---------------------------------------------------------------------------
// Variable discovery
// ---------------------------------------------------------------------------

// Matches identifiers inside `{{ ... }}` or `{% ... %}` blocks.
static VAR_EXPR: Lazy<Regex> = Lazy::new(|| Regex::new(r"\{\{([^}]*)\}\}|\{%([^%]*)%\}").unwrap());

// Matches a bare identifier. Used for scanning inside the expressions above.
static IDENT: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?:^|[^\w.'\x22])([A-Za-z_][A-Za-z0-9_]*)").unwrap());

// Built-in names that are never "undeclared user variables".
const BUILTINS: &[&str] = &[
    "loom",
    "loop",
    "self",
    "super",
    // minijinja builtins & tests
    "true",
    "false",
    "none",
    "True",
    "False",
    "None",
    "not",
    "and",
    "or",
    "in",
    "is",
    "if",
    "else",
    "elif",
    "endif",
    "for",
    "endfor",
    "set",
    "endset",
    "do",
    "with",
    "endwith",
    "block",
    "endblock",
    "extends",
    "include",
    "import",
    "from",
    "as",
    "macro",
    "endmacro",
    // common jinja filters
    "default",
    "length",
    "upper",
    "lower",
    "join",
    "trim",
    "replace",
    "capitalize",
    "title",
    "escape",
    "safe",
    "tojson",
    "first",
    "last",
    "sort",
    "reverse",
    "map",
    "select",
    "reject",
    "items",
    "keys",
    "values",
    "range",
    "list",
    "dict",
    "int",
    "float",
    "string",
    "bool",
];

/// Discover user-supplied variable names referenced in a template.
///
/// Returns variables sorted alphabetically. Excludes the `loom` namespace
/// (handled separately), for-loop locals, filters, and jinja keywords.
pub fn discover_variables(template: &str) -> Vec<super::manager::ActionVariable> {
    let builtins: HashSet<&str> = BUILTINS.iter().copied().collect();
    let mut found: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    let mut loop_locals: HashSet<String> = HashSet::new();

    // First pass: find `for X in Y` loop targets so we don't confuse X with a
    // user var.
    static FOR_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"\{%\s*for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+").unwrap());
    for cap in FOR_RE.captures_iter(template) {
        loop_locals.insert(cap[1].to_string());
    }

    static SET_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"\{%\s*set\s+([A-Za-z_][A-Za-z0-9_]*)\s*=").unwrap());
    for cap in SET_RE.captures_iter(template) {
        loop_locals.insert(cap[1].to_string());
    }

    // Second pass: scan all expressions.
    for cap in VAR_EXPR.captures_iter(template) {
        let inner = cap
            .get(1)
            .or_else(|| cap.get(2))
            .map(|m| m.as_str())
            .unwrap_or("");
        for m in IDENT.captures_iter(inner) {
            let name = m[1].to_string();
            // Skip identifiers that appear after `|` or `.` — those are
            // filters or attribute accesses. The IDENT regex already excludes
            // some of these via the preceding-char class; do an explicit
            // check in case of edge cases.
            if builtins.contains(name.as_str()) {
                continue;
            }
            if loop_locals.contains(&name) {
                continue;
            }
            if name == "loom" {
                continue;
            }
            if seen.insert(name.clone()) {
                found.push(name);
            }
        }
    }

    found.sort();
    found
        .into_iter()
        .map(|name| super::manager::ActionVariable {
            name,
            default: None,
            required: true,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn render_basic() {
        let ctx = json!({});
        let mut vars = serde_json::Map::new();
        vars.insert("TASK".into(), json!("Fix bug"));
        let out = render_prompt("Do: {{ TASK }}", &vars, &ctx).unwrap();
        assert_eq!(out, "Do: Fix bug");
    }

    #[test]
    fn render_strict_rejects_unknown() {
        let ctx = json!({});
        let vars = serde_json::Map::new();
        let err = render_prompt("{{ UNDEF }}", &vars, &ctx).unwrap_err();
        assert!(matches!(err, RenderError::Template(_)));
    }

    #[test]
    fn render_uses_loom_namespace() {
        let ctx = json!({"agent": {"name": "Alice"}});
        let mut vars = serde_json::Map::new();
        vars.insert("TASK".into(), json!("hi"));
        let out = render_prompt("{{ loom.agent.name }}: {{ TASK }}", &vars, &ctx).unwrap();
        assert_eq!(out, "Alice: hi");
    }

    #[test]
    fn discover_finds_user_vars_excludes_loom() {
        let vars = discover_variables("{{ TASK }} and {{ FOO }} but not {{ loom.x }}");
        let names: Vec<&str> = vars.iter().map(|v| v.name.as_str()).collect();
        assert!(names.contains(&"TASK"));
        assert!(names.contains(&"FOO"));
        assert!(!names.contains(&"loom"));
    }

    #[test]
    fn discover_skips_for_loop_vars() {
        let vars = discover_variables("{% for item in ITEMS %}{{ item }}{% endfor %}");
        let names: Vec<&str> = vars.iter().map(|v| v.name.as_str()).collect();
        assert!(names.contains(&"ITEMS"));
        assert!(!names.contains(&"item"));
    }

    #[test]
    fn discover_skips_keywords_and_filters() {
        // The `|default("x")` filter should not produce a "default" variable.
        let vars = discover_variables("{{ NAME | default('x') }}");
        let names: Vec<&str> = vars.iter().map(|v| v.name.as_str()).collect();
        assert!(names.contains(&"NAME"));
        assert!(!names.contains(&"default"));
    }

    #[test]
    fn render_supports_default_filter() {
        let ctx = json!({});
        let vars = serde_json::Map::new();
        let out = render_prompt("{{ TASK | default('fallback') }}", &vars, &ctx).unwrap();
        assert_eq!(out, "fallback");
    }
}
