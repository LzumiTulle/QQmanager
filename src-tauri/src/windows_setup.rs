//! Installs only our plugin into an existing LiteLoader profile.
use super::*;

#[cfg(windows)]
pub fn open_qq() -> Result<(), String> {
    use windows_registry::{CURRENT_USER, LOCAL_MACHINE};
    for root in [CURRENT_USER, LOCAL_MACHINE] {
        for branch in [r"Software\Microsoft\Windows\CurrentVersion", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion"] {
            let app_path = root.open(format!(r"{branch}\App Paths\QQ.exe")).ok()
                .and_then(|key| key.get_string("").ok());
            let uninstall = root.open(format!(r"{branch}\Uninstall\QQ")).ok();
            let folder = uninstall.as_ref().and_then(|key| key.get_string("InstallLocation").ok());
            let icon = uninstall.as_ref().and_then(|key| key.get_string("DisplayIcon").ok());
            let candidates = [
                app_path.map(|s| PathBuf::from(s.trim_matches('"'))),
                folder.map(|s| PathBuf::from(s.trim_matches('"')).join("QQ.exe")),
                icon.map(|s| PathBuf::from(s.rsplit_once(',').map_or(s.as_str(), |(path, _)| path).trim_matches('"'))),
            ];
            for path in candidates.into_iter().flatten() {
                if path.is_absolute() && path.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.eq_ignore_ascii_case("QQ.exe")) && path.is_file() {
                    return Command::new(path).spawn().map(|_| ()).map_err(|e| format!("无法打开 QQ：{e}"));
                }
            }
        }
    }
    Err("没有找到已注册的 QQ 安装位置，请从 Windows 开始菜单打开 QQ。".into())
}
fn is_profile(path: &std::path::Path) -> bool {
    path.is_absolute() && path.join("plugins").is_dir() && path.join("data").is_dir()
}

fn detect_profile(app: &tauri::AppHandle) -> Option<PathBuf> {
    let service = app.state::<Arc<Service>>();
    let prefs = service.prefs.lock().unwrap();
    let configured = std::env::var_os("LITELOADERQQNT_PROFILE").map(PathBuf::from);
    let saved = prefs["pluginProfile"].as_str().map(PathBuf::from);
    let bases = [
        app.path().document_dir().ok(),
        std::env::var_os("APPDATA").map(PathBuf::from),
    ];
    select_profile(configured, saved, bases.into_iter().flatten())
}

fn select_profile(
    configured: Option<PathBuf>,
    saved: Option<PathBuf>,
    bases: impl Iterator<Item = PathBuf>,
) -> Option<PathBuf> {
    if let Some(root) = configured.filter(|p| !p.as_os_str().is_empty()) {
        return is_profile(&root).then_some(root);
    }
    if let Some(root) = saved.filter(|p| is_profile(p)) {
        return Some(root);
    }
    let mut found = Vec::new();
    for base in bases {
        let root = base.join("LiteLoaderQQNT");
        if is_profile(&root) && !found.contains(&root) {
            found.push(root);
        }
    }
    if found.len() == 1 {
        found.pop()
    } else {
        None
    }
}

fn install(
    app: &tauri::AppHandle,
    root: &std::path::Path,
) -> Result<(), Box<dyn std::error::Error>> {
    if !is_profile(root) {
        return Err(
            "请选择已有 LiteLoaderQQNT 用户目录，其中应有 plugins 和 data 两个子目录。".into(),
        );
    }
    let session = request("/api/session")?;
    let response = client()
        .get(format!("{ORIGIN}/api/native/install-config"))
        .header("X-CSRF-Token", session["csrf"].as_str().ok_or("会话无效")?)
        .send()?;
    if response.status().as_u16() == 409 {
        return Err("请先在 QQmanager 保存 QQ 账号，再安装消息插件。".into());
    }
    let config: Value = response.error_for_status()?.json()?;
    if config["token"].as_str().is_none_or(|t| t.len() < 32) {
        return Err("配对信息无效".into());
    }
    let src = app.path().resource_dir()?.join("qq-plugin");
    write_plugin(root, &src, &serde_json::to_vec_pretty(&config)?)?;
    Ok(())
}

