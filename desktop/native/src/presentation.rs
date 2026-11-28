mod hangul_ribbon;
mod shape_tools;
mod table_cells;
mod workspace;

use crate::editor_fonts::{Fonts, Span, change_bounds};
use eframe::egui::*;
use serde_json::{Value, json};
use shape_tools::PageShape;
use std::{
    collections::{BTreeSet, HashSet},
    path::Path,
};

#[derive(Clone, Copy, Hash, PartialEq, Eq)]
struct Target {
    id: u64,
    cell: Option<[usize; 2]>,
}

impl Target {
    fn read(value: &Value) -> Option<Self> {
        Some(Self {
            id: value["id"].as_u64()?,
            cell: value["cell"]
                .as_array()
                .and_then(|a| Some([a.first()?.as_u64()? as usize, a.get(1)?.as_u64()? as usize])),
        })
    }

    fn json(self) -> Value {
        match self.cell {
            Some(cell) => json!({"id": self.id, "cell": cell}),
            None => json!({"id": self.id}),
        }
    }
}

struct Object {
    target: Target,
    bounds: [f32; 4],
    ink: Option<[f32; 4]>,
    inset: [f32; 2],
    size: f32,
    group: bool,
    parent: Option<u64>,
    text: Option<String>,
    name: String,
    align: Vec<String>,
    hangul_format: bool,
    fonts: Vec<Span>,
    baselines: Vec<(usize, f32, f32)>,
    font_reference: String,
}

impl Object {
    fn read(value: &Value) -> Option<Self> {
        Some(Self {
            target: Target::read(value)?,
            bounds: serde_json::from_value(value["box"].clone()).ok()?,
            ink: serde_json::from_value(value["ink"].clone()).ok(),
            inset: serde_json::from_value(value["inset"].clone()).unwrap_or([0.0, 0.0]),
            size: value["size"].as_f64().unwrap_or(18.0) as f32,
            group: value["group"] == true,
            parent: value["parent"].as_u64(),
            text: value["text"].as_str().map(str::to_owned),
            name: value["name"].as_str().unwrap_or("").to_owned(),
            hangul_format: value["hangul_format"] == true,
            align: serde_json::from_value(value["align"].clone()).unwrap_or_default(),
            fonts: serde_json::from_value(value["fonts"].clone()).unwrap_or_default(),
            baselines: serde_json::from_value(value["baselines"].clone()).unwrap_or_default(),
            font_reference: value["text"].as_str().unwrap_or("").into(),
        })
    }
}

#[derive(serde::Deserialize)]
struct PageTable {
    address: Vec<u32>,
    #[serde(rename = "box")]
    bounds: Option<[f32; 4]>,
    #[serde(default)]
    label: String,
    rows: u32,
    columns: u32,
    #[serde(default)]
    cells: Vec<table_cells::PageCell>,
    #[serde(default)]
    editable_grid: bool,
}

struct Text {
    target: Target,
    original: String,
    value: String,
    focus: bool,
    cursor: Option<usize>,
}

pub enum Command {
    Page(Value),
    Save(Value),
    Close,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Slides,
    Word,
    Hangul,
}

pub struct Editor {
    pub page: usize,
    format: Format,
    extension: String,
    pub waiting: bool,
    pub error: Option<String>,
    count: usize,
    width: f32,
    height: f32,
    image: Option<TextureHandle>,
    pixels: Option<ColorImage>,
    objects: Vec<Object>,
    selected: Option<Target>,
    multiple: BTreeSet<u64>,
    patched: HashSet<Target>,
    typefaces: Fonts,
    text: Option<Text>,
    next_text: Option<Target>,
    pending_target: Option<Target>,
    pending_event: Option<Event>,
    object_drag: Option<Vec2>,
    resize_drag: Option<Vec2>,
    session: Id,
    history_page: usize,
    undo: bool,
    redo: bool,
    dirty: bool,
    zoom: f32,
    show_navigator: bool,
    reveal: Option<Target>,
    font: String,
    font_size: i32,
    color: Color32,
    highlight: Color32,
    table: [u32; 2],
    page_tables: Vec<PageTable>,
    page_shapes: Vec<PageShape>,
    selected_shape: Option<usize>,
    shape_size: [u32; 2],
    hangul_tab: hangul_ribbon::HangulTab,
    hangul_fonts: Vec<String>,
    edit_digest: String,
    selected_table: Option<usize>,
    selected_cell: Option<usize>,
    cell_draft: Option<table_cells::CellDraft>,
    grid_removal: Option<table_cells::GridRemoval>,
    reveal_table: bool,
    reason: String,
    saving: bool,
    focus_reason: bool,
    closing: bool,
}

impl Editor {
    pub fn new(page: usize, session: Id, format: Format, extension: String) -> Self {
        Self {
            page,
            format,
            extension,
            waiting: true,
            error: None,
            count: 0,
            width: 720.0,
            height: 405.0,
            image: None,
            pixels: None,
            objects: Vec::new(),
            selected: None,
            multiple: BTreeSet::new(),
            patched: HashSet::new(),
            typefaces: Fonts::default(),
            text: None,
            next_text: None,
            pending_target: None,
            pending_event: None,
            object_drag: None,
            resize_drag: None,
            session,
            history_page: 0,
            undo: false,
            redo: false,
            dirty: false,
            zoom: 100.0,
            show_navigator: true,
            reveal: None,
            font: "Aptos".into(),
            font_size: 24,
            color: Color32::from_rgb(30, 89, 181),
            highlight: Color32::YELLOW,
            table: [2, 2],
            page_tables: Vec::new(),
            page_shapes: Vec::new(),
            selected_shape: None,
            shape_size: [0, 0],
            hangul_tab: Default::default(),
            hangul_fonts: Vec::new(),
            edit_digest: String::new(),
            selected_table: None,
            selected_cell: None,
            cell_draft: None,
            grid_removal: None,
            reveal_table: false,
            reason: String::new(),
            saving: false,
            focus_reason: false,
            closing: false,
        }
    }

