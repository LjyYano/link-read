//! Canonical video/episode links shared by preview, duplicate checks and generation.
use tauri::Url;

fn decode_ampersands(text: &str) -> String {
    let mut chunks = text.split('&');
    let mut decoded = String::with_capacity(text.len());
    decoded.push_str(chunks.next().unwrap_or_default());
    for chunk in chunks {
        decoded.push('&');
        let remainder = chunk.split_once(';').and_then(|(entity, remainder)| {
            ["amp", "#38", "#x26"].iter().any(|name| entity.eq_ignore_ascii_case(name))
                .then_some(remainder)
        });
        decoded.push_str(remainder.unwrap_or(chunk));
    }
    decoded
}

fn pasted_url(source: &str) -> Result<String, String> {
    let mut text = String::new();
    let mut chars = source.trim().chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\\' {
            while chars.peek() == Some(&'\\') { chars.next(); }
            if chars.peek().is_some_and(|c| ":/?.=&_#%-".contains(*c)) { continue; }
        }
        text.push(c);
    }
    let text = decode_ampersands(&text);
    let lower = text.to_ascii_lowercase();
    let mut values = Vec::<String>::new();
    let mut cursor = 0;
    while cursor < text.len() {
        let Some(offset) = lower[cursor..].find("http") else { break };
        let start = cursor + offset;
        if !lower[start..].starts_with("https://") && !lower[start..].starts_with("http://") {
            cursor = start + 4; continue;
        }
        let rest = &text[start..];
        let len = rest.find(|c: char| c.is_whitespace() || "<>\"[]【】「」".contains(c)).unwrap_or(rest.len());
        let found = rest[..len].trim_end_matches(|c: char| ").,;!，。；！）'".contains(c)).to_owned();
        if !values.contains(&found) { values.push(found); }
        cursor = start + len;
    }
    if values.len() > 1 { return Err("检测到多个链接，请一次提交一个视频或播客单集".into()); }
    Ok(values.pop().unwrap_or(text))
}

fn id(value: &str, prefix: &str, length: usize) -> bool {
    value.len() == length && value.starts_with(prefix) && value.bytes().all(|c| c.is_ascii_alphanumeric() || (prefix.is_empty() && (c == b'_' || c == b'-')))
}

fn podcast_id(value: &str) -> bool {
    !value.is_empty() && value.len() <= 20 && !value.starts_with('0')
        && value.bytes().all(|c| c.is_ascii_digit())
}

pub fn canonical(source: &str) -> Result<Url, String> {
    let message = "请输入 YouTube、B 站视频或 Apple 播客单集的 HTTPS 链接";
    let text = pasted_url(source)?;
    let u = Url::parse(&text).map_err(|_| message.to_owned())?;
    if u.scheme() != "https" || !u.username().is_empty() || u.password().is_some() || u.port().is_some() {
        return Err(message.into());
    }
    // Url normalizes an explicit :443 away. Reject explicit ports consistently.
    let authority = text.split("://").nth(1).unwrap_or("").split(['/', '?', '#']).next().unwrap_or("");
    if authority.is_empty() || authority.contains(':') || authority.contains('@') {
        return Err(message.into());
    }
    let parts: Vec<&str> = u.path().trim_matches('/').split('/').collect();
    let values = |name: &str| -> Vec<String> { u.query_pairs().filter(|(k, _)| k == name).map(|(_, v)| v.into_owned()).collect() };
    let host = u.host_str().unwrap_or("");
    let target = if ["bilibili.com", "www.bilibili.com", "m.bilibili.com"].contains(&host) {
        let ids = values("bvid");
        let bvid = if parts.len() == 2 && parts[0] == "video" {
            if !ids.is_empty() && (ids.len() != 1 || ids[0] != parts[1]) { return Err("视频编号冲突".into()); }
            parts[1].to_owned()
        } else if ["/list/watchlater", "/medialist/play/watchlater"].contains(&u.path().trim_end_matches('/')) && ids.len() == 1 {
            ids[0].clone()
        } else { return Err(message.into()); };
        let pages = values("p");
        let page = pages.first().map(String::as_str).unwrap_or("1");
        let number = page.parse::<u32>().unwrap_or(0);
        if !id(&bvid, "BV", 12) || pages.len() > 1 || page.len() > 4 || !page.bytes().all(|x| x.is_ascii_digit()) || !(1..=1000).contains(&number) {
            return Err("B 站视频编号或分 P 参数无效".into());
        }
        format!("https://www.bilibili.com/video/{bvid}?p={number}")
    } else if ["b23.tv", "www.b23.tv"].contains(&host) {
        if parts.len() != 1 || parts[0].is_empty() || !parts[0].bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') { return Err(message.into()); }
        format!("https://b23.tv/{}", parts[0])
    } else if ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"].contains(&host) {
        let ids = values("v");
        let video = if ["youtu.be", "www.youtu.be"].contains(&host) && parts.len() == 1 { parts[0] }
            else if parts.len() == 1 && parts[0] == "watch" && ids.len() == 1 { &ids[0] }
            else if parts.len() == 2 && ["shorts", "embed", "live"].contains(&parts[0]) { parts[1] }
            else { return Err(message.into()); };
        if !id(video, "", 11) { return Err(message.into()); }
        format!("https://www.youtube.com/watch?v={video}")
    } else if host == "podcasts.apple.com" {
        // Url removes dot segments; reject them in the original pasted path first.
        let authority_path = text.split_once("://").map(|(_, rest)| rest).unwrap_or("").split(['?', '#']).next().unwrap_or("");
        let raw_path = authority_path.find('/').map(|start| &authority_path[start..]).unwrap_or("");
        if raw_path.split('/').any(|segment| {
            let dots = segment.to_ascii_lowercase().replace("%2e", ".");
            dots == "." || dots == ".."
        }) { return Err("Apple 播客链接路径无效".into()); }
        // Keep IDs as strings: Apple IDs must not lose precision across layers.
        let path = u.path().strip_prefix('/').unwrap_or(u.path());
        let path = path.strip_suffix('/').unwrap_or(path);
        let segments: Vec<&str> = path.split('/').collect();
        let (country, rest) = if segments.first() == Some(&"podcast") {
            ("us".to_owned(), &segments[..])
        } else if segments.first().is_some_and(|s| s.len() == 2 && s.bytes().all(|c| c.is_ascii_alphabetic())) {
            (segments[0].to_ascii_lowercase(), &segments[1..])
        } else { return Err(message.into()); };
        let episodes = values("i");
        if !(rest.len() == 2 || rest.len() == 3) || rest[0] != "podcast"
            || rest.iter().any(|s| s.is_empty()) || episodes.len() != 1 || !podcast_id(&episodes[0]) {
            return Err("请复制 Apple 播客的单集链接，需包含 i= 单集编号".into());
        }
        let show_id = rest.last().and_then(|s| s.strip_prefix("id")).unwrap_or("");
        if !podcast_id(show_id) { return Err(message.into()); }
        format!("https://podcasts.apple.com/{country}/podcast/id{show_id}?i={}", episodes[0])
    } else { return Err(message.into()); };
    Url::parse(&target).map_err(|_| message.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_matches_shared_link_cases() {
        let cases: serde_json::Value = serde_json::from_str(include_str!("../../../tests/fixtures/video_links.json")).unwrap();
        for case in cases.as_array().unwrap() {
            let actual = canonical(case["input"].as_str().unwrap()).ok().map(|u| u.to_string());
            assert_eq!(actual.as_deref(), case["output"].as_str(), "{}", case["input"]);
        }
    }
}