const PLUGIN_FILES: [&str; 4] = ["manifest.json", "core.cjs", "media.cjs", "main.cjs"];

fn write_plugin(
    root: &std::path::Path,
    src: &std::path::Path,
    config: &[u8],
) -> std::io::Result<()> {
    use std::io::{Error, ErrorKind};
    let plugins = root.join("plugins");
    let data = root.join("data");
    let dest = plugins.join("qqmanager_native");
    let settings = data.join("qqmanager_native");
    let target = settings.join("config.json");
    for path in [&plugins, &data, &dest, &settings, &target] {
        match fs::symlink_metadata(path) {
            Ok(meta) => {
                #[cfg(windows)]
                let linked = std::os::windows::fs::MetadataExt::file_attributes(&meta) & 0x400 != 0;
                #[cfg(not(windows))]
                let linked = meta.file_type().is_symlink();
                if linked
                    || (path == &target && !meta.is_file())
                    || (path != &target && !meta.is_dir())
                {
                    return Err(Error::other(
                        "插件或配置路径类型异常，请检查 LiteLoaderQQNT 用户目录。",
                    ));
                }
            }
            Err(e) if e.kind() == ErrorKind::NotFound => (),
            Err(e) => return Err(e),
        }
    }
    if !plugins.is_dir() || !data.is_dir() {
        return Err(Error::other("插件目录已变更，请重新选择。"));
    }
    if fs::read(&target).ok().as_deref() == Some(config)
        && PLUGIN_FILES.iter().all(|name| {
            fs::read(src.join(name)).is_ok_and(|bytes| {
                fs::read(dest.join(name)).is_ok_and(|installed| installed == bytes)
            })
        })
    {
        return Ok(());
    }

    // Stage complete files on the same volume before touching an installed copy.
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(Error::other)?
        .as_nanos();
    let backup = root.join(format!("qqmanager-backup-{stamp}"));
    fs::create_dir(&backup)?;
    let staged = backup.join("new-plugin");
    let new_config = backup.join("new-config.json");
    let old = backup.join("plugin");
    let old_config = backup.join("config.json");
    let mut saved_plugin = false;
    let mut saved_config = false;
    let mut promoted = false;
    let result = (|| {
        fs::create_dir(&staged)?;
        for name in PLUGIN_FILES {
            fs::copy(src.join(name), staged.join(name))?;
        }
        fs::write(&new_config, config)?;
        fs::create_dir_all(&settings)?;
        if dest.exists() {
            fs::rename(&dest, &old)?;
            saved_plugin = true;
        }
        if target.exists() {
            fs::rename(&target, &old_config)?;
            saved_config = true;
        }
        fs::rename(&staged, &dest)?;
        promoted = true;
        fs::rename(&new_config, &target)
    })();
    if result.is_err() {
        if promoted {
            fs::rename(&dest, &staged)?;
        }
        if saved_plugin {
            fs::rename(&old, &dest)?;
        }
        if saved_config {
            fs::rename(&old_config, &target)?;
        }
        let _ = fs::remove_dir_all(&staged);
        let _ = fs::remove_file(&new_config);
    }
    let _ = fs::remove_dir(&backup); // Keep non-empty backups of previous installations.
    result
}