    pub fn update(&mut self, ctx: &Context, result: &Value) -> Result<(), String> {
        self.undo = result["undo"] == true;
        self.redo = result["redo"] == true;
        self.dirty = result["dirty"] == true;
        if let Some(target) = Target::read(&result["patch"]) {
            let item = self
                .objects
                .iter_mut()
                .find(|item| item.target == target)
                .ok_or("정렬할 텍스트를 찾을 수 없습니다.")?;
            item.align =
                serde_json::from_value(result["patch"]["align"].clone()).unwrap_or_default();
            item.text = result["patch"]["text"].as_str().map(str::to_owned);
            if let Some(edit) = self.text.as_mut().filter(|edit| edit.target == target) {
                edit.original.clone_from(&edit.value);
                edit.focus = true;
            }
            self.next_text = None;
            self.pending_target = None;
            self.patched.insert(target);
            self.waiting = false;
            self.error = None;
            return Ok(());
        }
        self.patched.clear();
        self.typefaces.update(ctx, &result["fonts"]);
        let path = result["image"]
            .as_str()
            .ok_or("슬라이드 이미지를 찾을 수 없습니다.")?;
        let image = image::open(Path::new(path))
            .map_err(|e| e.to_string())?
            .into_rgba8();
        let size = [image.width() as usize, image.height() as usize];
        let pixels = ColorImage::from_rgba_unmultiplied(size, &image);
        self.image =
            Some(ctx.load_texture("pptx-edit-slide", pixels.clone(), TextureOptions::LINEAR));
        self.pixels = Some(pixels);
        let page = result["page"].as_u64().unwrap_or(1) as usize;
        if self.page != page {
            self.multiple.clear();
        }
        self.page = page;
        self.history_page = self.history_page.wrapping_add(1);
        self.count = result["count"].as_u64().unwrap_or(1) as usize;
        self.width = result["width"].as_f64().unwrap_or(720.0) as f32;
        self.height = result["height"].as_f64().unwrap_or(405.0) as f32;
        self.objects = result["objects"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Object::read)
            .collect();
        self.multiple.retain(|id| {
            self.objects
                .iter()
                .any(|item| item.target.id == *id && item.parent.is_none())
        });
        self.hangul_fonts =
            serde_json::from_value(result["hangul_fonts"].clone()).unwrap_or_default();
        self.page_tables = serde_json::from_value(result["tables"].clone()).unwrap_or_default();
        self.page_shapes = serde_json::from_value(result["shapes"].clone()).unwrap_or_default();
        self.selected_shape = None;
        self.edit_digest = result["edit_digest"].as_str().unwrap_or_default().into();
        self.selected_table = None;
        self.selected_cell = None;
        self.cell_draft = None;
        self.grid_removal = None;
        self.reveal_table = false;
        self.object_drag = None;
        self.resize_drag = None;
        self.selected = Target::read(&result["selected"])
            .or(self.pending_target.take())
            .filter(|target| self.objects.iter().any(|item| item.target == *target));
        self.text = None;
        if let Some(target) = self.next_text.take() {
            self.start_text(target, None);
            if let Ok([first, last]) =
                serde_json::from_value::<[usize; 2]>(result["hangul_selection"].clone())
            {
                let id = text_id(self.session, self.history_page, target);
                let mut state = TextEdit::load_state(ctx, id).unwrap_or_default();
                state.cursor.set_char_range(Some(text::CCursorRange::two(
                    text::CCursor::new(first),
                    text::CCursor::new(last),
                )));
                state.store(ctx, id);
            }
        }
        self.waiting = false;
        self.error = None;
        Ok(())
    }

    pub fn release_fonts(&mut self, ctx: &Context) {
        self.typefaces.reset(ctx);
    }

    pub fn set_error(&mut self, error: String) {
        self.waiting = false;
        self.next_text = None;
        self.pending_target = self.selected;
        self.error = Some(error);
    }

    fn text_edit(&self) -> Option<Value> {
        self.text
            .as_ref()
            .filter(|edit| edit.value != edit.original)
            .map(|edit| json!({"target": edit.target.json(), "text": edit.value}))
    }

    fn changed(&self) -> bool {
        self.dirty || self.text_edit().is_some()
    }

    fn selection(&self, ctx: &Context) -> Option<[usize; 2]> {
        let text = self.text.as_ref()?;
        let range =
            TextEdit::load_state(ctx, text_id(self.session, self.history_page, text.target))?
                .cursor
                .char_range()?;
        let [first, last] = range.sorted_cursors();
        Some([first.index, last.index])
    }

    fn insertion_tools(&mut self, ui: &mut Ui, action: &mut Option<(&str, Value)>) {
        let shapes = |ui: &mut Ui, action: &mut Option<(&str, Value)>| {
            let label = if self.format == Format::Slides {
                "도형"
            } else {
                "◇ 도형"
            };
            ui.menu_button(label, |ui| {
                for (label, value) in [
                    ("사각형", "rectangle"),
                    ("타원", "ellipse"),
                    ("삼각형", "triangle"),
                    ("화살표", "arrow"),
                ] {
                    if ui.button(label).clicked() {
                        *action = Some(("shape", json!(value)));
                        ui.close();
                    }
                }
            })
            .response
            .on_hover_text("본문에 기본 도형을 삽입합니다.");
        };
        let mut tables = |ui: &mut Ui, action: &mut Option<(&str, Value)>| {
            let label = if self.format == Format::Slides {
                "표"
            } else {
                "▦ 표"
            };
            ui.menu_button(label, |ui| {
                ui.horizontal(|ui| {
                    ui.add(
                        DragValue::new(&mut self.table[0])
                            .range(1..=12)
                            .suffix("행"),
                    );
                    ui.add(
                        DragValue::new(&mut self.table[1])
                            .range(1..=12)
                            .suffix("열"),
                    );
                });
                if ui.button("표 삽입").clicked() {
                    *action = Some(("table", json!(self.table)));
                    ui.close();
                }
            })
            .response
            .on_hover_text("행과 열 수를 지정해 본문에 표를 삽입합니다.");
        };
        ui.add_enabled_ui(self.count > 0, |ui| {
            if self.format == Format::Word {
                tables(ui, action);
                shapes(ui, action);
            } else {
                shapes(ui, action);
                tables(ui, action);
            }
        });
    }

