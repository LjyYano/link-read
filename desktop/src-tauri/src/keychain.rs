//! Legacy Keychain migration: read and delete existing credentials only.
//! New credentials are managed by credentials.rs; no Keychain write API remains.

#[cfg(target_os = "macos")]
mod platform {
    use std::{
        ffi::{c_char, c_void},
        ptr,
        slice,
    };

    const SERVICE: &str = "com.yano.linkread.direct-api";
    const ACCOUNT: &str = "model-api-token";
    const NOT_FOUND: i32 = -25300;

    #[link(name = "Security", kind = "framework")]
    extern "C" {
        fn SecKeychainFindGenericPassword(
            keychain_or_array: *const c_void,
            service_length: u32,
            service_name: *const c_char,
            account_length: u32,
            account_name: *const c_char,
            password_length: *mut u32,
            password_data: *mut *mut c_void,
            item_ref: *mut *mut c_void,
        ) -> i32;
        fn SecKeychainItemFreeContent(attributes: *mut c_void, data: *mut c_void) -> i32;
        fn SecKeychainItemDelete(item_ref: *mut c_void) -> i32;
    }

    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CFRelease(value: *const c_void);
    }

    fn status_message(code: i32) -> String {
        format!("macOS 钥匙串操作失败（OSStatus {code}）")
    }

    fn find(item: *mut *mut c_void, length: *mut u32, data: *mut *mut c_void) -> i32 {
        unsafe {
            SecKeychainFindGenericPassword(
                ptr::null(),
                SERVICE.len() as u32,
                SERVICE.as_ptr().cast(),
                ACCOUNT.len() as u32,
                ACCOUNT.as_ptr().cast(),
                length,
                data,
                item,
            )
        }
    }

    pub fn load() -> Result<Option<String>, String> {
        let mut length = 0_u32;
        let mut data: *mut c_void = ptr::null_mut();
        let mut item: *mut c_void = ptr::null_mut();
        let status = find(&mut item, &mut length, &mut data);
        if status == NOT_FOUND {
            return Ok(None);
        }
        if status != 0 {
            return Err(status_message(status));
        }
        let bytes = if length == 0 || data.is_null() {
            Vec::new()
        } else {
            unsafe { slice::from_raw_parts(data.cast::<u8>(), length as usize).to_vec() }
        };
        unsafe {
            if !data.is_null() {
                SecKeychainItemFreeContent(ptr::null_mut(), data);
            }
            if !item.is_null() {
                CFRelease(item);
            }
        }
        String::from_utf8(bytes)
            .map(Some)
            .map_err(|_| "钥匙串中的 Token 不是有效 UTF-8".to_owned())
    }

    pub fn delete() -> Result<(), String> {
        let mut item: *mut c_void = ptr::null_mut();
        let result = find(&mut item, ptr::null_mut(), ptr::null_mut());
        if result == NOT_FOUND {
            return Ok(());
        }
        if result != 0 {
            return Err(status_message(result));
        }
        let status = unsafe { SecKeychainItemDelete(item) };
        unsafe { CFRelease(item) };
        if status != 0 {
            return Err(status_message(status));
        }
        Ok(())
    }
}

#[cfg(target_os = "macos")]
pub use platform::{delete, load};

#[cfg(not(target_os = "macos"))]
pub fn load() -> Result<Option<String>, String> {
    Err("API Token 仅支持 macOS 钥匙串".into())
}
#[cfg(not(target_os = "macos"))]
pub fn delete() -> Result<(), String> {
    Err("API Token 仅支持 macOS 钥匙串".into())
}
