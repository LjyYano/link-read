use std::{fs, path::Path};

/// Add only the stored note's actual takeaways to the desktop preview.
/// The source HTML and JSON files remain untouched.
pub fn with_takeaways(mut html: String, article_path: &Path) -> String {
    let Some(folder) = article_path.parent() else {
        return html;
    };
    let note_path = folder.join("note.json");
    if note_path.is_symlink() {
        return html;
    }
    let note = fs::read_to_string(note_path)
        .ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok());
    let Some(note) = note else {
        return html;
    };
    if note.get("document_type").and_then(|value| value.as_str()) != Some("linkread_note") {
        return html;
    }
    let Some(values) = note
        .pointer("/analysis/takeaways")
        .and_then(|value| value.as_array())
    else {
        return html;
    };
    if values.is_empty() || values.iter().any(|value| !value.is_string()) {
        return html;
    }
    let Some(end) = html.rfind("</body>") else {
        return html;
    };
    let mut template = String::from("<template id=\"desktop-note-takeaways\"><ul>");
    for value in values {
        let text = value.as_str().expect("validated takeaway string");
        template.push_str("<li>");
        template.push_str(&escape_html(text));
        template.push_str("</li>");
    }
    template.push_str("</ul></template>");
    html.insert_str(end, &template);
    html
}

fn escape_html(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

#[cfg(test)]
mod tests {
    use super::with_takeaways;
    use std::fs;

    #[test]
    fn injected_takeaways_escape_untrusted_text_and_preserve_scripts() {
        let directory = tempfile::tempdir().unwrap();
        let article = directory.path().join("article.html");
        let html = "<body><script>const template = '</body>';</script></body>";
        let note = serde_json::json!({
            "document_type": "linkread_note",
            "analysis": {"takeaways": ["</template><script>alert('x' & \"y\")</script>"]}
        });
        fs::write(directory.path().join("note.json"), note.to_string()).unwrap();
        let output = with_takeaways(html.to_owned(), &article);
        assert!(output.starts_with("<body><script>const template = '</body>';</script>"));
        assert!(output.contains(
            "&lt;/template&gt;&lt;script&gt;alert(&#39;x&#39; &amp; &quot;y&quot;)&lt;/script&gt;"
        ));
        assert_eq!(output.matches("<script>").count(), 1);
        assert!(output.ends_with("</ul></template></body>"));
    }

    #[test]
    fn missing_empty_or_invalid_takeaways_leave_html_unchanged() {
        let directory = tempfile::tempdir().unwrap();
        let article = directory.path().join("article.html");
        let html = "<body>正文</body>";
        assert_eq!(with_takeaways(html.to_owned(), &article), html);
        for note in [
            "invalid json",
            r#"{"document_type":"linkread_note","analysis":{"takeaways":[]}}"#,
            r#"{"document_type":"linkread_note","analysis":{"takeaways":[42]}}"#,
            r#"{"analysis":{"takeaways":["wrong document type"]}}"#,
        ] {
            fs::write(directory.path().join("note.json"), note).unwrap();
            assert_eq!(with_takeaways(html.to_owned(), &article), html);
        }
    }
}
