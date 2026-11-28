//! Enumerate locally installed font families without network access or external tools.

use std::collections::BTreeSet;

pub fn valid_font_name(name: &str) -> bool {
    let trimmed = name.trim();
    if trimmed.is_empty() || trimmed.len() > 128 {
        return false;
    }
    !trimmed.chars().any(|ch| {
        ch.is_control()
            || matches!(
                ch,
                '<' | '>' | '{' | '}' | ';' | ':' | '"' | '\'' | '\\' | '/' | '(' | ')' | '*'
            )
    })
}

#[cfg(target_os = "macos")]
pub fn installed_font_families() -> Vec<String> {
    use std::ffi::{c_char, c_void, CStr};

    type CFArrayRef = *const c_void;
    type CFStringRef = *const c_void;
    type CFIndex = isize;
    const K_CF_STRING_ENCODING_UTF8: u32 = 0x0800_0100;

    #[link(name = "CoreText", kind = "framework")]
    extern "C" {
        fn CTFontManagerCopyAvailableFontFamilyNames() -> CFArrayRef;
    }

    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CFArrayGetCount(the_array: CFArrayRef) -> CFIndex;
        fn CFArrayGetValueAtIndex(the_array: CFArrayRef, idx: CFIndex) -> *const c_void;
        fn CFStringGetLength(the_string: CFStringRef) -> CFIndex;
        fn CFStringGetMaximumSizeForEncoding(length: CFIndex, encoding: u32) -> CFIndex;
        fn CFStringGetCString(
            the_string: CFStringRef,
            buffer: *mut c_char,
            buffer_size: CFIndex,
            encoding: u32,
        ) -> u8;
        fn CFRelease(cf: *const c_void);
    }

    struct CfReleaseGuard(*const c_void);
    impl Drop for CfReleaseGuard {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe { CFRelease(self.0) };
            }
        }
    }

    let array = unsafe { CTFontManagerCopyAvailableFontFamilyNames() };
    if array.is_null() {
        return Vec::new();
    }
    let _guard = CfReleaseGuard(array);
    let count = unsafe { CFArrayGetCount(array) };
    if count <= 0 {
        return Vec::new();
    }

    let mut families = BTreeSet::new();
    for idx in 0..count {
        let cf_str = unsafe { CFArrayGetValueAtIndex(array, idx) };
        if cf_str.is_null() {
            continue;
        }
        let len = unsafe { CFStringGetLength(cf_str) };
        if len <= 0 {
            continue;
        }
        let max_bytes = unsafe { CFStringGetMaximumSizeForEncoding(len, K_CF_STRING_ENCODING_UTF8) };
        if max_bytes <= 0 || max_bytes > 512 {
            continue;
        }
        let mut buf = vec![0u8; (max_bytes as usize) + 1];
        let ok = unsafe {
            CFStringGetCString(
                cf_str,
                buf.as_mut_ptr() as *mut c_char,
                buf.len() as CFIndex,
                K_CF_STRING_ENCODING_UTF8,
            )
        };
        if ok == 0 {
            continue;
        }
        let Ok(cstr) = CStr::from_bytes_until_nul(&buf) else {
            continue;
        };
        let Ok(name) = cstr.to_str() else {
            continue;
        };
        let trimmed = name.trim();
        if trimmed.starts_with('.') || !valid_font_name(trimmed) {
            continue;
        }
        families.insert(trimmed.to_owned());
    }
    families.into_iter().collect()
}

#[cfg(not(target_os = "macos"))]
pub fn installed_font_families() -> Vec<String> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::{installed_font_families, valid_font_name};

    #[test]
    fn valid_font_name_accepts_safe_names_and_rejects_css_metacharacters() {
        assert!(valid_font_name("PingFang SC"));
        assert!(valid_font_name("LXGW WenKai"));
        assert!(valid_font_name("Iowan Old Style"));
        assert!(!valid_font_name(""));
        assert!(!valid_font_name("   "));
        assert!(!valid_font_name("Font; } body { display: none"));
        assert!(!valid_font_name("Font</style><script>"));
        assert!(!valid_font_name(&"a".repeat(129)));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_font_enumeration_returns_sorted_safe_families() {
        let fonts = installed_font_families();
        assert!(!fonts.is_empty(), "macOS should expose system font families");
        assert!(fonts.iter().all(|name| !name.starts_with('.') && valid_font_name(name)));
        assert!(fonts.windows(2).all(|pair| pair[0] < pair[1]));
    }
}
