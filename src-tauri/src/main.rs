// UNTESTED: not compiled in the Linux authoring environment. Built by CI on windows-latest.
// The shell owns the per-launch API token: it generates it, hands it to the Python sidecar via env,
// and exposes it to the UI over IPC only. The backend binds to 127.0.0.1.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use rand::{distributions::Alphanumeric, Rng};
use serde::Serialize;
use tauri::Manager;
use tauri_plugin_shell::ShellExt;

#[derive(Serialize, Clone)]
struct BackendConfig { url: String, token: String }

#[tauri::command]
fn backend_config(state: tauri::State<BackendConfig>) -> BackendConfig { state.inner().clone() }

fn main() {
    let token: String = rand::thread_rng().sample_iter(&Alphanumeric).take(48).map(char::from).collect();
    let cfg = BackendConfig { url: "http://127.0.0.1:8765".into(), token: token.clone() };
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(cfg)
        .invoke_handler(tauri::generate_handler![backend_config])
        .setup(move |app| {
            let cmd = app.shell().sidecar("cit-backend")?.env("CIT_API_TOKEN", token.clone()).env("CIT_PORT", "8765");
            let (_rx, child) = cmd.spawn()?;
            app.manage(std::sync::Mutex::new(Some(child))); // killed when the app exits
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Claude IT Technician");
}
