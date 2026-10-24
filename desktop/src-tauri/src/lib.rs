//! Claude 工作台桌面壳子：窗口里直接加载中心服务的网页端，外加托盘和“服务端模式”。
//!
//! 服务端模式（只在台式机上打开）：开机自启，后台无窗口运行
//! `python server.py` 和 `python mcp_pipe.py claude_mcp.py`，进程退出后自动重启。
//! 环境变量从项目文件夹里的 config.bat 的 set 行读取。

use std::{
    collections::HashMap,
    fs::{self, File, OpenOptions},
    net::{TcpStream, ToSocketAddrs},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU32, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use tauri::{
    ipc::CapabilityBuilder,
    menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, RunEvent, Url, WindowEvent, Wry,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

const AUTOSTART_ARG: &str = "--autostart";

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
struct Config {
    server_url: String,
    server_mode: bool,
    project_dir: String,
}

/// 服务端模式下守护的一个 Python 进程
struct Proc {
    label: &'static str,
    log_name: &'static str,
    args: &'static [&'static str],
    /// 启用后延迟多久再启动（小智连接等中心服务先起来）
    delay: Duration,
    child: Option<Child>,
    started_at: Instant,
    next_start: Instant,
    failures: u32,
    status: String,
}

impl Proc {
    fn new(label: &'static str, log_name: &'static str, args: &'static [&'static str], delay: Duration) -> Self {
        let now = Instant::now();
        Proc { label, log_name, args, delay, child: None, started_at: now, next_start: now + delay, failures: 0, status: String::new() }
    }
}

struct TrayItems {
    mode: CheckMenuItem<Wry>,
    status: Vec<MenuItem<Wry>>,
}

struct AppState {
    cfg: Mutex<Config>,
    /// 本地设置页的地址（启动时记下，之后从托盘回到设置页用）
    setup_url: Mutex<Option<Url>>,
    procs: Mutex<Vec<Proc>>,
    tray: Mutex<Option<TrayItems>>,
    quitting: AtomicBool,
    cap_seq: AtomicU32,
}

// ---------- 配置 ----------

fn config_path(app: &AppHandle) -> PathBuf {
    app.path().app_config_dir().expect("找不到配置目录").join("config.json")
}

fn log_dir(app: &AppHandle) -> PathBuf {
    let dir = app.path().app_log_dir().expect("找不到日志目录");
    let _ = fs::create_dir_all(&dir);
    dir
}

