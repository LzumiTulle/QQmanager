use std::mem::{size_of, transmute};
use windows_sys::{
    core::w,
    Win32::{
        System::LibraryLoader::{GetModuleHandleW, GetProcAddress},
        UI::{
            Accessibility::{HCF_HIGHCONTRASTON, HIGHCONTRASTW},
            WindowsAndMessaging::{SystemParametersInfoW, SPI_GETHIGHCONTRAST},
        },
    },
};

/// Tauri themes the caption and menu bar. Win32 separately caches the popup
/// menu palette at process scope, including the menus opened from the tray.
pub fn set_popup_theme(theme: tauri::Theme) {
    let os = windows_version::OsVersion::current();
    // UxTheme ordinal 135 had a different signature before Windows 10 1903.
    if os.major != 10 || os.build < 18362 {
        return;
    }
    unsafe {
        let library = GetModuleHandleW(w!("uxtheme.dll"));
        if library.is_null() {
            return;
        }
        let (Some(set_mode), Some(flush)) = (
            GetProcAddress(library, 135usize as *const u8),
            GetProcAddress(library, 136usize as *const u8),
        ) else {
            return;
        };
        let mut contrast = HIGHCONTRASTW {
            cbSize: size_of::<HIGHCONTRASTW>() as u32,
            ..Default::default()
        };
        let known = SystemParametersInfoW(
            SPI_GETHIGHCONTRAST, contrast.cbSize, (&raw mut contrast).cast(), 0,
        ) != 0;
        // PreferredAppMode values are Default, AllowDark, ForceDark, ForceLight.
        let mode = if !known || contrast.dwFlags & HCF_HIGHCONTRASTON != 0 {
            0
        } else if theme == tauri::Theme::Dark {
            2
        } else {
            3
        };
        let set_mode = transmute::<_, unsafe extern "system" fn(i32) -> i32>(set_mode);
        let flush = transmute::<_, unsafe extern "system" fn()>(flush);
        set_mode(mode);
        flush();
    }
}
