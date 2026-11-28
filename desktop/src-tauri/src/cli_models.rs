//! Offline discovery of locally supported Codex and Claude Code models & effort levels.
use std::{
    collections::HashSet,
    env,
    fs,
    io::Read,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::types::Provider;

const MAX_CACHE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_CONFIG_BYTES: u64 = 256 * 1024;

pub const CODEX_EFFORTS: &[&str] = &["minimal", "low", "medium", "high", "xhigh", "max", "ultra"];
pub const CLAUDE_EFFORTS: &[&str] = &["low", "medium", "high", "xhigh", "max"];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CliModelOption {
    pub id: String,
    pub label: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub efforts: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_effort: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CliModelCatalog {
    pub models: Vec<CliModelOption>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_effort: Option<String>,
    pub supported_efforts: Vec<String>,
    pub source: &'static str,
}

pub fn valid_cli_model(model: &str) -> bool {
    !model.is_empty()
        && model.len() <= 256
        && !model.starts_with('-')
        && !model.chars().any(char::is_control)
}

pub fn valid_reasoning_effort(provider: Provider, effort: &str) -> bool {
    match provider {
        Provider::Codex => CODEX_EFFORTS.contains(&effort),
        Provider::Claude => CLAUDE_EFFORTS.contains(&effort),
        Provider::Api => effort.is_empty(),
    }
}

fn read_bounded_utf8(path: &Path, limit: u64) -> Option<String> {
    let file = fs::File::open(path).ok()?;
    let meta = file.metadata().ok()?;
    if !meta.is_file() || meta.len() > limit {
        return None;
    }
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() as u64 > limit {
        return None;
    }
    String::from_utf8(bytes).ok()
}

fn clean_label(value: &str, max_chars: usize) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() || trimmed.chars().any(char::is_control) {
        return None;
    }
    Some(trimmed.chars().take(max_chars).collect())
}

fn parse_toml_string_literal(raw: &str) -> Option<String> {
    let trimmed = raw.trim();
    let quote = trimmed.as_bytes().first().copied()?;
    if quote != b'"' && quote != b'\'' {
        return None;
    }
    let rest = &trimmed[1..];
    let end = rest.find(quote as char)?;
    let value = &rest[..end];
    if value.chars().any(char::is_control) {
        return None;
    }
    Some(value.to_owned())
}

fn parse_codex_config(text: &str) -> (Option<String>, Option<String>) {
    let mut model = None;
    let mut effort = None;
    for raw_line in text.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if line.starts_with('[') {
            break;
        }
        let Some((key, rhs)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim();
        if key == "model" && model.is_none() {
            if let Some(value) = parse_toml_string_literal(rhs) {
                if valid_cli_model(&value) {
                    model = Some(value);
                }
            }
        } else if key == "model_reasoning_effort" && effort.is_none() {
            if let Some(value) = parse_toml_string_literal(rhs) {
                let normalized = value.to_ascii_lowercase();
                if CODEX_EFFORTS.contains(&normalized.as_str()) {
                    effort = Some(normalized);
                }
            }
        }
    }
    (model, effort)
}

fn codex_home_dir() -> Option<PathBuf> {
    if let Some(custom) = env::var_os("CODEX_HOME") {
        let path = PathBuf::from(custom);
        if path.is_absolute() {
            return Some(path);
        }
    }
    env::var_os("HOME").map(|home| PathBuf::from(home).join(".codex"))
}

fn home_dir() -> Option<PathBuf> {
    env::var_os("HOME").map(PathBuf::from)
}

fn claude_config_dir() -> Option<PathBuf> {
    if let Some(custom) = env::var_os("CLAUDE_CONFIG_DIR") {
        let path = PathBuf::from(custom);
        if path.is_absolute() {
            return Some(path);
        }
    }
    home_dir().map(|home| home.join(".claude"))
}

fn non_empty_string(value: Option<&Value>) -> bool {
    value
        .and_then(Value::as_str)
        .is_some_and(|s| !s.trim().is_empty() && !s.chars().any(char::is_control))
}

pub fn claude_auth_available_in(
    config_dir: Option<&Path>,
    env_api_key: Option<&str>,
    env_auth_token: Option<&str>,
) -> bool {
    if env_api_key.is_some_and(|v| !v.trim().is_empty())
        || env_auth_token.is_some_and(|v| !v.trim().is_empty())
    {
        return true;
    }
    let Some(text) =
        config_dir.and_then(|dir| read_bounded_utf8(&dir.join("settings.json"), MAX_CACHE_BYTES))
    else {
        return false;
    };
    let Ok(root) = serde_json::from_str::<Value>(&text) else {
        return false;
    };
    if non_empty_string(root.get("apiKeyHelper")) {
        return true;
    }
    root.get("env").and_then(Value::as_object).is_some_and(|env_map| {
        non_empty_string(env_map.get("ANTHROPIC_API_KEY"))
            || non_empty_string(env_map.get("ANTHROPIC_AUTH_TOKEN"))
    })
}

pub fn claude_auth_available() -> bool {
    let dir = claude_config_dir();
    let api_key = env::var("ANTHROPIC_API_KEY").ok();
    let auth_token = env::var("ANTHROPIC_AUTH_TOKEN").ok();
    claude_auth_available_in(dir.as_deref(), api_key.as_deref(), auth_token.as_deref())
}

fn builtin_codex_models() -> Vec<CliModelOption> {
    let standard = |efforts: &[&str]| -> Vec<String> { efforts.iter().map(|e| (*e).to_owned()).collect() };
    vec![
        CliModelOption {
            id: "gpt-5.4".into(),
            label: "GPT-5.4".into(),
            efforts: standard(&["low", "medium", "high", "xhigh"]),
            default_effort: Some("medium".into()),
            description: None,
        },
        CliModelOption {
            id: "gpt-5.3-codex".into(),
            label: "GPT-5.3-Codex".into(),
            efforts: standard(&["low", "medium", "high", "xhigh"]),
            default_effort: Some("medium".into()),
            description: None,
        },
        CliModelOption {
            id: "gpt-5.2-codex".into(),
            label: "GPT-5.2-Codex".into(),
            efforts: standard(&["low", "medium", "high", "xhigh"]),
            default_effort: Some("medium".into()),
            description: None,
        },
        CliModelOption {
            id: "gpt-5.1-codex".into(),
            label: "GPT-5.1-Codex".into(),
            efforts: standard(&["low", "medium", "high"]),
            default_effort: Some("medium".into()),
            description: None,
        },
        CliModelOption {
            id: "gpt-5.1-codex-mini".into(),
            label: "GPT-5.1-Codex-Mini".into(),
            efforts: standard(&["low", "medium", "high"]),
            default_effort: Some("medium".into()),
            description: None,
        },
        CliModelOption {
            id: "o3".into(),
            label: "o3".into(),
            efforts: standard(&["low", "medium", "high"]),
            default_effort: Some("medium".into()),
            description: None,
        },
        CliModelOption {
            id: "o4-mini".into(),
            label: "o4-mini".into(),
            efforts: standard(&["low", "medium", "high"]),
            default_effort: Some("medium".into()),
            description: None,
        },
    ]
}

fn builtin_claude_models() -> Vec<CliModelOption> {
    let standard = |efforts: &[&str]| -> Vec<String> { efforts.iter().map(|e| (*e).to_owned()).collect() };
    vec![
        CliModelOption {
            id: "sonnet".into(),
            label: "Claude Sonnet".into(),
            efforts: standard(CLAUDE_EFFORTS),
            default_effort: Some("medium".into()),
            description: None,
        },
        CliModelOption {
            id: "opus".into(),
            label: "Claude Opus".into(),
            efforts: standard(CLAUDE_EFFORTS),
            default_effort: Some("high".into()),
            description: None,
        },
        CliModelOption {
            id: "haiku".into(),
            label: "Claude Haiku".into(),
            efforts: standard(&["low", "medium", "high"]),
            default_effort: Some("medium".into()),
            description: None,
        },
        CliModelOption {
            id: "claude-sonnet-4-6".into(),
            label: "Claude Sonnet 4.6".into(),
            efforts: standard(CLAUDE_EFFORTS),
            default_effort: Some("medium".into()),
            description: None,
        },
        CliModelOption {
            id: "claude-opus-4-6".into(),
            label: "Claude Opus 4.6".into(),
            efforts: standard(CLAUDE_EFFORTS),
            default_effort: Some("high".into()),
            description: None,
        },
        CliModelOption {
            id: "claude-haiku-4-5".into(),
            label: "Claude Haiku 4.5".into(),
            efforts: standard(&["low", "medium", "high"]),
            default_effort: Some("medium".into()),
            description: None,
        },
    ]
}

fn parse_codex_cache(text: &str) -> Vec<CliModelOption> {
    let Ok(root) = serde_json::from_str::<Value>(text) else {
        return Vec::new();
    };
    let Some(items) = root.get("models").and_then(Value::as_array) else {
        return Vec::new();
    };
    let mut ranked: Vec<(i64, usize, CliModelOption)> = Vec::new();
    let mut seen = HashSet::new();
    for (index, item) in items.iter().enumerate() {
        let Some(obj) = item.as_object() else {
            continue;
        };
        if obj.get("visibility").and_then(Value::as_str) == Some("hide") {
            continue;
        }
        let Some(slug) = obj.get("slug").and_then(Value::as_str).map(str::trim) else {
            continue;
        };
        if !valid_cli_model(slug) || !seen.insert(slug.to_owned()) {
            continue;
        }
        let label = obj
            .get("display_name")
            .and_then(Value::as_str)
            .and_then(|v| clean_label(v, 128))
            .unwrap_or_else(|| slug.to_owned());
        let mut efforts = Vec::new();
        if let Some(levels) = obj.get("supported_reasoning_levels").and_then(Value::as_array) {
            for level in levels {
                let raw = level
                    .as_str()
                    .or_else(|| level.get("effort").and_then(Value::as_str));
                if let Some(effort) = raw.map(|v| v.trim().to_ascii_lowercase()) {
                    if CODEX_EFFORTS.contains(&effort.as_str()) && !efforts.contains(&effort) {
                        efforts.push(effort);
                    }
                }
            }
        }
        if efforts.is_empty() {
            efforts = vec!["low".into(), "medium".into(), "high".into()];
        }
        let default_effort = obj
            .get("default_reasoning_level")
            .and_then(Value::as_str)
            .map(|v| v.trim().to_ascii_lowercase())
            .filter(|v| efforts.contains(v));
        let description = obj
            .get("description")
            .and_then(Value::as_str)
            .and_then(|v| clean_label(v, 240));
        let priority = obj
            .get("priority")
            .and_then(Value::as_i64)
            .unwrap_or(i64::MAX);
        ranked.push((
            priority,
            index,
            CliModelOption {
                id: slug.to_owned(),
                label,
                efforts,
                default_effort,
                description,
            },
        ));
    }
    ranked.sort_by_key(|(priority, index, _)| (*priority, *index));
    ranked.into_iter().map(|(_, _, option)| option).collect()
}

fn union_efforts(models: &[CliModelOption], allowed: &[&str], fallback: &[&str]) -> Vec<String> {
    let mut present = HashSet::new();
    for model in models {
        for effort in &model.efforts {
            present.insert(effort.as_str());
        }
    }
    let ordered: Vec<String> = allowed
        .iter()
        .filter(|effort| present.contains(**effort))
        .map(|effort| (*effort).to_owned())
        .collect();
    if ordered.is_empty() {
        fallback.iter().map(|e| (*e).to_owned()).collect()
    } else {
        ordered
    }
}

pub fn discover_codex_models_in(codex_home: Option<&Path>) -> CliModelCatalog {
    let (default_model, default_effort) = codex_home
        .and_then(|dir| read_bounded_utf8(&dir.join("config.toml"), MAX_CONFIG_BYTES))
        .map(|text| parse_codex_config(&text))
        .unwrap_or((None, None));
    let cached = codex_home
        .and_then(|dir| read_bounded_utf8(&dir.join("models_cache.json"), MAX_CACHE_BYTES))
        .map(|text| parse_codex_cache(&text))
        .unwrap_or_default();
    let (models, source) = if !cached.is_empty() {
        (cached, "cache")
    } else {
        (builtin_codex_models(), "builtin")
    };
    let supported_efforts = union_efforts(&models, CODEX_EFFORTS, &["low", "medium", "high", "xhigh"]);
    CliModelCatalog {
        models,
        default_model,
        default_effort,
        supported_efforts,
        source,
    }
}

fn parse_claude_additional_models(text: &str) -> Vec<CliModelOption> {
    let Ok(root) = serde_json::from_str::<Value>(text) else {
        return Vec::new();
    };
    let Some(items) = root
        .get("additionalModelOptionsCache")
        .and_then(Value::as_array)
    else {
        return Vec::new();
    };
    let mut results = Vec::new();
    let mut seen = HashSet::new();
    for item in items {
        let Some(obj) = item.as_object() else {
            continue;
        };
        let Some(id) = obj.get("value").and_then(Value::as_str).map(str::trim) else {
            continue;
        };
        if !valid_cli_model(id) || !seen.insert(id.to_owned()) {
            continue;
        }
        let label = obj
            .get("label")
            .and_then(Value::as_str)
            .and_then(|v| clean_label(v, 128))
            .unwrap_or_else(|| id.to_owned());
        let description = obj
            .get("description")
            .and_then(Value::as_str)
            .and_then(|v| clean_label(v, 240));
        results.push(CliModelOption {
            id: id.to_owned(),
            label,
            efforts: CLAUDE_EFFORTS.iter().map(|e| (*e).to_owned()).collect(),
            default_effort: Some("high".into()),
            description,
        });
    }
    results
}

fn parse_claude_settings_models(text: &str) -> (Option<String>, Vec<CliModelOption>) {
    let Ok(root) = serde_json::from_str::<Value>(text) else {
        return (None, Vec::new());
    };
    let Some(env_map) = root.get("env").and_then(Value::as_object) else {
        return (None, Vec::new());
    };
    let mut results = Vec::new();
    let mut seen = HashSet::new();
    let mut push_model = |raw_id: Option<&Value>, raw_name: Option<&Value>| -> Option<String> {
        let id = raw_id.and_then(Value::as_str).map(str::trim)?;
        if !valid_cli_model(id) {
            return None;
        }
        let label = raw_name
            .and_then(Value::as_str)
            .and_then(|v| clean_label(v, 128))
            .unwrap_or_else(|| id.to_owned());
        if seen.insert(id.to_owned()) {
            results.push(CliModelOption {
                id: id.to_owned(),
                label,
                efforts: CLAUDE_EFFORTS.iter().map(|e| (*e).to_owned()).collect(),
                default_effort: Some("high".into()),
                description: None,
            });
        }
        Some(id.to_owned())
    };
    let default_model = push_model(env_map.get("ANTHROPIC_MODEL"), None);
    for (model_key, name_key) in [
        ("ANTHROPIC_DEFAULT_FABLE_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME"),
        ("ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME"),
        ("ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME"),
        ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME"),
    ] {
        let _ = push_model(env_map.get(model_key), env_map.get(name_key));
    }
    // Prefer friendly display name if ANTHROPIC_MODEL matches one of the named tier models.
    if let Some(ref active_id) = default_model {
        for (model_key, name_key) in [
            ("ANTHROPIC_DEFAULT_FABLE_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME"),
            ("ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME"),
            ("ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME"),
            ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME"),
        ] {
            if env_map.get(model_key).and_then(Value::as_str).map(str::trim) == Some(active_id.as_str()) {
                if let Some(name) = env_map.get(name_key).and_then(Value::as_str).and_then(|v| clean_label(v, 128)) {
                    if let Some(first) = results.iter_mut().find(|item| item.id == *active_id) {
                        first.label = name;
                    }
                    break;
                }
            }
        }
    }
    (default_model, results)
}

pub fn discover_claude_models_in(home: Option<&Path>, config_dir: Option<&Path>) -> CliModelCatalog {
    let (default_model, configured) = config_dir
        .and_then(|dir| read_bounded_utf8(&dir.join("settings.json"), MAX_CACHE_BYTES))
        .map(|text| parse_claude_settings_models(&text))
        .unwrap_or((None, Vec::new()));
    let cached = home
        .and_then(|dir| read_bounded_utf8(&dir.join(".claude.json"), MAX_CACHE_BYTES))
        .map(|text| parse_claude_additional_models(&text))
        .unwrap_or_default();
    let source = if configured.is_empty() && cached.is_empty() {
        "builtin"
    } else {
        "cache"
    };
    let mut models = Vec::new();
    let mut seen = HashSet::new();
    for item in configured
        .into_iter()
        .chain(cached)
        .chain(builtin_claude_models())
    {
        if seen.insert(item.id.clone()) {
            models.push(item);
        }
    }
    let supported_efforts = union_efforts(&models, CLAUDE_EFFORTS, CLAUDE_EFFORTS);
    CliModelCatalog {
        models,
        default_model,
        default_effort: None,
        supported_efforts,
        source,
    }
}

pub fn list_models(provider: Provider) -> CliModelCatalog {
    match provider {
        Provider::Codex => {
            let dir = codex_home_dir();
            discover_codex_models_in(dir.as_deref())
        }
        Provider::Claude => {
            let home = home_dir();
            let config = claude_config_dir();
            discover_claude_models_in(home.as_deref(), config.as_deref())
        }
        Provider::Api => CliModelCatalog {
            models: Vec::new(),
            default_model: None,
            default_effort: None,
            supported_efforts: Vec::new(),
            source: "builtin",
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codex_discovery_reads_cache_and_config_and_filters_hidden_models() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("config.toml"),
            "# comment\nmodel = \"gpt-6.2-sol\"\nmodel_reasoning_effort = \"xhigh\"\n\n[tui]\nmodel = \"ignored\"\n",
        )
        .unwrap();
        fs::write(
            dir.path().join("models_cache.json"),
            serde_json::json!({
                "models": [
                    {
                        "slug": "gpt-6-luna",
                        "display_name": "GPT-6-Luna",
                        "priority": 2,
                        "visibility": "list",
                        "default_reasoning_level": "medium",
                        "supported_reasoning_levels": [
                            {"effort": "low"}, {"effort": "medium"}, {"effort": "high"}
                        ]
                    },
                    {
                        "slug": "hidden-model",
                        "display_name": "Hidden",
                        "priority": 0,
                        "visibility": "hide",
                        "supported_reasoning_levels": [{"effort": "low"}]
                    },
                    {
                        "slug": "-bad-flag",
                        "display_name": "Bad",
                        "priority": 0,
                        "visibility": "list"
                    },
                    {
                        "slug": "gpt-6.2-sol",
                        "display_name": "GPT-6.2-Sol",
                        "priority": 1,
                        "visibility": "list",
                        "default_reasoning_level": "low",
                        "supported_reasoning_levels": [
                            {"effort": "low"}, {"effort": "medium"}, {"effort": "high"}, {"effort": "xhigh"}
                        ]
                    }
                ]
            })
            .to_string(),
        )
        .unwrap();
        let catalog = discover_codex_models_in(Some(dir.path()));
        assert_eq!(catalog.source, "cache");
        assert_eq!(catalog.default_model.as_deref(), Some("gpt-6.2-sol"));
        assert_eq!(catalog.default_effort.as_deref(), Some("xhigh"));
        let ids: Vec<_> = catalog.models.iter().map(|m| m.id.as_str()).collect();
        assert_eq!(ids, vec!["gpt-6.2-sol", "gpt-6-luna"]);
        assert_eq!(catalog.supported_efforts, vec!["low", "medium", "high", "xhigh"]);
    }

    #[test]
    fn codex_discovery_falls_back_to_builtin_when_cache_missing() {
        let dir = tempfile::tempdir().unwrap();
        let catalog = discover_codex_models_in(Some(dir.path()));
        assert_eq!(catalog.source, "builtin");
        assert!(!catalog.models.is_empty());
        assert!(catalog.models.iter().any(|m| m.id == "gpt-5.4"));
    }

    #[test]
    fn claude_discovery_merges_settings_and_additional_options_with_builtin_models() {
        let dir = tempfile::tempdir().unwrap();
        let claude_dir = dir.path().join(".claude");
        fs::create_dir_all(&claude_dir).unwrap();
        fs::write(
            claude_dir.join("settings.json"),
            serde_json::json!({
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "cc-switch-token",
                    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                    "ANTHROPIC_MODEL": "glm-5.4[1m]",
                    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.4[1m]",
                    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "glm-5.4",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3-flash[1m]",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "glm-5.3-flash"
                }
            })
            .to_string(),
        )
        .unwrap();
        fs::write(
            dir.path().join(".claude.json"),
            serde_json::json!({
                "additionalModelOptionsCache": [
                    {"value": "claude-fable-5-2[1m]", "label": "Fable", "description": "Fable 5.2"},
                    {"value": "--injected", "label": "Bad"}
                ]
            })
            .to_string(),
        )
        .unwrap();
        let catalog = discover_claude_models_in(Some(dir.path()), Some(&claude_dir));
        assert_eq!(catalog.source, "cache");
        assert_eq!(catalog.default_model.as_deref(), Some("glm-5.4[1m]"));
        assert_eq!(catalog.models[0].id, "glm-5.4[1m]");
        assert_eq!(catalog.models[0].label, "glm-5.4");
        assert_eq!(catalog.models[1].id, "glm-5.3-flash[1m]");
        assert_eq!(catalog.models[2].id, "claude-fable-5-2[1m]");
        assert!(catalog.models.iter().any(|m| m.id == "sonnet"));
        assert!(catalog.models.iter().any(|m| m.id == "opus"));
        assert!(!catalog.models.iter().any(|m| m.id == "--injected"));
    }

    #[test]
    fn claude_auth_detects_env_and_settings_json_api_credentials() {
        let dir = tempfile::tempdir().unwrap();
        assert!(!claude_auth_available_in(Some(dir.path()), None, None));
        assert!(claude_auth_available_in(Some(dir.path()), Some("sk-ant-test"), None));
        assert!(claude_auth_available_in(Some(dir.path()), None, Some("auth-token")));

        fs::write(
            dir.path().join("settings.json"),
            serde_json::json!({"env": {"ANTHROPIC_BASE_URL": "https://example.com"}}).to_string(),
        )
        .unwrap();
        assert!(!claude_auth_available_in(Some(dir.path()), None, None));

        fs::write(
            dir.path().join("settings.json"),
            serde_json::json!({"env": {"ANTHROPIC_AUTH_TOKEN": "cc-switch-token"}}).to_string(),
        )
        .unwrap();
        assert!(claude_auth_available_in(Some(dir.path()), None, None));

        fs::write(
            dir.path().join("settings.json"),
            serde_json::json!({"apiKeyHelper": "/usr/local/bin/get-key"}).to_string(),
        )
        .unwrap();
        assert!(claude_auth_available_in(Some(dir.path()), None, None));
    }

    #[test]
    fn model_and_effort_validators_reject_flags_and_unknown_efforts() {
        assert!(valid_cli_model("gpt-6.2-sol"));
        assert!(valid_cli_model("claude-fable-5-2[1m]"));
        assert!(!valid_cli_model(""));
        assert!(!valid_cli_model("--model"));
        assert!(!valid_cli_model("bad\nmodel"));
        assert!(valid_reasoning_effort(Provider::Codex, "xhigh"));
        assert!(valid_reasoning_effort(Provider::Codex, "ultra"));
        assert!(valid_reasoning_effort(Provider::Claude, "xhigh"));
        assert!(valid_reasoning_effort(Provider::Claude, "max"));
        assert!(!valid_reasoning_effort(Provider::Claude, "ultra"));
        assert!(!valid_reasoning_effort(Provider::Codex, "extreme"));
    }
}