fn load_config(app: &AppHandle) -> Config {
    fs::read_to_string(config_path(app)).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

fn store_config(app: &AppHandle, cfg: &Config) -> Result<(), String> {
    let path = config_path(app);
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    fs::write(&path, serde_json::to_string_pretty(cfg).unwrap()).map_err(|e| format!("保存配置失败：{e}"))
}

/// 读取 config.bat 里的 `set "K=V"` / `set K=V` 行，支持 %VAR% 展开
fn read_config_bat(path: &Path) -> HashMap<String, String> {
    let mut vars = HashMap::new();
    let Ok(bytes) = fs::read(path) else { return vars };
    let text = String::from_utf8_lossy(&bytes);
    for line in text.lines() {
        let line = line.trim().trim_start_matches('\u{feff}').trim_start_matches('@');
        if line.len() < 4 || !line[..4].eq_ignore_ascii_case("set ") {
            continue;
        }
        let mut rest = line[4..].trim();
        if rest.starts_with('/') {
            continue; // set /a、set /p 不处理
        }
        if let Some(inner) = rest.strip_prefix('"') {
            rest = match inner.rfind('"') {
                Some(end) => &inner[..end],
                None => inner,
            };
        }
        let Some((key, value)) = rest.split_once('=') else { continue };
        let key = key.trim();
        if key.is_empty() {
            continue;
        }
        let value = expand_vars(value, &vars);
        vars.insert(key.to_string(), value);
    }
    vars
}

fn expand_vars(value: &str, vars: &HashMap<String, String>) -> String {
    let mut out = String::new();
    let mut rest = value;
    while let Some(start) = rest.find('%') {
        out.push_str(&rest[..start]);
        let after = &rest[start + 1..];
        match after.find('%') {
            Some(end) => {
                let name = &after[..end];
                let found = vars
                    .iter()
                    .find(|(k, _)| k.eq_ignore_ascii_case(name))
                    .map(|(_, v)| v.clone())
                    .or_else(|| std::env::var(name).ok());
                match found {
                    Some(v) => out.push_str(&v),
                    None => {
                        out.push('%');
                        out.push_str(name);
                        out.push('%');
                    }
                }
                rest = &after[end + 1..];
            }
            None => {
                out.push_str(&rest[start..]);
                rest = "";
            }
        }
    }
    out.push_str(rest);
    out
}

fn check_project_dir(dir: &str) -> Result<(), String> {
    if dir.is_empty() {
        return Err("请填写项目文件夹（server.py 所在的文件夹）".into());
    }
    let dir = Path::new(dir);
    if !dir.join("server.py").is_file() {
        return Err("这个文件夹里没有 server.py".into());
    }
    if !dir.join("config.bat").is_file() {
        return Err("没找到 config.bat，请先在这个文件夹里运行 install.bat".into());
    }
    Ok(())
}

// ---------- 进程守护 ----------

fn find_python(dir: &Path, env: &HashMap<String, String>) -> PathBuf {
    if let Some(p) = env.get("PYTHON") {
        return PathBuf::from(p);
    }
    for venv in [".venv", "venv"] {
        let p = if cfg!(windows) { dir.join(venv).join("Scripts").join("python.exe") } else { dir.join(venv).join("bin").join("python") };
        if p.is_file() {
            return p;
        }
    }
    PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
}

fn open_log(app: &AppHandle, name: &str) -> std::io::Result<File> {
    let path = log_dir(app).join(name);
    // 日志超过 5 MB 就从头写
    let too_big = fs::metadata(&path).map(|m| m.len() > 5 * 1024 * 1024).unwrap_or(false);
    OpenOptions::new().create(true).append(!too_big).write(true).truncate(too_big).open(path)
}

fn spawn_proc(app: &AppHandle, cfg: &Config, p: &Proc) -> std::io::Result<Child> {
    let dir = PathBuf::from(&cfg.project_dir);
    let env = read_config_bat(&dir.join("config.bat"));
    let python = find_python(&dir, &env);
    let log = open_log(app, p.log_name)?;
    let mut cmd = Command::new(python);
    cmd.arg("-u")
        .args(p.args)
        .current_dir(&dir)
        .envs(&env)
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log);
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.spawn()
}

