fn main() {
    // 声明本软件自己的命令，生成 allow-xxx 权限：设置页用 get_config/save_config，服务器网页只能用文件相关的几个
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["get_config", "save_config", "discover_servers", "open_remote_file", "upload_local_folder", "local_link_path", "sync_back"]),
    ))
    .expect("tauri build 失败");
}
