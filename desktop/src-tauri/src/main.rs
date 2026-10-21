// 发布版不弹黑色控制台窗口
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    projector_desktop_lib::run()
}
