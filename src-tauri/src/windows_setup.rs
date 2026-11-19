//! Installs only our plugin into a user-selected, already working LiteLoader directory.
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
fn install(app: &tauri::AppHandle, root: PathBuf) -> Result<(), Box<dyn std::error::Error>> {
    if !root.join("plugins").is_dir() || !root.join("data").is_dir() {
        return Err("请选择包含 plugins 和 data 子目录的 LiteLoaderQQNT 用户目录。此助手不会安装加载器或修改 QQ。".into());
    }
    let c = request("/api/connector")?;
    let account = c["selected_account"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or("请先在 QQmanager 的接入页保存你的 QQ 账号，然后再次安装插件")?;
    let session = request("/api/session")?;
    let config: Value = client()
        .post(format!("{ORIGIN}/api/native/pair"))
        .header("X-CSRF-Token", session["csrf"].as_str().ok_or("会话无效")?)
        .json(&json!({"account":account,"groups":c["selected"]}))
        .send()?
        .error_for_status()?
        .json()?;
    if config["token"].as_str().is_none_or(|t| t.len() < 32) {
        return Err("配对信息无效".into());
    }
    let src = app.path().resource_dir()?.join("qq-plugin");
    let dest = root.join("plugins/qqmanager_native");
    let data = root.join("data/qqmanager_native");
    for p in [&dest, &data] {
        if p.symlink_metadata()
            .is_ok_and(|m| m.file_type().is_symlink())
        {
            return Err("插件路径是链接，请选择实际目录".into());
        }
    }
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis();
    let backup = root.join(format!("qqmanager-backup-{stamp}"));
    fs::create_dir_all(&backup)?;
    if dest.exists() {
        fs::rename(&dest, backup.join("plugin"))?;
    }
    fs::create_dir_all(&dest)?;
    fs::create_dir_all(&data)?;
    // Copy only our distributable files; never copy QQ binaries or other plugins.
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        if entry.file_type()?.is_file() {
            fs::copy(entry.path(), dest.join(entry.file_name()))?;
        }
    }
    let target = data.join("config.json");
    if target.exists() {
        fs::copy(&target, backup.join("config.json"))?;
    }
    fs::write(target, serde_json::to_vec_pretty(&config)?)?;
    Ok(())
}
pub fn choose(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        if let Some(folder) = app
            .dialog()
            .file()
            .set_title("选择 LiteLoaderQQNT 用户目录（包含 plugins 和 data）")
            .blocking_pick_folder()
        {
            match folder.into_path().map_err(|e|e.to_string()).and_then(|root|install(&app,root).map_err(|e|e.to_string())){Ok(())=>error(&app,"插件已安装并配对。请正常重启 QQ，再到接入页检查群列表和收集状态。旧文件已备份在所选目录。"),Err(e)=>error(&app,e)}
        }
    });
}
