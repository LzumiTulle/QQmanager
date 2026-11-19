#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use serde_json::{json, Value};
use std::{
    fs,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::Duration,
};
use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, WebviewUrl, WebviewWindowBuilder,
};
#[cfg(not(windows))]
use tauri::menu::Submenu;
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;
mod windows_setup;
#[cfg(windows)]
mod windows_theme;
const ORIGIN: &str = "http://127.0.0.1:8765";
fn client() -> reqwest::blocking::Client {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3))
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .unwrap()
}
fn request(route: &str) -> Result<Value, Box<dyn std::error::Error>> {
    Ok(client()
        .get(format!("{ORIGIN}{route}"))
        .send()?
        .error_for_status()?
        .json()?)
}
fn health(v: &Value) -> bool {
    v["app"] == "QQmanager" && v["protocol"] == 1
}
fn local(u: &tauri::Url) -> bool {
    u.scheme() == "http"
        && u.host_str() == Some("127.0.0.1")
        && u.port() == Some(8765)
        && u.username().is_empty()
        && u.password().is_none()
}
fn external(u: &tauri::Url) -> bool {
    matches!(u.scheme(), "http" | "https")
        && u.username().is_empty()
        && u.password().is_none()
        && !local(u)
}
struct Service {
    child: Mutex<Option<Child>>,
    data: PathBuf,
    prefs_path: PathBuf,
    prefs: Mutex<Value>,
    stopping: AtomicBool,
}
impl Service {
    fn save(&self) {
        if let Ok(p) = self.prefs.lock() {
            let _ = fs::write(&self.prefs_path, serde_json::to_vec_pretty(&*p).unwrap());
        }
    }
    fn stop(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        if let Some(mut child) = self.child.lock().unwrap().take() {
            drop(child.stdin.take());
            thread::spawn(move || {
                for _ in 0..30 {
                    if child.try_wait().ok().flatten().is_some() {
                        return;
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                let _ = child.kill();
                let _ = child.wait();
            });
        }
    }
}
fn start_service(app: &tauri::AppHandle) -> Result<Arc<Service>, Box<dyn std::error::Error>> {
    // Keep Electron's user directory so upgrading never creates an empty account.
    let base = if cfg!(target_os = "windows") {
        PathBuf::from(std::env::var("APPDATA")?).join("QQmanager")
    } else {
        app.path()
            .home_dir()?
            .join("Library/Application Support/QQmanager")
    };
    fs::create_dir_all(&base)?;
    let prefs_path = base.join("desktop.json");
    let mut prefs: Value = fs::read(&prefs_path)
        .ok()
        .and_then(|x| serde_json::from_slice(&x).ok())
        .unwrap_or(json!({}));
    let mut data = prefs["dataDirectory"]
        .as_str()
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or(base.join("data"));
    let mut child = None;
    match client().get(format!("{ORIGIN}/api/health")).send() {
        Ok(r) => {
            let h: Value = r.error_for_status()?.json()?;
            if !health(&h) {
                return Err("端口 8765 被其他程序占用".into());
            }
            let session = request("/api/session")?;
            let details: Value = client()
                .get(format!("{ORIGIN}/api/desktop"))
                .header(
                    "X-CSRF-Token",
                    session["csrf"].as_str().ok_or("服务会话无效")?,
                )
                .send()?
                .error_for_status()?
                .json()?;
            data = PathBuf::from(
                details["data_directory"]
                    .as_str()
                    .ok_or("无法读取数据目录")?,
            );
            if !data.is_absolute() {
                return Err("数据目录无效".into());
            }
        }
        Err(e) if e.is_connect() => {
            fs::create_dir_all(&data)?;
            let resources = if cfg!(debug_assertions) {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap()
                    .join("build")
            } else {
                app.path().resource_dir()?
            };
            let exe = resources.join("backend").join(if cfg!(windows) {
                "qqmanager-server.exe"
            } else {
                "qqmanager-server"
            });
            let log = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(data.join("desktop-server.log"))?;
            let mut cmd = Command::new(exe);
            cmd.args(["--port", "8765", "--data-dir"])
                .arg(&data)
                .env("QQMANAGER_DESKTOP", "1")
                .stdin(Stdio::piped())
                .stdout(log.try_clone()?)
                .stderr(log);
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                cmd.creation_flags(0x08000000);
            }
            let mut proc = cmd.spawn()?;
            let mut ready = false;
            for _ in 0..100 {
                if proc.try_wait()?.is_some() {
                    break;
                }
                if request("/api/health").is_ok_and(|v| health(&v)) {
                    ready = true;
                    break;
                }
                thread::sleep(Duration::from_millis(200));
            }
            if !ready {
                let _ = proc.kill();
                let _ = proc.wait();
                return Err("后台服务启动失败，请查看数据目录中的 desktop-server.log".into());
            }
            child = Some(proc);
        }
        Err(_) => return Err("无法确认已有服务，请退出旧版 QQmanager 后重试".into()),
    }
    prefs["dataDirectory"] = json!(data);
    let s = Arc::new(Service {
        child: Mutex::new(child),
        data,
        prefs_path,
        prefs: Mutex::new(prefs),
        stopping: AtomicBool::new(false),
    });
    s.save();
    Ok(s)
}
fn show(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}
fn error(app: &tauri::AppHandle, text: impl Into<String>) {
    app.dialog().message(text).title("QQmanager").show(|_| {});
}
fn native_menu(app: &tauri::AppHandle, s: &Arc<Service>) -> tauri::Result<Menu<tauri::Wry>> {
    let show = MenuItem::with_id(app, "show", "打开 QQmanager", true, None::<&str>)?;
    let auto = CheckMenuItem::with_id(
        app,
        "auto",
        "登录系统后后台启动",
        true,
        s.prefs.lock().unwrap()["autoStart"]
            .as_bool()
            .unwrap_or(false),
        None::<&str>,
    )?;
    let data = MenuItem::with_id(app, "data", "打开数据文件夹", true, None::<&str>)?;
    let plugin = MenuItem::with_id(app, "plugin", "安装 / 更新 QQ 消息插件", true, None::<&str>)?;
    let help = MenuItem::with_id(app, "help", "使用说明", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出 QQmanager", true, Some("CmdOrCtrl+Q"))?;
    Menu::with_items(
        app,
        &[
            &show,
            &auto,
            &data,
            &plugin,
            &help,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )
}
fn desktop_operation(app: &tauri::AppHandle, action: &str, enabled: Option<bool>) -> Result<bool, String> {
    let service = app.state::<Arc<Service>>();
    let auto_start = || service.prefs.lock().unwrap()["autoStart"].as_bool().unwrap_or(false);
    match action {
        "status" => {}
        "show" => show(app),
        #[cfg(windows)]
        "open-qq" => windows_setup::open_qq()?,
        "quit" => { service.stop(); app.exit(0); }
        "data" => app.opener().open_path(service.data.to_string_lossy(), None::<&str>).map_err(|e| e.to_string())?,
        "help" => app.opener().open_url("https://github.com/LzumiTulle/QQmanager#readme", None::<&str>).map_err(|e| e.to_string())?,
        "plugin" => {
            #[cfg(target_os = "macos")]
            app.opener().open_path(app.path().resource_dir().map_err(|e| e.to_string())?.join("QQmanager Installer.app").to_string_lossy(), None::<&str>).map_err(|e| e.to_string())?;
            #[cfg(not(target_os = "macos"))]
            windows_setup::choose(app.clone());
        }
        "auto" => {
            let current = auto_start();
            let desired = enabled.unwrap_or(!current);
            let result = if desired { app.autolaunch().enable() } else { app.autolaunch().disable() };
            if result.is_ok() {
                service.prefs.lock().unwrap()["autoStart"] = json!(desired);
                service.save();
            }
            if let Some(item) = app.state::<Menu<tauri::Wry>>().get("auto") {
                item.as_check_menuitem_unchecked().set_checked(auto_start()).map_err(|e| e.to_string())?;
            }
            result.map_err(|e| e.to_string())?;
        }
        _ => return Err("未知的桌面操作".into()),
    }
    Ok(auto_start())
}

#[cfg(windows)]
#[tauri::command]
fn desktop_action(window: tauri::WebviewWindow, action: String, enabled: Option<bool>) -> Result<bool, String> {
    if window.label() != "main" || !local(&window.url().map_err(|e| e.to_string())?) {
        return Err("此操作仅限 QQmanager 主窗口".into());
    }
    desktop_operation(window.app_handle(), &action, enabled)
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let autostart = tauri_plugin_autostart::Builder::new().args(["--background"]);
    #[cfg(target_os = "macos")]
    let autostart = autostart.macos_launcher(tauri_plugin_autostart::MacosLauncher::LaunchAgent);
    let builder=tauri::Builder::default();
    #[cfg(windows)]
    let builder=builder.invoke_handler(tauri::generate_handler![desktop_action]);
    let app=builder
 .plugin(tauri_plugin_single_instance::init(|app,_,_|show(app)))
 .plugin(autostart.build())
 .plugin(tauri_plugin_opener::init()).plugin(tauri_plugin_dialog::init()).plugin(tauri_plugin_notification::init())
 .setup(|app|{
  let handle=app.handle().clone();let service=match start_service(&handle){Ok(s)=>s,Err(e)=>{app.dialog().message(e.to_string()).title("QQmanager 启动失败").blocking_show();return Err(e);}};
  app.manage(service.clone());
  let auto=service.prefs.lock().unwrap()["autoStart"].as_bool().unwrap_or(true);
  let result=if auto{app.autolaunch().enable()}else{app.autolaunch().disable()};
  let auto_ok=result.is_ok();service.prefs.lock().unwrap()["autoStart"]=json!(auto&&auto_ok);service.save();
  if let Err(e)=result {error(&handle,format!("开机启动设置失败，可稍后从菜单重试：{e}"));}
  let menu=native_menu(&handle,&service)?;
  app.manage(menu.clone());
  #[cfg(not(windows))]
  {
  let app_menu=Menu::with_items(app,&[&Submenu::with_items(app,"QQmanager",true,&menu.items()?.iter().map(|x|x as &dyn tauri::menu::IsMenuItem<tauri::Wry>).collect::<Vec<_>>())?,&Submenu::with_items(app,"编辑",true,&[&PredefinedMenuItem::undo(app,None)?,&PredefinedMenuItem::redo(app,None)?,&PredefinedMenuItem::cut(app,None)?,&PredefinedMenuItem::copy(app,None)?,&PredefinedMenuItem::paste(app,None)?,&PredefinedMenuItem::select_all(app,None)?])?])?;
  app.set_menu(app_menu)?;
  }
  TrayIconBuilder::new().icon(tauri::image::Image::from_bytes(include_bytes!("../../desktop/tray.png"))?).icon_as_template(cfg!(target_os="macos")).tooltip("QQmanager · 后台运行").menu(&menu).show_menu_on_left_click(false).on_tray_icon_event(|tray,event|{if matches!(event,TrayIconEvent::Click{button:MouseButton::Left,button_state:MouseButtonState::Up,..}){show(tray.app_handle());}}).build(app)?;
  let nav=handle.clone();let popup=handle.clone();let download=handle.clone();let runtime=service.clone();
  let builder=WebviewWindowBuilder::new(app,"main",WebviewUrl::External(ORIGIN.parse()?)).title("QQmanager").inner_size(1280.,850.).min_inner_size(800.,600.).visible(false);
  #[cfg(windows)]
  let builder=builder.theme(Some(tauri::Theme::Dark));
  let window=builder
    .on_page_load(move|_,payload|{if matches!(payload.event(),tauri::webview::PageLoadEvent::Finished)&&local(payload.url())&&payload.url().path()=="/"{let _=fs::write(runtime.data.join("desktop-runtime.json"),serde_json::to_vec(&json!({"version":"0.4.0","webview_loaded":true,"renderer":if cfg!(windows){"WebView2"}else{"WebKit"}})).unwrap());}})
    .on_navigation(move|u|{if local(u){true}else{if external(u){let _=nav.opener().open_url(u.to_string(),None::<&str>);}false}})
    .on_new_window(move|u,_|{if local(&u)||external(&u){let _=popup.opener().open_url(u.to_string(),None::<&str>);}tauri::webview::NewWindowResponse::Deny})
    .on_download(move|view,event|{match event{
      tauri::webview::DownloadEvent::Requested{url,destination}=>{if !local(&url){return false;}if let Ok(dir)=download.path().download_dir(){let name=destination.file_name().unwrap_or_default().to_owned();let stamp=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis();*destination=dir.join(format!("QQmanager-{stamp}-{}",name.to_string_lossy()));true}else{false}},
      tauri::webview::DownloadEvent::Finished{success,path,..}=>{let message=if success{format!("文件已保存到下载文件夹：{}",path.as_ref().and_then(|p|p.file_name()).unwrap_or_default().to_string_lossy())}else{"下载失败，请重试。".to_string()};let script=format!("if(typeof toast === 'function') toast({});",serde_json::to_string(&message).unwrap());let view=view.clone();thread::spawn(move||{let _=view.eval(&script);});true},_=>false}})
    .build()?;
  #[cfg(windows)]
  {
    windows_theme::set_popup_theme(window.theme()?);
    window.on_window_event(|event| {
      if let tauri::WindowEvent::ThemeChanged(theme) = event {
        windows_theme::set_popup_theme(*theme);
      }
    });
  }
  let close=window.clone();window.on_window_event(move|e|{if let tauri::WindowEvent::CloseRequested{api,..}=e{api.prevent_close();let _=close.hide();}});
  if !std::env::args().any(|a|a=="--background"){window.show()?;}
  let h=handle.clone();thread::spawn(move||{
   let mut seen:Vec<Value>=service.prefs.lock().unwrap()["notified"].as_array().cloned().unwrap_or_default();let mut initialized=service.prefs.lock().unwrap()["notified"].is_array();
   while !service.stopping.load(Ordering::SeqCst){
    if let Ok(v)=request("/api/state"){if let Some(rows)=v["notifications"].as_array(){for row in rows{if !seen.contains(&row["id"]){if initialized && row["seen"]!=1 {let _=h.notification().builder().title("QQmanager · 待办提醒").body(row["title"].as_str().unwrap_or("待办提醒")).show();}seen.push(row["id"].clone());}}initialized=true;if seen.len()>1000{seen.drain(..seen.len()-1000);}service.prefs.lock().unwrap()["notified"]=json!(seen);service.save();}}
    if service.child.lock().unwrap().as_mut().is_some_and(|c|c.try_wait().ok().flatten().is_some()){error(&h,"后台服务已停止。请退出并重新打开 QQmanager，原有数据仍保留。");break;}
    thread::sleep(Duration::from_secs(10));
   }
  });Ok(())
 })
 .on_menu_event(|app,event|{
    let action = event.id().as_ref();
    if matches!(action, "show" | "quit" | "data" | "help" | "plugin" | "auto") {
      if let Err(e) = desktop_operation(app, action, None) { error(app, e); }
    }
 })
 .build(tauri::generate_context!())?;
    app.run(|app, event| match event {
        tauri::RunEvent::Exit => {
            if let Some(s) = app.try_state::<Arc<Service>>() {
                s.stop();
            }
        }
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen { .. } => show(app),
        _ => {}
    });
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("QQmanager: {e}");
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn destinations() {
        assert!(local(&format!("{ORIGIN}/#home").parse().unwrap()));
        for s in [
            "file:///etc/passwd",
            "javascript:1",
            "http://127.0.0.1:8765.evil.com/",
            "http://user@127.0.0.1:8765/",
        ] {
            assert!(!s.parse().is_ok_and(|u| local(&u)));
        }
        assert!(!external(&"https://user:pass@example.com".parse().unwrap()));
        assert!(!external(&"file:///tmp/run.exe".parse().unwrap()));
    }
    #[test]
    fn service_identity() {
        assert!(health(&json!({"app":"QQmanager","protocol":1})));
        assert!(!health(&json!({"app":"other","protocol":1})));
    }
}