pub fn choose(app: tauri::AppHandle) {
    static INSTALLING: Mutex<()> = Mutex::new(());
    std::thread::spawn(move || {
        let Ok(_guard) = INSTALLING.try_lock() else {
            return;
        };
        let root = match detect_profile(&app) {
            Some(root) => root,
            None => {
                use tauri_plugin_dialog::MessageDialogButtons;
                if !app.dialog().message("自动安装需要已有的 LiteLoaderQQNT 环境。如果你已在 QQ 中启用它，可以手动选择包含 plugins 和 data 的用户目录。")
                    .title("未能定位 QQ 插件目录")
                    .buttons(MessageDialogButtons::OkCancelCustom("选择已有目录".into(), "暂不安装".into()))
                    .blocking_show() { return; }
                let Some(folder) = app
                    .dialog()
                    .file()
                    .set_title("选择 LiteLoaderQQNT 用户目录")
                    .blocking_pick_folder()
                else {
                    return;
                };
                match folder.into_path() {
                    Ok(root) => root,
                    Err(e) => {
                        error(&app, e.to_string());
                        return;
                    }
                }
            }
        };
        match install(&app, &root) {
            Ok(()) => {
                let service = app.state::<Arc<Service>>();
                service.prefs.lock().unwrap()["pluginProfile"] = json!(root);
                service.save();
                error(&app, "插件文件和本机配对配置已就绪。请正常退出并重新打开 QQ，再到 QQmanager 选择需要收集的群。");
            }
            Err(e) => error(&app, format!("未能安装 QQ 消息插件：{e}")),
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn install_and_retry_in_a_disposable_profile() {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let profile = std::env::temp_dir().join(format!("qqmanager-plugin-test-{unique}"));
        let src = profile.join("bundle");
        for dir in [&src, &profile.join("plugins"), &profile.join("data")] {
            fs::create_dir_all(dir).unwrap();
        }
        assert!(is_profile(&profile));
        assert_eq!(
            select_profile(Some(profile.clone()), None, std::iter::empty()),
            Some(profile.clone())
        );
        assert_eq!(
            select_profile(None, Some(profile.clone()), std::iter::empty()),
            Some(profile.clone())
        );
        assert!(
            select_profile(Some(src.clone()), Some(profile.clone()), std::iter::empty()).is_none()
        );
        for name in PLUGIN_FILES {
            fs::write(src.join(name), name).unwrap();
        }
        write_plugin(&profile, &src, b"fixture").unwrap();
        write_plugin(&profile, &src, b"fixture").unwrap();
        assert_eq!(fs::read_dir(&profile).unwrap().count(), 3);
        let target = profile.join("plugins/qqmanager_native/main.cjs");
        let config = profile.join("data/qqmanager_native/config.json");
        fs::remove_file(src.join("main.cjs")).unwrap();
        assert!(write_plugin(&profile, &src, b"changed").is_err());
        assert_eq!(fs::read(&target).unwrap(), b"main.cjs");
        assert_eq!(fs::read(&config).unwrap(), b"fixture");
        fs::write(src.join("main.cjs"), b"new").unwrap();
        write_plugin(&profile, &src, b"changed").unwrap();
        assert_eq!(fs::read(target).unwrap(), b"new");
        assert_eq!(fs::read(config).unwrap(), b"changed");
        let backup = fs::read_dir(&profile)
            .unwrap()
            .flatten()
            .find(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with("qqmanager-backup-")
            })
            .unwrap();
        assert_eq!(
            fs::read(backup.path().join("plugin/main.cjs")).unwrap(),
            b"main.cjs"
        );
        assert_eq!(
            fs::read(backup.path().join("config.json")).unwrap(),
            b"fixture"
        );
        let bases = [profile.join("documents"), profile.join("roaming")];
        for base in &bases {
            for name in ["data", "plugins"] {
                fs::create_dir_all(base.join("LiteLoaderQQNT").join(name)).unwrap();
            }
        }
        assert_eq!(
            select_profile(None, None, bases.iter().take(1).cloned()),
            Some(bases[0].join("LiteLoaderQQNT"))
        );
        assert!(select_profile(None, None, bases.into_iter()).is_none());
        fs::remove_dir_all(profile).unwrap();
    }
}