/// 结束进程及其子进程（mcp_pipe.py 会再起一个 claude_mcp.py）
fn kill_tree(mut child: Child) {
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn stop_all(app: &AppHandle) {
    let state = app.state::<AppState>();
    state.quitting.store(true, Ordering::SeqCst);
    for p in state.procs.lock().unwrap().iter_mut() {
        if let Some(c) = p.child.take() {
            kill_tree(c);
        }
    }
}

fn supervise(app: AppHandle) {
    loop {
        let state = app.state::<AppState>();
        if state.quitting.load(Ordering::SeqCst) {
            return;
        }
        let cfg = state.cfg.lock().unwrap().clone();
        let mut changed = Vec::new();
        {
            let mut procs = state.procs.lock().unwrap();
            let now = Instant::now();
            for (i, p) in procs.iter_mut().enumerate() {
                let status = if !cfg.server_mode {
                    if let Some(c) = p.child.take() {
                        kill_tree(c);
                    }
                    p.failures = 0;
                    p.next_start = now + p.delay;
                    format!("{}：未启用", p.label)
                } else {
                    if let Some(c) = p.child.as_mut() {
                        if let Ok(Some(code)) = c.try_wait() {
                            p.child = None;
                            // 运行超过一分钟才退出的，不算连续崩溃
                            if p.started_at.elapsed() > Duration::from_secs(60) {
                                p.failures = 0;
                            }
                            p.failures += 1;
                            let wait = Duration::from_secs((1u64 << p.failures.min(6)).min(60));
                            p.next_start = now + wait;
                            eprintln!("[{}] 进程退出（{code}），{} 秒后重启", p.label, wait.as_secs());
                        }
                    }
                    if p.child.is_none() && now >= p.next_start {
                        match spawn_proc(&app, &cfg, p) {
                            Ok(c) => {
                                p.child = Some(c);
                                p.started_at = now;
                            }
                            Err(e) => {
                                eprintln!("[{}] 启动失败：{e}", p.label);
                                p.failures += 1;
                                p.next_start = now + Duration::from_secs(10);
                            }
                        }
                    }
                    if p.child.is_some() {
                        format!("{}：● 运行中", p.label)
                    } else if p.failures > 0 {
                        let secs = p.next_start.saturating_duration_since(now).as_secs();
                        format!("{}：已停止，{secs} 秒后重启", p.label)
                    } else {
                        format!("{}：启动中…", p.label)
                    }
                };
                if status != p.status {
                    p.status = status.clone();
                    changed.push((i, status));
                }
            }
        }
        if let Some(tray) = state.tray.lock().unwrap().as_ref() {
            for (i, text) in changed {
                let _ = tray.status[i].set_text(text);
            }
        }
        thread::sleep(Duration::from_secs(1));
    }
}

// ---------- 窗口 ----------

/// 允许服务器网页调用通知插件（网页的地址是用户填的，只能在运行时加）
fn allow_remote(app: &AppHandle, url: &Url) {
    let state = app.state::<AppState>();
    let n = state.cap_seq.fetch_add(1, Ordering::SeqCst);
    let pattern = format!("{}/*", url.origin().ascii_serialization());
    let cap = CapabilityBuilder::new(format!("server-page-{n}"))
        .remote(pattern)
        .window("main")
        .permission("notification:default");
    if let Err(e) = app.add_capability(cap) {
        eprintln!("添加网页权限失败：{e}");
    }
}

fn wait_for_port(url: &Url, timeout: Duration) {
    let Some(host) = url.host_str() else { return };
    let port = url.port_or_known_default().unwrap_or(80);
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Ok(addrs) = (host, port).to_socket_addrs() {
            if addrs.into_iter().any(|a| TcpStream::connect_timeout(&a, Duration::from_millis(500)).is_ok()) {
                return;
            }
        }
        thread::sleep(Duration::from_millis(500));
    }
}

/// 窗口加载服务器网页；服务端模式下先等中心服务起来
fn open_server(app: &AppHandle) {
    let cfg = app.state::<AppState>().cfg.lock().unwrap().clone();
    let Ok(url) = Url::parse(&cfg.server_url) else { return show_setup(app) };
    allow_remote(app, &url);
    let app = app.clone();
    thread::spawn(move || {
        if cfg.server_mode {
            wait_for_port(&url, Duration::from_secs(30));
        }
        if let Some(w) = app.get_webview_window("main") {
            let _ = w.navigate(url);
        }
    });
}

fn show_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn toggle_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        if w.is_visible().unwrap_or(false) && !w.is_minimized().unwrap_or(false) {
            let _ = w.hide();
        } else {
            show_window(app);
        }
    }
}

fn show_setup(app: &AppHandle) {
    let url = app.state::<AppState>().setup_url.lock().unwrap().clone();
    if let (Some(w), Some(url)) = (app.get_webview_window("main"), url) {
        let _ = w.navigate(url);
    }
    show_window(app);
}

/// 打开或关闭服务端模式：开机自启 + 托盘勾选状态；进程由 supervise 线程跟着配置启停
fn apply_server_mode(app: &AppHandle, on: bool) {
    let autolaunch = app.autolaunch();
    let result = if on { autolaunch.enable() } else { autolaunch.disable() };
    if let Err(e) = result {
        eprintln!("设置开机自启失败：{e}");
    }
    if let Some(tray) = app.state::<AppState>().tray.lock().unwrap().as_ref() {
        let _ = tray.mode.set_checked(on);
    }
}

// ---------- 设置页调用的命令 ----------

#[tauri::command]
fn get_config(state: tauri::State<AppState>) -> Config {
    state.cfg.lock().unwrap().clone()
}

#[tauri::command]
fn save_config(app: AppHandle, state: tauri::State<AppState>, server_url: String, server_mode: bool, project_dir: String) -> Result<(), String> {
    let server_url = server_url.trim().trim_end_matches('/').to_string();
    let url = Url::parse(&server_url).map_err(|_| "服务器地址格式不对，应该像 http://192.168.1.10:8080".to_string())?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err("服务器地址要以 http:// 开头".into());
    }
    if server_mode {
        check_project_dir(&project_dir)?;
    }
    let cfg = Config { server_url, server_mode, project_dir };
    store_config(&app, &cfg)?;
    *state.cfg.lock().unwrap() = cfg;
    apply_server_mode(&app, server_mode);
    open_server(&app);
    Ok(())
}