    fn table_tools(&mut self, ui: &mut Ui, action: &mut Option<(&'static str, Value)>) {
        if self.page_tables.is_empty() {
            return;
        }
        ui.strong("표 레이아웃");
        let menu = if self.format == Format::Word {
            "문서 표"
        } else {
            "기존 표"
        };
        ui.menu_button(menu, |ui| {
            for (index, table) in self.page_tables.iter().enumerate() {
                let mut label =
                    format!("표 {} · {}행 × {}열", index + 1, table.rows, table.columns);
                if !table.label.is_empty() {
                    label.push_str(" · ");
                    label.push_str(&table.label);
                }
                if ui
                    .selectable_label(self.selected_table == Some(index), label)
                    .clicked()
                {
                    self.selected_table = Some(index);
                    self.selected_cell = None;
                    self.selected_shape = None;
                    self.reveal_table = true;
                    ui.close();
                }
            }
        });
        self.table_grid_tools(ui, action);
        self.table_cell_tools(ui);
        if let Some(table) = self
            .selected_table
            .and_then(|index| self.page_tables.get(index))
        {
            button(
                ui,
                self.text_edit().is_none(),
                "표 전체 삭제",
                "표와 셀 내용을 삭제합니다. 입력 중인 본문을 먼저 반영하세요.",
                || {
                    *action = Some((
                        "delete_table",
                        json!({"address": table.address, "digest": self.edit_digest}),
                    ));
                },
            );
        }
    }

    fn request(&mut self, ctx: &Context, operation: &str, value: Value) -> Command {
        let target = self.selected.map(Target::json);
        let paged_insertion =
            self.format != Format::Slides && matches!(operation, "table" | "shape");
        let same = !paged_insertion
            && !matches!(
                operation,
                "page"
                    | "undo"
                    | "redo"
                    | "new_slide"
                    | "duplicate_slide"
                    | "delete_slide"
                    | "delete_object"
                    | "delete_table"
                    | "edit_table_cell"
                    | "edit_table_grid"
                    | "delete_shape"
                    | "shape_up"
                    | "shape_down"
                    | "shape_size"
                    | "group"
                    | "ungroup"
            );
        self.pending_target = if same { self.selected } else { None };
        if same && operation != "move_object" {
            self.next_text = self.text.as_ref().map(|edit| edit.target);
        }
        let number = if operation == "page" {
            value.as_u64().unwrap_or(self.page as u64)
        } else {
            self.page as u64
        };
        let mut params = json!({"number": number, "operation": operation, "value": value,
            "target": target, "ids": self.multiple, "selection": self.selection(ctx),
            "patch_text": operation == "align" && self.image.is_some() && self.format == Format::Slides});
        // Text belongs to the current slide, including when navigating away.
        if let Some(edit) = self
            .text_edit()
            .filter(|_| !matches!(operation, "undo" | "redo"))
        {
            params["text_edit"] = edit;
            params["text_page"] = json!(self.page);
        }
        self.waiting = true;
        self.error = None;
        Command::Page(params)
    }

    fn start_text(&mut self, target: Target, cursor: Option<usize>) {
        let Some(object) = self.objects.iter().find(|item| item.target == target) else {
            return;
        };
        let Some(value) = &object.text else { return };
        if let Some(Span(_, name, size, _)) = object.fonts.first() {
            let label = self.typefaces.label(name);
            if !label.is_empty() {
                self.font = label.to_owned();
            }
            if size.is_finite() && *size > 0.0 {
                self.font_size = size.round() as i32;
            }
        }
        let value = value.replace('\u{000b}', "\n");
        self.selected = Some(target);
        self.text = Some(Text {
            target,
            original: value.clone(),
            value,
            focus: true,
            cursor,
        });
    }

    fn select(
        &mut self,
        ctx: &Context,
        item: Option<Target>,
        multi: bool,
        cursor: Option<usize>,
    ) -> Option<Command> {
        if self.waiting {
            return None;
        }
        if self
            .text
            .as_ref()
            .is_some_and(|edit| Some(edit.target) != item)
            && self.text_edit().is_some()
        {
            self.pending_target = item;
            self.next_text = item.filter(|_| !multi);
            self.waiting = true;
            return Some(Command::Page(
                json!({"number":self.page,"text_edit":self.text_edit()}),
            ));
        }
        if !multi
            && self
                .text
                .as_ref()
                .is_some_and(|edit| Some(edit.target) == item)
        {
            self.focus_text(ctx);
            ctx.request_repaint();
            return None;
        }
        self.text = None;
        if multi && self.format == Format::Slides {
            if let Some(target) = item {
                if let Some(current) = self
                    .selected
                    .filter(|t| self.multiple.is_empty() && t.id != target.id)
                {
                    self.multiple.insert(current.id);
                }
                if !self.multiple.insert(target.id) {
                    self.multiple.remove(&target.id);
                }
            }
            self.selected = item;
        } else {
            self.multiple.clear();
            self.selected = item;
            if let Some(target) = item {
                self.start_text(target, cursor);
            }
        }
        ctx.request_repaint();
        None
    }

    fn tool(&mut self, ui: &mut Ui) -> Option<(&'static str, Value)> {
        if self.format == Format::Hangul {
            return self.hangul_tools(ui);
        }
        let mut action = None;
        let text = self.selected.is_some_and(|target| {
            self.objects
                .iter()
                .any(|item| item.target == target && item.text.is_some())
        });
        let object = self.selected.is_some();
        let group = self.selected.is_some_and(|target| {
            self.objects
                .iter()
                .any(|item| item.target == target && item.group)
        });
        let root_objects = self.multiple.iter().all(|id| {
            self.objects
                .iter()
                .any(|item| item.target.id == *id && item.parent.is_none())
        });
        ui.add_enabled_ui(!self.waiting, |ui| {
            ui.horizontal_wrapped(|ui| {
                button(
                    ui,
                    self.undo || self.text_edit().is_some(),
                    "↶",
                    "실행 취소",
                    || action = Some(("undo_tool", Value::Null)),
                );
                button(
                    ui,
                    self.redo || self.text.is_some(),
                    "↷",
                    "다시 실행",
                    || action = Some(("redo_tool", Value::Null)),
                );
                ui.separator();
                button(
                    ui,
                    self.text.is_some(),
                    "잘라내기",
                    "Cmd/Ctrl+X",
                    || action = Some(("cut", Value::Null)),
                );
                button(ui, self.text.is_some(), "복사", "Cmd/Ctrl+C", || {
                    action = Some(("copy", Value::Null))
                });
                button(
                    ui,
                    self.text.is_some(),
                    "붙여넣기",
                    "Cmd/Ctrl+V",
                    || action = Some(("paste", Value::Null)),
                );
                ui.separator();
                ui.add_enabled_ui(text, |ui| {
                    let font_button = ui.add_sized(
                        [140.0, ui.spacing().interact_size.y],
                        Button::new(self.font.clone()).truncate(),
                    );
                    Popup::menu(&font_button).show(|ui| {
                        for value in [
                            "Aptos",
                            "Arial",
                            "Pretendard",
                            "Noto Sans CJK KR",
                            "Apple SD Gothic Neo",
                            "Malgun Gothic",
                            "Times New Roman",
                        ] {
                            if ui.button(value).clicked() {
                                self.font = value.into();
                                action = Some(("font", json!(value)));
                                ui.close();
                            }
                        }
                    });
                    font_button.on_hover_text(format!("글꼴 · {}", self.font));
                    let size_button = ui.add_sized(
                        [36.0, ui.spacing().interact_size.y],
                        Button::new(self.font_size.to_string()).truncate(),
                    );
                    Popup::menu(&size_button).show(|ui| {
                        for value in [
                            8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 44, 54, 72, 96, 120,
                        ] {
                            if ui.button(value.to_string()).clicked() {
                                self.font_size = value;
                                action = Some(("size", json!(value)));
                                ui.close();
                            }
                        }
                    });
                    size_button.on_hover_text("글자 크기");
                    for (label, tip, op) in [
                        ("B", "굵게 · Cmd/Ctrl+B", "bold"),
                        ("I", "기울임 · Cmd/Ctrl+I", "italic"),
                        ("U", "밑줄 · Cmd/Ctrl+U", "underline"),
                    ] {
                        if ui
                            .button(RichText::new(label).strong())
                            .on_hover_text(tip)
                            .clicked()
                        {
                            action = Some((op, Value::Null));
                        }
                    }
                    for (label, color, operation) in [
                        ("글자색", &mut self.color, "color"),
                        ("강조색", &mut self.highlight, "highlight"),
                    ] {
                        if operation == "highlight" && self.format == Format::Word {
                            continue;
                        }
                        ui.menu_button(RichText::new(label).color(*color), |ui| {
                            for (name, value) in [
                                ("검정", Color32::BLACK),
                                ("흰색", Color32::WHITE),
                                ("파랑", Color32::from_rgb(30, 89, 181)),
                                ("빨강", Color32::from_rgb(206, 43, 56)),
                                ("노랑", Color32::YELLOW),
                                ("초록", Color32::from_rgb(29, 128, 83)),
                            ] {
                                if ui.button(name).clicked() {
                                    *color = value;
                                    action = Some((
                                        operation,
                                        json!(format!(
                                            "{:02X}{:02X}{:02X}",
                                            value.r(),
                                            value.g(),
                                            value.b()
                                        )),
                                    ));
                                    ui.close();
                                }
                            }
                        });
                    }
                    ui.separator();
                    for (label, value) in [
                        ("왼쪽", "left"),
                        ("가운데", "center"),
                        ("오른쪽", "right"),
                        ("양쪽", "justify"),
                    ] {
                        button(ui, true, label, "문단 정렬", || {
                            action = Some(("align", json!(value)))
                        });
                    }
                    if self.format != Format::Slides {
                        return;
                    }
                    button(ui, true, "• 목록", "글머리 기호", || {
                        action = Some(("bullet", Value::Null))
                    });
                    button(ui, true, "1. 목록", "번호 매기기", || {
                        action = Some(("number", Value::Null))
                    });
                    button(ui, true, "내어쓰기", "내어쓰기", || {
                        action = Some(("indent", json!(-1)))
                    });
                    button(ui, true, "들여쓰기", "들여쓰기", || {
                        action = Some(("indent", json!(1)))
                    });
                    ui.menu_button("줄 간격", |ui| {
                        for value in ["1", "1.5", "2"] {
                            if ui.button(value).clicked() {
                                action = Some(("spacing", json!(value)));
                                ui.close();
                            }
                        }
                    });
                });
            });
            if self.format != Format::Slides {
                ui.horizontal_wrapped(|ui| {
                    ui.strong(if self.format == Format::Word {
                        "삽입"
                    } else {
                        "입력"
                    });
                    self.insertion_tools(ui, &mut action);
                });
                if !self.page_tables.is_empty() {
                    ui.horizontal_wrapped(|ui| self.table_tools(ui, &mut action));
                }
                self.paged_shape_tools(ui, &mut action);
                return;
            }
            ui.horizontal_wrapped(|ui| {
                button(
                    ui,
                    self.count > 0,
                    "＋ 새 슬라이드",
                    "현재 슬라이드 뒤에 삽입",
                    || action = Some(("new_slide", Value::Null)),
                );
                button(
                    ui,
                    self.count > 0,
                    "복제",
                    "현재 슬라이드 복제",
                    || action = Some(("duplicate_slide", Value::Null)),
                );
                button(
                    ui,
                    self.count > 1,
                    "삭제",
                    "현재 슬라이드 삭제",
                    || action = Some(("delete_slide", Value::Null)),
                );
                ui.separator();
                button(
                    ui,
                    self.count > 0,
                    "텍스트 상자",
                    "텍스트 상자 삽입",
                    || action = Some(("textbox", Value::Null)),
                );
                button(
                    ui,
                    self.count > 0,
                    "이미지",
                    "PNG 또는 JPEG 삽입",
                    || {
                        if let Some(image) = rfd::FileDialog::new()
                            .add_filter("이미지", &["png", "jpg", "jpeg"])
                            .pick_file()
                        {
                            action = Some(("image", json!(image)));
                        }
                    },
                );
                self.insertion_tools(ui, &mut action);
                ui.separator();
                ui.add_enabled_ui(object, |ui| {
                    ui.menu_button("정렬", |ui| {
                        for (label, value) in [
                            ("왼쪽", "left"),
                            ("가로 가운데", "horizontal"),
                            ("오른쪽", "right"),
                            ("위", "top"),
                            ("세로 가운데", "vertical"),
                            ("아래", "bottom"),
                        ] {
                            if ui.button(label).clicked() {
                                action = Some(("align_object", json!(value)));
                                ui.close();
                            }
                        }
                    });
                });
                button(
                    ui,
                    object,
                    "앞으로",
                    "선택한 개체를 한 단계 앞으로",
                    || action = Some(("forward", Value::Null)),
                );
                button(
                    ui,
                    object,
                    "뒤로",
                    "선택한 개체를 한 단계 뒤로",
                    || action = Some(("backward", Value::Null)),
                );
                button(
                    ui,
                    self.multiple.len() >= 2 && root_objects,
                    "그룹",
                    "Shift+클릭으로 개체를 여러 개 선택",
                    || action = Some(("group", Value::Null)),
                );
                ui.add_enabled_ui(object, |ui| {
                    ui.menu_button("맨 앞·맨 뒤", |ui| {
                        for (label, operation) in [("맨 앞으로", "front"), ("맨 뒤로", "back")]
                        {
                            if ui.button(label).clicked() {
                                action = Some((operation, Value::Null));
                                ui.close();
                            }
                        }
                    });
                });
                button(
                    ui,
                    group,
                    "그룹 해제",
                    "선택한 그룹 해제",
                    || action = Some(("ungroup", Value::Null)),
                );
            });
        });
        action
    }

    pub fn show(&mut self, ui: &mut Ui, title: &str, version: &str) -> Option<Command> {
        let (mut action, command) = self.workspace(ui, title, version);

        if self.waiting {
            return command;
        }
        if self.grid_removal.is_some() {
            return self.grid_dialog(ui.ctx());
        }
        if self.cell_draft.is_some() {
            return self.cell_dialog(ui.ctx());
        }
        if let Some(modal) = self.dialog(ui.ctx()) {
            return Some(modal);
        }
        if self.saving || self.closing {
            return None;
        }
        action = action.or_else(|| self.shortcuts(ui.ctx()));
        match action {
            Some(("select", value)) => {
                self.selected_shape = None;
                let target = Target::read(&value);
                self.reveal = target;
                self.select(ui.ctx(), target, false, None)
            }
            Some((operation @ ("copy" | "cut" | "paste" | "undo_tool" | "redo_tool"), _)) => {
                if operation == "paste" {
                    ui.ctx().send_viewport_cmd(ViewportCommand::RequestPaste);
                    self.focus_text(ui.ctx());
                    None
                } else if let Some((event, can_apply)) = self.edit_event(operation) {
                    if can_apply {
                        self.focus_text(ui.ctx());
                        self.pending_event = Some(event);
                        ui.ctx().request_repaint();
                        None
                    } else {
                        Some(self.request(
                            ui.ctx(),
                            if operation == "undo_tool" {
                                "undo"
                            } else {
                                "redo"
                            },
                            Value::Null,
                        ))
                    }
                } else {
                    None
                }
            }
            Some((name, value)) => Some(self.request(ui.ctx(), name, value)),
            None => command,
        }
    }

    fn focus_text(&self, ctx: &Context) {
        if let Some(edit) = &self.text {
            ctx.memory_mut(|memory| {
                memory.request_focus(text_id(self.session, self.history_page, edit.target))
            });
        }
    }

    fn edit_event(&self, operation: &str) -> Option<(Event, bool)> {
        match operation {
            "copy" => Some((Event::Copy, self.text.is_some())),
            "cut" => Some((Event::Cut, self.text.is_some())),
            "undo_tool" | "redo_tool" => {
                let redo = operation == "redo_tool";
                let local =
                    self.text_edit().is_some() || (redo && self.text.is_some() && !self.redo);
                Some((
                    Event::Key {
                        key: Key::Z,
                        physical_key: None,
                        pressed: true,
                        repeat: false,
                        modifiers: Modifiers {
                            shift: redo,
                            command: true,
                            ..Modifiers::NONE
                        },
                    },
                    local,
                ))
            }
            _ => None,
        }
    }

    fn dialog(&mut self, ctx: &Context) -> Option<Command> {
        let mut command = None;
        if self.saving {
            let modal = Modal::new(Id::new("save-slide-deck")).show(ctx, |ui| {
                ui.set_width(360.0);
                ui.heading("수정안 저장");
                let label = ui.label("변경 사유 (선택)");
                let response = ui.add(TextEdit::singleline(&mut self.reason).desired_width(340.0).char_limit(500)).labelled_by(label.id);
                if std::mem::take(&mut self.focus_reason) { response.request_focus(); }
                let enter = response.lost_focus() && ctx.input(|input| input.key_pressed(Key::Enter));
                ui.horizontal(|ui| {
                    if ui.button("저장").clicked() || enter {
                        self.waiting = true;
                        self.saving = false;
                        command = Some(Command::Save(json!({"reason": self.reason, "number": self.page, "text_edit": self.text_edit()})));
                    }
                    if ui.button("취소").clicked() { self.saving = false; }
                });
            });
            if modal.should_close() {
                self.saving = false;
            }
        }
        if self.closing {
            if !self.changed() {
                return Some(Command::Close);
            }
            let modal = Modal::new(Id::new("close-slide-deck")).show(ctx, |ui| {
                ui.heading("편집 종료");
                ui.label("저장하지 않은 변경을 폐기할까요?");
                ui.horizontal(|ui| {
                    if ui.button("변경 폐기").clicked() {
                        command = Some(Command::Close);
                    }
                    if ui.button("계속 편집").clicked() {
                        self.closing = false;
                    }
                });
            });
            if modal.should_close() {
                self.closing = false;
            }
        }
        command
    }

    fn shortcuts(&mut self, ctx: &Context) -> Option<(&'static str, Value)> {
        let consume = |modifiers, key| ctx.input_mut(|i| i.consume_key(modifiers, key));
        if consume(Modifiers::COMMAND, Key::S) && self.changed() {
            self.saving = true;
            self.focus_reason = true;
            ctx.request_repaint();
        }
        if self.selected.is_some() && (self.format != Format::Hangul || self.can_format_hangul()) {
            for (key, tool) in [(Key::B, "bold"), (Key::I, "italic"), (Key::U, "underline")] {
                if consume(Modifiers::COMMAND, key) && self.text.is_some() {
                    return Some((tool, Value::Null));
                }
            }
        }
        if self.text.is_none() {
            if self.redo
                && (consume(Modifiers::COMMAND | Modifiers::SHIFT, Key::Z)
                    || consume(Modifiers::COMMAND, Key::Y))
            {
                return Some(("redo", Value::Null));
            }
            if self.undo && consume(Modifiers::COMMAND, Key::Z) {
                return Some(("undo", Value::Null));
            }
            if self.selected.is_some() && self.format == Format::Slides {
                if consume(Modifiers::NONE, Key::Delete) || consume(Modifiers::NONE, Key::Backspace)
                {
                    return Some(("delete_object", Value::Null));
                }
                for (key, vector) in [
                    (Key::ArrowLeft, [-1, 0]),
                    (Key::ArrowRight, [1, 0]),
                    (Key::ArrowUp, [0, -1]),
                    (Key::ArrowDown, [0, 1]),
                ] {
                    if consume(Modifiers::NONE, key) {
                        return Some((
                            "move_object",
                            json!([
                                vector[0] as f32 / self.width,
                                vector[1] as f32 / self.height
                            ]),
                        ));
                    }
                }
            }
        }
        if self.page > 1 && consume(Modifiers::NONE, Key::PageUp) {
            return Some(("page", json!(self.page - 1)));
        }
        if self.page < self.count && consume(Modifiers::NONE, Key::PageDown) {
            return Some(("page", json!(self.page + 1)));
        }
        None
    }

    fn canvas(&mut self, ui: &mut Ui) -> Option<Command> {
        let Some(texture) = &self.image else {
            return None;
        };
        let limit = if self.format == Format::Slides {
            940.0
        } else {
            700.0
        };
        let width = (ui.available_width() - 24.0).clamp(180.0, limit) * self.zoom / 100.0;
        let left = ((ui.available_width() - width) / 2.0).max(0.0);
        let size = vec2(width, width * self.height / self.width);
        if self.format == Format::Word {
            ui.add_space(21.0);
        }
        let (_, space) = ui.allocate_space(vec2(left + size.x, size.y));
        let canvas = Rect::from_min_size(space.min + vec2(left, 0.0), size);
        self.ruler(ui, canvas);
        if !self.waiting
            && let Some(target) = self.reveal.take()
            && let Some(object) = self.objects.iter().find(|item| item.target == target)
        {
            let position = project(canvas, object.ink.unwrap_or(object.bounds));
            if !ui.clip_rect().contains_rect(position) {
                ui.scroll_to_rect(position, Some(Align::Center));
            }
        }
        ui.painter().rect_filled(
            canvas.translate(vec2(1.0, 3.0)).expand(1.0),
            3.0,
            Color32::from_black_alpha(36),
        );
        ui.painter().image(
            texture.id(),
            canvas,
            Rect::from_min_max(Pos2::ZERO, pos2(1.0, 1.0)),
            Color32::WHITE,
        );
        let response = ui.interact(
            canvas,
            ui.id().with("slide-canvas"),
            Sense::click_and_drag(),
        );
        let pointer = response
            .hover_pos()
            .or_else(|| response.interact_pointer_pos());
        let hit = pointer.and_then(|point| self.hit(canvas, point));
        if hit.is_some() && !self.waiting {
            ui.ctx().set_cursor_icon(CursorIcon::Text);
        }
        let border = Color32::from_rgb(63, 158, 249);
        if let Some(bounds) = self
            .selected_table
            .and_then(|index| self.page_tables.get(index))
            .and_then(|table| table.bounds)
        {
            let bounds = project(canvas, bounds);
            let bounds = self
                .selected_table_cell()
                .map_or(bounds, |cell| project(canvas, cell.bounds));
            if std::mem::take(&mut self.reveal_table) && !ui.clip_rect().contains_rect(bounds) {
                ui.scroll_to_rect(bounds, Some(Align::Center));
            }
            ui.painter().rect_stroke(
                bounds,
                0.0,
                Stroke::new(1.5_f32, border),
                StrokeKind::Inside,
            );
        }
        if !self.waiting
            && self.format == Format::Slides
            && let Some(object) = self
                .objects
                .iter()
                .find(|item| Some(item.target) == self.selected)
            && object.target.cell.is_none()
            && object.parent.is_none()
        {
            let bounds = project(canvas, object.bounds);
            let handle = ui
                .interact(
                    Rect::from_center_size(bounds.right_bottom(), vec2(11.0, 11.0)),
                    ui.id().with(("shape-resize", object.target.id)),
                    Sense::drag(),
                )
                .on_hover_cursor(CursorIcon::ResizeNwSe);
            if let Some(delta) = handle.total_drag_delta() {
                self.resize_drag = Some(delta);
            }
            let dragged = self.resize_drag.unwrap_or_default();
            if handle.dragged() {
                ui.painter().rect_stroke(
                    Rect::from_min_max(
                        bounds.min,
                        (bounds.max + dragged).max(bounds.min + vec2(3.0, 3.0)),
                    ),
                    0.0,
                    Stroke::new(1.5_f32, border),
                    StrokeKind::Inside,
                );
            }
            if handle.drag_stopped() {
                self.resize_drag = None;
                if dragged.length_sq() > 1.0 {
                    return Some(self.request(
                        ui.ctx(),
                        "resize_object",
                        json!([dragged.x / canvas.width(), dragged.y / canvas.height()]),
                    ));
                }
            }
            if self.text.is_none()
                && ui.input(|input| {
                    input
                        .pointer
                        .press_origin()
                        .is_some_and(|point| bounds.contains(point))
                })
                && let Some(delta) = response.total_drag_delta()
            {
                self.object_drag = Some(delta);
                ui.painter().rect_stroke(
                    bounds.translate(delta),
                    0.0,
                    Stroke::new(1.5_f32, border),
                    StrokeKind::Inside,
                );
            }
            if response.drag_stopped()
                && let Some(delta) = self.object_drag.take()
                && delta.length_sq() > 1.0
            {
                return Some(self.request(
                    ui.ctx(),
                    "move_object",
                    json!([delta.x / canvas.width(), delta.y / canvas.height()]),
                ));
            }
        }
        for object in self
            .objects
            .iter()
            .filter(|object| self.patched.contains(&object.target))
        {
            if self
                .text
                .as_ref()
                .is_some_and(|edit| edit.target == object.target)
            {
                continue;
            }
            if let (Some(text), Some(pixels)) = (&object.text, &self.pixels) {
                let (area, ink, size) = editing_rect(canvas, object, self.width, true);
                let background = backdrop(pixels, canvas, ink);
                ui.painter().rect_filled(ink, 0.0, background);
                let galley = object.text_layout(
                    &self.typefaces,
                    ui,
                    text,
                    FontId::proportional(size),
                    contrast(background),
                    area.width(),
                    canvas.width() / self.width,
                    true,
                );
                let galley = object.place_lines(galley, text, canvas, area.min, true);
                ui.painter().with_clip_rect(area.expand(2.0)).galley(
                    area.left_top(),
                    galley,
                    contrast(background),
                );
            }
        }
        let mut finish = false;
        if let Some(edit) = &mut self.text
            && let Some(object) = self.objects.iter().find(|item| item.target == edit.target)
        {
            let localized = self.patched.contains(&object.target);
            let (edit_box, ink_area, font_size) =
                editing_rect(canvas, object, self.width, localized);
            let color = self
                .pixels
                .as_ref()
                .map_or(Color32::WHITE, |image| backdrop(image, canvas, ink_area));
            let foreground = contrast(color);
            // Keep the original raster visible on focus and caret movement.
            // Reserve the background before the editor so it can be filled in
            // the same frame as the first actual text change.
            let background = ui.painter().add(Shape::Noop);
            let ctx = ui.ctx().clone();
            let id = text_id(self.session, self.history_page, edit.target);
            if edit.focus {
                if let Some(cursor) = edit.cursor.take() {
                    let mut state = TextEdit::load_state(&ctx, id).unwrap_or_default();
                    state
                        .cursor
                        .set_char_range(Some(text::CCursorRange::one(text::CCursor::new(cursor))));
                    state.store(&ctx, id);
                }
                ctx.memory_mut(|memory| memory.request_focus(id));
                edit.focus = false;
            }
            if let Some(event) = self.pending_event.take() {
                ctx.input_mut(|input| input.events.push(event));
            }
            let scale = canvas.width() / self.width;
            let typefaces = &self.typefaces;
            let original = &edit.original;
            let mut layout = |ui: &Ui, input: &dyn TextBuffer, wrap: f32| {
                let value = input.as_str();
                let color = if !localized && value == original {
                    Color32::TRANSPARENT
                } else {
                    foreground
                };
                let galley = object.text_layout(
                    typefaces,
                    ui,
                    value,
                    FontId::proportional(font_size),
                    color,
                    wrap,
                    scale,
                    localized,
                );
                object.place_lines(galley, value, canvas, edit_box.min, localized)
            };
            let mut editor_visible = ui.clip_rect().intersect(canvas);
            editor_visible.max.x = editor_visible.max.x.min(edit_box.right() + 1.0);
            editor_visible.min.x = editor_visible.min.x.max(edit_box.left() - 4.0);
            editor_visible.min.y = editor_visible
                .min
                .y
                .max(edit_box.top().min(ink_area.top()) - 4.0);
            editor_visible.max.y = editor_visible.max.y.min(edit_box.bottom() + 4.0);
            let mut child = ui.new_child(
                UiBuilder::new()
                    .max_rect(edit_box)
                    .layout(Layout::top_down(Align::Min)),
            );
            child.set_clip_rect(editor_visible);
            TextEdit::multiline(&mut edit.value)
                .id(id)
                .font(FontId::proportional(font_size))
                .margin(0)
                .frame(false)
                .desired_width(edit_box.width())
                .desired_rows(1)
                .text_color(foreground)
                .char_limit(20000)
                .interactive(!self.waiting)
                .layouter(&mut layout)
                .show(&mut child);
            if localized || edit.value != edit.original {
                ui.painter()
                    .set(background, Shape::rect_filled(ink_area, 0.0, color));
            }
            if !self.waiting
                && ctx.input_mut(|input| input.consume_key(Modifiers::NONE, Key::Escape))
            {
                finish = true;
            }
        }
        if self.format == Format::Slides {
            for object in &self.objects {
                let bounds = project(canvas, object.bounds);
                let selected = Some(object.target) == self.selected
                    || (object.target.cell.is_none() && self.multiple.contains(&object.target.id));
                let moving = Some(object.target) == self.selected
                    && (self.object_drag.is_some() || self.resize_drag.is_some());
                if selected && !moving {
                    ui.painter().rect_stroke(
                        bounds,
                        0.0,
                        Stroke::new(1.5_f32, border),
                        StrokeKind::Inside,
                    );
                    for anchor in [
                        bounds.left_top(),
                        bounds.right_top(),
                        bounds.left_bottom(),
                        bounds.right_bottom(),
                    ] {
                        ui.painter().rect_filled(
                            Rect::from_center_size(anchor, vec2(5.0, 5.0)),
                            0.0,
                            border,
                        );
                    }
                } else if self.selected.is_none() && hit == Some(object.target) {
                    ui.painter().rect_stroke(
                        bounds,
                        0.0,
                        Stroke::new(1.0_f32, border),
                        StrokeKind::Inside,
                    );
                }
            }
        }
        if finish {
            let selected = self.selected;
            if let Some(edit) = self.text_edit() {
                self.pending_target = selected;
                self.waiting = true;
                return Some(Command::Page(json!({"number":self.page,"text_edit":edit})));
            }
            if let Some(edit) = self.text.take() {
                ui.ctx().memory_mut(|memory| {
                    memory.surrender_focus(text_id(self.session, self.history_page, edit.target))
                });
            }
            return None;
        }
        if response.clicked() && !self.waiting {
            self.selected_shape = None;
            if self.format == Format::Hangul {
                self.select_table_at(canvas, pointer.filter(|_| hit.is_none()));
            }
            if self.selected_table.is_some() && hit.is_none() {
                return self.select(ui.ctx(), None, false, None);
            }
            let multi = ui.input(|input| input.modifiers.shift);
            let cursor = hit.zip(pointer).and_then(|(target, position)| {
                let object = self.objects.iter().find(|item| item.target == target)?;
                let value = object.text.as_ref()?.replace('\u{000b}', "\n");
                let localized = self.patched.contains(&target);
                let (editor, _, size) = editing_rect(canvas, object, self.width, localized);
                let galley = object.text_layout(
                    &self.typefaces,
                    ui,
                    &value,
                    FontId::proportional(size),
                    Color32::TRANSPARENT,
                    editor.width(),
                    canvas.width() / self.width,
                    localized,
                );
                let galley = object.place_lines(galley, &value, canvas, editor.min, localized);
                Some(galley.cursor_from_pos(position - editor.min).index)
            });
            return self.select(ui.ctx(), hit, multi, cursor);
        }
        None
    }

    fn hit(&self, canvas: Rect, point: Pos2) -> Option<Target> {
        self.objects
            .iter()
            .rev()
            .find(|item| {
                item.ink
                    .is_some_and(|ink| project(canvas, ink).expand(3.0).contains(point))
            })
            .or_else(|| {
                self.objects
                    .iter()
                    .rev()
                    .find(|item| project(canvas, item.bounds).contains(point))
            })
            .map(|item| item.target)
    }
}

fn button(ui: &mut Ui, enabled: bool, label: &str, tooltip: &str, fire: impl FnOnce()) {
    if ui
        .add_enabled(enabled, Button::new(label))
        .on_hover_text(tooltip)
        .clicked()
    {
        fire();
    }
}

fn text_id(session: Id, history_page: usize, target: Target) -> Id {
    session.with((history_page, target.id, target.cell))
}

fn project(rect: Rect, [left, top, right, bottom]: [f32; 4]) -> Rect {
    Rect::from_min_max(
        rect.min + vec2(left * rect.width(), top * rect.height()),
        rect.min + vec2(right * rect.width(), bottom * rect.height()),
    )
}

fn editing_rect(canvas: Rect, object: &Object, width: f32, localized: bool) -> (Rect, Rect, f32) {
    let outer = project(canvas, object.bounds).intersect(canvas);
    let ink = object.ink.map(|ink| project(canvas, ink));
    let inset = vec2(
        object.inset[0] * canvas.width(),
        object.inset[1] * canvas.height(),
    );
    let mut origin = ink.map_or(outer.min + inset, |r| r.min);
    if localized {
        origin.x = outer.left() + inset.x;
    }
    let size = (object.size * canvas.width() / width).clamp(10.0, 100.0);
    let start = origin.min(outer.max - vec2(8.0, 8.0)).max(canvas.min);
    let end = outer
        .max
        .max(start + vec2(45.0, size * 1.45))
        .min(canvas.max);
    let editor = Rect::from_min_max(start, end);
    let erase = ink.unwrap_or(editor);
    (editor, erase.expand(1.5).intersect(canvas), size)
}

fn contrast(color: Color32) -> Color32 {
    if color.r() as u32 + color.g() as u32 + (color.b() as u32) < 390 {
        Color32::WHITE
    } else {
        Color32::BLACK
    }
}

impl Object {
    fn text_layout(
        &self,
        fonts: &Fonts,
        ui: &Ui,
        value: &str,
        fallback: FontId,
        color: Color32,
        width: f32,
        scale: f32,
        aligned: bool,
    ) -> std::sync::Arc<Galley> {
        let alignment = if aligned { self.align.as_slice() } else { &[] };
        let justify = !alignment.is_empty() && alignment.iter().all(|value| value == "justify");
        let galley = fonts.layout(
            ui,
            value,
            &self.font_reference,
            &self.fonts,
            scale,
            fallback,
            color,
            width,
            justify,
        );
        align_galley(galley, alignment, width)
    }

