#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use std::{net::SocketAddr, net::TcpStream};

const BACKEND_URL: &str = "http://127.0.0.1:8097";
const BACKEND_PORT: u16 = 8097;
const BUILD_MANIFEST_DIR: &str = env!("CARGO_MANIFEST_DIR");

#[derive(Default)]
struct BackendState {
    child: Mutex<Option<Child>>,
}

#[tauri::command]
fn start_backend(state: tauri::State<'_, BackendState>) -> Result<String, String> {
    let mut guard = state.child.lock().map_err(|_| "Failed to lock backend state.")?;
    if let Some(child) = guard.as_mut() {
        if child.try_wait().map_err(|error| format!("Failed to inspect backend state: {error}"))?.is_some() {
            *guard = None;
        }
    }

    if backend_reachable() {
        if backend_compatible() {
            return Ok(BACKEND_URL.to_string());
        }
        stop_managed_child(&mut guard);
        terminate_backend_on_port(BACKEND_PORT)?;
    }

    if guard.is_some() {
        return Ok(BACKEND_URL.to_string());
    }

    let root = find_boss_root().ok_or_else(|| "Unable to locate the BOSS repository root.".to_string())?;
    let mut child = spawn_backend(&root)?;
    wait_for_backend(&mut child)?;
    *guard = Some(child);
    Ok(BACKEND_URL.to_string())
}

#[tauri::command]
fn stop_backend(state: tauri::State<'_, BackendState>) -> Result<(), String> {
    let mut guard = state.child.lock().map_err(|_| "Failed to lock backend state.")?;
    stop_managed_child(&mut guard);
    Ok(())
}

fn backend_reachable() -> bool {
    let socket = SocketAddr::from(([127, 0, 0, 1], BACKEND_PORT));
    TcpStream::connect_timeout(&socket, Duration::from_millis(250)).is_ok()
}

fn backend_compatible() -> bool {
    match backend_get("/permissions") {
        Ok(body) => body.contains("\"full_access_mode\""),
        Err(_) => false,
    }
}

fn wait_for_backend(child: &mut Child) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(45);
    while Instant::now() < deadline {
        if backend_reachable() && backend_compatible() {
            return Ok(());
        }

        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("Failed to inspect backend startup: {error}"))?
        {
            return Err(format!("BOSS backend exited during startup with status {status}."));
        }

        thread::sleep(Duration::from_millis(250));
    }

    Err("Timed out waiting for the BOSS backend.".to_string())
}

fn spawn_backend(root: &Path) -> Result<Child, String> {
    let boss_bin = root.join(".venv").join("bin").join("boss");
    let mut command = if boss_bin.exists() {
        let mut cmd = Command::new(boss_bin);
        cmd.arg("web");
        cmd
    } else {
        let mut cmd = Command::new("python3");
        cmd.arg(root.join("main.py"));
        cmd.arg("web");
        cmd
    };

    command
        .args(["--host", "127.0.0.1", "--port", "8097", "--no-browser"])
        .current_dir(root)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("Failed to launch BOSS backend: {error}"))
}

fn backend_get(path: &str) -> Result<String, String> {
    let mut stream = TcpStream::connect(("127.0.0.1", BACKEND_PORT))
        .map_err(|error| format!("Failed to connect to backend: {error}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(|error| format!("Failed to configure backend socket: {error}"))?;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{BACKEND_PORT}\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("Failed to query backend: {error}"))?;

    let mut buffer = String::new();
    stream
        .read_to_string(&mut buffer)
        .map_err(|error| format!("Failed to read backend response: {error}"))?;
    let body = buffer
        .split("\r\n\r\n")
        .nth(1)
        .ok_or_else(|| "Backend returned an invalid HTTP response.".to_string())?;
    Ok(body.to_string())
}

fn stop_managed_child(guard: &mut Option<Child>) {
    if let Some(child) = guard.as_mut() {
        let _ = child.kill();
        let _ = child.wait();
    }
    *guard = None;
}

fn terminate_backend_on_port(port: u16) -> Result<(), String> {
    let output = Command::new("lsof")
        .args(["-ti", &format!("tcp:{port}")])
        .output()
        .map_err(|error| format!("Failed to inspect existing backend on port {port}: {error}"))?;
    if !output.status.success() && output.stdout.is_empty() {
        return Ok(());
    }

    let pids: Vec<String> = String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(ToOwned::to_owned)
        .collect();
    if pids.is_empty() {
        return Ok(());
    }

    for pid in &pids {
        let _ = Command::new("kill").args(["-TERM", pid]).status();
    }

    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if !backend_reachable() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(150));
    }

    for pid in &pids {
        let _ = Command::new("kill").args(["-KILL", pid]).status();
    }
    thread::sleep(Duration::from_millis(250));
    Ok(())
}

fn find_boss_root() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(current_dir) = std::env::current_dir() {
        candidates.push(current_dir);
    }
    candidates.push(PathBuf::from(BUILD_MANIFEST_DIR));
    if let Ok(explicit_root) = std::env::var("BOSS_ROOT") {
        candidates.push(PathBuf::from(explicit_root));
    }
    if let Ok(executable) = std::env::current_exe() {
        candidates.push(executable);
    }

    for candidate in candidates {
        for ancestor in candidate.ancestors() {
            if ancestor.join("main.py").exists() && ancestor.join("boss").is_dir() {
                return Some(ancestor.to_path_buf());
            }
        }
    }
    None
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .invoke_handler(tauri::generate_handler![start_backend, stop_backend])
        .run(tauri::generate_context!())
        .expect("error while running BOSS UI");
}