// ---------- 托盘 ----------

fn build_tray(app: &AppHandle, cfg: &Config) -> tauri::Result<()> {
    let toggle = MenuItem::with_id(app, "toggle", "显示/隐藏窗口", true, None::<&str>)?;
    let reload = MenuItem::with_id(app, "reload", "重新加载", true, None::<&str>)?;
    let setup = MenuItem::with_id(app, "setup", "修改服务器地址…", true, None::<&str>)?;
    let mode = CheckMenuItem::with_id(app, "mode", "服务端模式（开机自启中心服务）", true, cfg.server_mode, None::<&str>)?;
    let status: Vec<MenuItem<Wry>> = app.state::<AppState>().procs.lock().unwrap()
        .iter()
        .map(|p| MenuItem::new(app, format!("{}：未启用", p.label), false, None::<&str>))
        .collect::<tauri::Result<_>>()?;
    let logs = MenuItem::with_id(app, "logs", "打开日志文件夹", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&toggle, &reload, &setup, &PredefinedMenuItem::separator(app)?, &mode])?;
    for item in &status {
        menu.append(item)?;
    }
    menu.append_items(&[&logs, &PredefinedMenuItem::separator(app)?, &quit])?;

    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("Claude 工作台")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "toggle" => toggle_window(app),
            "reload" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.eval("location.reload()");
                }
                show_window(app);
            }
            "setup" => show_setup(app),
            "mode" => {
                let state = app.state::<AppState>();
                let mut cfg = state.cfg.lock().unwrap().clone();
                if !cfg.server_mode && check_project_dir(&cfg.project_dir).is_err() {
                    // 还没填项目文件夹，去设置页填
                    apply_server_mode(app, false);
                    show_setup(app);
                    return;
                }
                cfg.server_mode = !cfg.server_mode;
                let _ = store_config(app, &cfg);
                let on = cfg.server_mode;
                *state.cfg.lock().unwrap() = cfg;
                apply_server_mode(app, on);
            }
            "logs" => {
                let dir = log_dir(app);
                let opener = if cfg!(windows) { "explorer" } else { "open" };
                let _ = Command::new(opener).arg(dir).spawn();
            }
            "quit" => {
                stop_all(app);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                toggle_window(tray.app_handle());
            }
        })
        .build(app)?;

    *app.state::<AppState>().tray.lock().unwrap() = Some(TrayItems { mode, status });
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_window(app)))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![AUTOSTART_ARG])))
        .manage(AppState {
            cfg: Mutex::new(Config::default()),
            setup_url: Mutex::new(None),
            procs: Mutex::new(vec![
                Proc::new("中心服务", "server.log", &["server.py"], Duration::ZERO),
                Proc::new("小智连接", "mcp_pipe.log", &["mcp_pipe.py", "claude_mcp.py"], Duration::from_secs(3)),
            ]),
            tray: Mutex::new(None),
            quitting: AtomicBool::new(false),
            cap_seq: AtomicU32::new(0),
        })
        .invoke_handler(tauri::generate_handler![get_config, save_config])
        .setup(|app| {
            let handle = app.handle().clone();
            let cfg = load_config(&handle);
            *app.state::<AppState>().cfg.lock().unwrap() = cfg.clone();
            if let Some(w) = app.get_webview_window("main") {
                *app.state::<AppState>().setup_url.lock().unwrap() = w.url().ok();
            }
            build_tray(&handle, &cfg)?;
            thread::spawn({
                let handle = handle.clone();
                move || supervise(handle)
            });

            let autostarted = std::env::args().any(|a| a == AUTOSTART_ARG);
            if !cfg.server_url.is_empty() {
                open_server(&handle);
            }
            // 开机自启时只在托盘里待着
            if !autostarted || cfg.server_url.is_empty() {
                show_window(&handle);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // 关闭窗口 = 最小化到托盘
            if let WindowEvent::CloseRequested { api, .. } = event {
                if !window.app_handle().state::<AppState>().quitting.load(Ordering::SeqCst) {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("启动失败")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_all(app);
            }
        });
}