    fn place_lines(
        &self,
        mut galley: std::sync::Arc<Galley>,
        value: &str,
        canvas: Rect,
        origin: Pos2,
        aligned: bool,
    ) -> std::sync::Arc<Galley> {
        if self.baselines.is_empty() {
            return galley;
        }
        let (prefix, old_suffix, new_suffix) = change_bounds(&self.font_reference, value);
        let moved = std::sync::Arc::make_mut(&mut galley);
        let mut offset = 0;
        let mut last_delta = vec2(0.0, 0.0);
        let mut bounds = Rect::NOTHING;
        for (row_index, row) in moved.rows.iter_mut().enumerate() {
            if let Some((column, glyph)) = row
                .glyphs
                .iter()
                .enumerate()
                .find(|(_, glyph)| !glyph.chr.is_whitespace())
            {
                let index = offset + column;
                let reference_index = if index < prefix {
                    Some(index)
                } else if index >= new_suffix {
                    Some(old_suffix + index - new_suffix)
                } else {
                    None
                };
                let anchor = if row_index == 0 {
                    self.baselines.first()
                } else {
                    reference_index
                        .and_then(|index| self.baselines.iter().find(|mark| mark.0 == index))
                };
                if let Some(&(_, x, y)) = anchor {
                    let page_point = canvas.min + vec2(x * canvas.width(), y * canvas.height());
                    let delta = page_point - (origin + row.pos.to_vec2() + glyph.pos.to_vec2());
                    // Protect against ambiguous PDF glyph matches or a major reflow.
                    let limit = row.size.y * 3.0;
                    if delta.y.abs() <= limit && (aligned || delta.x.abs() <= limit) {
                        last_delta = vec2(if aligned { 0.0 } else { delta.x }, delta.y);
                    }
                }
            }
            row.pos += last_delta;
            bounds = bounds.union(row.visuals.mesh_bounds.translate(row.pos.to_vec2()));
            offset += row.glyphs.len() + usize::from(row.ends_with_newline);
        }
        moved.mesh_bounds = bounds;
        galley
    }
}

fn align_galley(
    galley: std::sync::Arc<Galley>,
    alignment: &[String],
    width: f32,
) -> std::sync::Arc<Galley> {
    if !alignment
        .iter()
        .any(|align| matches!(align.as_str(), "center" | "right"))
    {
        return galley;
    }
    let mut moved = (*galley).clone();
    let mut paragraph = 0;
    moved.mesh_bounds = Rect::NOTHING;
    for row in &mut moved.rows {
        let free = (width - row.size.x).max(0.0);
        let offset = match alignment.get(paragraph).map(String::as_str) {
            Some("center") => free * 0.5,
            Some("right") => free,
            _ => 0.0,
        };
        row.pos.x += offset;
        moved.mesh_bounds = moved
            .mesh_bounds
            .union(row.visuals.mesh_bounds.translate(row.pos.to_vec2()));
        paragraph += usize::from(row.ends_with_newline);
    }
    moved.rect.max.x = width;
    std::sync::Arc::new(moved)
}

fn backdrop(image: &ColorImage, canvas: Rect, target: Rect) -> Color32 {
    let point = target.left_top().lerp(target.left_bottom(), 0.5) - vec2(2.0, 0.0);
    let x = ((point.x - canvas.left()) / canvas.width() * image.size[0] as f32)
        .clamp(0.0, (image.size[0] - 1) as f32) as usize;
    let y = ((point.y - canvas.top()) / canvas.height() * image.size[1] as f32)
        .clamp(0.0, (image.size[1] - 1) as f32) as usize;
    image[(x, y)]
}
