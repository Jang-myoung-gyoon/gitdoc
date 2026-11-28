#![cfg_attr(windows, windows_subsystem = "windows")]

mod bridge;
mod editor_fonts;
mod office_editor;
mod pages;
mod paper;
mod paper_text;
mod presentation;
mod printing;
mod screen;
mod updates;

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
    pub row_key: Option<String>,
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
            "row_key" => result.row_key = Some(value.into_owned()),
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
        // Release packages keep the engine beside the executable.
        let folder = binary.parent();
        let packaged = if cfg!(target_os = "macos") {
            folder
                .and_then(Path::parent)
                .map(|contents| contents.join("Resources").join("app"))
        } else {
            folder.map(|folder| folder.join("app"))
        };
        if let Some(packaged) = packaged
            && packaged.join("desktop").join("bridge.py").is_file()
        {
            return packaged;
        }
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
    let local = root.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    if local.is_file() {
        return local.into_os_string();
    }
    // Release packages ship their own runtime next to the engine folder.
    if root.join("PACKAGED").is_file()
        && let Some(runtime) = root.parent().map(|folder| folder.join("python"))
    {
        let bundled = if cfg!(windows) {
            runtime.join("python.exe")
        } else {
            runtime.join("bin").join("python3")
        };
        if bundled.is_file() {
            return bundled.into_os_string();
        }
    }
    OsString::from(if cfg!(windows) { "python" } else { "python3" })
}

fn font_directories() -> Vec<PathBuf> {
    let home = env::var_os("HOME").map(PathBuf::from);
    let mut dirs: Vec<_> = env::var_os("DOCVERS_FONT_DIRS")
        .map(|value| env::split_paths(&value).collect())
        .unwrap_or_default();
    if cfg!(target_os = "macos") {
        dirs.extend(home.map(|path| path.join("Library/Fonts")));
        dirs.extend(
            [
                "/Library/Fonts",
                "/System/Library/Fonts",
                "/System/Library/Fonts/Supplemental",
            ]
            .map(PathBuf::from),
        );
    } else if cfg!(windows) {
        dirs.extend(
            env::var_os("LOCALAPPDATA")
                .map(|path| PathBuf::from(path).join("Microsoft/Windows/Fonts")),
        );
        dirs.push(
            PathBuf::from(env::var_os("WINDIR").unwrap_or_else(|| "C:\\Windows".into()))
                .join("Fonts"),
        );
    } else {
        dirs.extend(home.map(|path| path.join(".local/share/fonts")));
        dirs.extend(
            [
                "/usr/share/fonts",
                "/usr/local/share/fonts",
                "/usr/share/fonts/opentype/noto",
                "/usr/share/fonts/truetype/dejavu",
            ]
            .map(PathBuf::from),
        );
    }
    dirs.retain(|path| path.is_dir());
    dirs
}

fn configure(ctx: &egui::Context) {
    let preferred = if cfg!(target_os = "macos") {
        &["AppleSDGothicNeo.ttc", "AppleGothic.ttf"][..]
    } else if cfg!(windows) {
        &["segoeui.ttf", "malgun.ttf"][..]
    } else {
        &["NotoSansCJK-Regular.ttc", "DejaVuSans.ttf"][..]
    };
    let directories = font_directories();
    let custom = env::var_os("DOCVERS_UI_FONT").map(PathBuf::from);
    let candidates = custom
        .into_iter()
        .chain(preferred.iter().filter_map(|name| {
            directories
                .iter()
                .map(|dir| dir.join(name))
                .find(|path| path.is_file())
        }));
    let mut fonts = egui::FontDefinitions::default();
    let mut system_fonts = Vec::new();
    for path in candidates {
        if let Ok(bytes) = fs::read(path)
            && ttf_parser::Face::parse(&bytes, 0).is_ok()
        {
            let key = format!("gitdoc-system-{}", system_fonts.len());
            fonts
                .font_data
                .insert(key.clone(), egui::FontData::from_owned(bytes).into());
            system_fonts.push(key);
        }
    }
    fonts
        .families
        .entry(egui::FontFamily::Proportional)
        .or_default()
        .splice(0..0, system_fonts.clone());
    fonts
        .families
        .entry(egui::FontFamily::Monospace)
        .or_default()
        .extend(system_fonts);
    editor_fonts::remember_base(ctx, &fonts);
    ctx.set_fonts(fonts);
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
    // Lets a package check confirm what an installed app would start, without opening a window.
    if env::args().nth(1).as_deref() == Some("--print-paths") {
        println!(
            "{}",
            serde_json::json!({"root": root.to_string_lossy(), "python": python.to_string_lossy()})
        );
        return Ok(());
    }
    let setup = env::args()
        .nth(1)
        .map_or_else(|| Ok(Destination::default()), |arg| destination(&arg));
    let icon = std::sync::Arc::new(
        eframe::icon_data::from_png_bytes(include_bytes!("../../../static/gitdoc-64.png"))
            .expect("GitDoc icon must be a valid PNG"),
    );
    eframe::run_native(
        "GitDoc",
        eframe::NativeOptions {
            viewport: egui::ViewportBuilder::default()
                .with_icon(icon.clone())
                .with_inner_size([1360.0, 920.0])
                .with_min_inner_size([800.0, 570.0]),
            ..Default::default()
        },
        Box::new(move |creation| {
            configure(&creation.egui_ctx);
            let updates = updates::Updates::new(&root, &python, &creation.egui_ctx);
            let bridge = bridge::Bridge::start(root, python, creation.egui_ctx.clone());
            let brand_icon = creation.egui_ctx.load_texture(
                "GitDoc mark",
                egui::ColorImage::from_rgba_unmultiplied(
                    [icon.width as usize, icon.height as usize],
                    &icon.rgba,
                ),
                egui::TextureOptions::LINEAR,
            );
            Ok(Box::new(GitDoc::new(bridge, setup, updates, brand_icon)))
        }),
    )
}
