#![cfg_attr(windows, windows_subsystem = "windows")]

mod bridge;
mod pages;
mod printing;
mod screen;

use eframe::egui;
use screen::GitDoc;
use std::{
    env,
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
};
use url::Url;

#[derive(Clone, Default)]
pub struct Destination {
    pub doc: Option<i64>,
    pub branch: Option<String>,
    pub ids: Vec<i64>,
    pub page: usize,
    pub change: Option<usize>,
    pub sheet: Option<String>,
    pub cell: Option<String>,
    pub pages: bool,
}

fn destination(link: &str) -> Result<Destination, String> {
    let url = Url::parse(link).map_err(|_| "GitDoc 링크가 올바르지 않습니다.")?;
    if url.scheme() != "gitdoc" || url.host_str() != Some("open") {
        return Err("GitDoc 링크가 올바르지 않습니다.".into());
    }
    let parts: Vec<_> = url.path().split('/').filter(|s| !s.is_empty()).collect();
    if parts.is_empty() {
        return Ok(Destination::default());
    }
    if parts.first() != Some(&"documents")
        || !matches!(parts.len(), 2 | 3)
        || parts.get(2).is_some_and(|v| *v != "compare")
    {
        return Err("문서 링크를 확인하세요.".into());
    }
    let doc = parts[1]
        .parse()
        .ok()
        .filter(|value| *value > 0)
        .ok_or("문서 ID를 확인하세요.")?;
    let mut result = Destination {
        doc: Some(doc),
        page: 1,
        ..Default::default()
    };
    let mut ids = [None; 3];
    for (key, value) in url.query_pairs() {
        match key.as_ref() {
            "branch" => result.branch = Some(value.into_owned()),
            "page" => result.page = value.parse::<usize>().ok().unwrap_or(1).max(1),
            "change" => {
                result.change = value
                    .parse::<usize>()
                    .ok()
                    .filter(|n| *n > 0)
                    .map(|n| n - 1)
            }
            "sheet" => result.sheet = Some(value.into_owned()),
            "cell" => result.cell = Some(value.into_owned()),
            "view" => result.pages = value == "pages",
            "version" => ids[0] = value.parse::<i64>().ok().filter(|n| *n > 0),
            "base" => ids[1] = value.parse::<i64>().ok().filter(|n| *n > 0),
            "target" => ids[2] = value.parse::<i64>().ok().filter(|n| *n > 0),
            _ => {}
        }
    }
    if parts.len() == 3 {
        let target = ids[2].ok_or("대상 버전 ID를 확인하세요.")?;
        result.ids = vec![ids[1].unwrap_or(0), target];
    } else if let Some(id) = ids[0] {
        result.ids = vec![id];
    }
    Ok(result)
}

fn root() -> PathBuf {
    if let Some(path) = env::var_os("DOCVERS_ROOT") {
        return path.into();
    }
    if let Ok(binary) = env::current_exe() {
        for candidate in binary.ancestors() {
            if candidate.join("desktop/bridge.py").is_file() {
                return candidate.to_owned();
            }
        }
    }
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn python(root: &Path) -> OsString {
    if let Some(path) = env::var_os("DOCVERS_PYTHON") {
        return path;
    }
    let bundled = root.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    if bundled.is_file() {
        return bundled.into_os_string();
    }
    OsString::from(if cfg!(windows) { "python" } else { "python3" })
}

fn configure(ctx: &egui::Context) {
    let fonts = if cfg!(target_os = "macos") {
        vec![PathBuf::from("/System/Library/Fonts/AppleSDGothicNeo.ttc")]
    } else if cfg!(windows) {
        vec![
            PathBuf::from(env::var_os("WINDIR").unwrap_or_else(|| "C:\\Windows".into()))
                .join("Fonts/malgun.ttf"),
        ]
    } else {
        vec![PathBuf::from(
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        )]
    };
    let selected = env::var_os("DOCVERS_UI_FONT")
        .map(PathBuf::from)
        .or_else(|| fonts.into_iter().find(|p| p.is_file()));
    if let Some(bytes) = selected.and_then(|p| fs::read(p).ok()) {
        let mut fonts = egui::FontDefinitions::default();
        fonts.font_data.insert(
            "gitdoc-cjk".into(),
            egui::FontData::from_owned(bytes).into(),
        );
        for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
            fonts
                .families
                .entry(family)
                .or_default()
                .push("gitdoc-cjk".into());
        }
        ctx.set_fonts(fonts);
    }
    ctx.set_visuals(egui::Visuals::light());
    ctx.style_mut(|style| {
        style.spacing.item_spacing = egui::vec2(9.0, 8.0);
        style.spacing.button_padding = egui::vec2(10.0, 5.0);
        style.visuals.selection.bg_fill = egui::Color32::from_rgb(217, 232, 248);
        style.visuals.selection.stroke =
            egui::Stroke::new(1.0_f32, egui::Color32::from_rgb(30, 95, 153));
    });
}

fn main() -> eframe::Result {
    let root = root();
    let python = python(&root);
    let setup = env::args()
        .nth(1)
        .map_or_else(|| Ok(Destination::default()), |arg| destination(&arg));
    eframe::run_native(
        "GitDoc",
        eframe::NativeOptions {
            viewport: egui::ViewportBuilder::default()
                .with_inner_size([1360.0, 920.0])
                .with_min_inner_size([800.0, 570.0]),
            ..Default::default()
        },
        Box::new(move |creation| {
            configure(&creation.egui_ctx);
            let bridge = bridge::Bridge::start(root, python, creation.egui_ctx.clone());
            Ok(Box::new(GitDoc::new(bridge, setup)))
        }),
    )
}
