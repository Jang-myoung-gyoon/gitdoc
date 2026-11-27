use crate::{
    Destination,
    bridge::{Bridge, Message, Reply},
    paper::{self, PaperRow},
};
use eframe::egui::*;
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    path::{Path, PathBuf},
};

fn rows(value: &Value) -> &[Value] {
    value.as_array().map_or(&[], Vec::as_slice)
}
fn text(value: &Value) -> &str {
    value.as_str().unwrap_or("")
}
fn id(value: &Value) -> i64 {
    value.as_i64().unwrap_or(0)
}
fn count(value: &Value) -> usize {
    value.as_u64().unwrap_or(0) as usize
}
fn status(value: &Value) -> &'static str {
    match text(value) {
        "pending" => "검토 대기",
        "rejected" => "반려",
        _ => "확정",
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Action {
    Register,
    Branch,
    Submit,
    Edit,
    Approve,
    Reject,
    Restore,
    Merge,
}

struct Form {
    action: Action,
    title: &'static str,
    values: Value,
    name: String,
    reason: String,
    file: Option<PathBuf>,
    mode: String,
    sheet: String,
    cell: String,
    kind: String,
    find: String,
    replacement: String,
    page: usize,
    submitting: bool,
    error: Option<String>,
}

impl Form {
    fn new(action: Action, title: &'static str, values: Value) -> Self {
        Self {
            action,
            title,
            values,
            name: String::new(),
            reason: String::new(),
            file: None,
            mode: String::new(),
            sheet: String::new(),
            cell: String::new(),
            kind: "text".into(),
            find: String::new(),
            replacement: String::new(),
            page: 1,
            submitting: false,
            error: None,
        }
    }
    fn needs_file(&self) -> bool {
        matches!(self.action, Action::Register | Action::Submit)
            || self.action == Action::Merge && self.values["conflict"] == true
    }
}

#[derive(Default)]
struct PagePreview {
    textures: Vec<Option<TextureHandle>>,
    regions: Vec<[f32; 4]>,
    notes: Vec<String>,
}

#[derive(Default)]
struct Pane {
    data: Value,
    ratios: Vec<Vec<f32>>,
    previews: HashMap<usize, Option<PagePreview>>,
    index: Vec<(usize, usize)>,
    indexed_through: usize,
    index_failures: usize,
    indexing: bool,
    seek: Option<((usize, Option<usize>), bool)>,
    page: usize,
    selected_region: Option<(usize, usize)>,
    page_jump: Option<usize>,
    follow_region: bool,
    paper_offset: Option<f32>,
    scroll_to: bool,
    sheet: usize,
    cell: String,
    jump_cell: String,
    sheets: Vec<String>,
    sheet_changes: Vec<(String, String)>,
    changed_cells: HashMap<String, HashSet<String>>,
    table_start: usize,
}

pub struct GitDoc {
    bridge: Bridge,
    busy: usize,
    epoch: u64,
    doc: i64,
    branch: String,
    library: Value,
    detail: Value,
    ids: Vec<i64>,
    selection: Destination,
    query: String,
    notice: String,
    error: bool,
    pane: Pane,
    form: Option<Form>,
    zoom: f32,
    show_diff: bool,
    list_changes: bool,
    compare_base: i64,
    comparison_anchor: Option<i64>,
    branch_target: String,
    comparison: Value,
}

impl GitDoc {
    pub fn new(bridge: Bridge, destination: Result<Destination, String>) -> Self {
        let mut app = Self {
            bridge,
            busy: 0,
            epoch: 0,
            doc: 0,
            branch: "main".into(),
            library: Value::Null,
            detail: Value::Null,
            ids: vec![],
            selection: Destination::default(),
            query: String::new(),
            notice: String::new(),
            error: false,
            pane: Pane::default(),
            form: None,
            zoom: 100.0,
            show_diff: false,
            list_changes: false,
            compare_base: 0,
            comparison_anchor: None,
            branch_target: String::new(),
            comparison: Value::Null,
        };
        app.refresh();
        match destination {
            Ok(place) => {
                if let Some(doc) = place.doc {
                    app.open_doc(doc, place);
                }
            }
            Err(error) => app.notify(error, true),
        }
        app
    }

    fn notify(&mut self, message: impl Into<String>, error: bool) {
        self.notice = message.into();
        self.error = error;
    }
    fn request(&mut self, op: &'static str, scope: u64, params: Value) -> bool {
        if self.bridge.send.send(Message { op, scope, params }).is_ok() {
            if op != "changes" {
                self.busy += 1;
            }
            true
        } else {
            self.notify("문서 처리 엔진이 연결되지 않았습니다.", true);
            false
        }
    }
    fn refresh(&mut self) {
        self.request("library", 0, json!({"query":self.query}));
    }
    fn head(&self) -> i64 {
        id(&self.detail["branch"]["head_id"])
    }
    fn open_doc(&mut self, doc: i64, place: Destination) {
        self.doc = doc;
        self.branch = place.branch.clone().unwrap_or_else(|| "main".into());
        self.selection = place;
        self.epoch += 1;
        self.ids.clear();
        self.detail = Value::Null;
        self.comparison = Value::Null;
        self.pane = Pane::default();
        self.zoom = 100.0;
        self.request(
            "detail",
            self.epoch,
            json!({"doc":doc,"branch":self.selection.branch,"version":self.selection.ids.last()}),
        );
    }

    fn record(&self, version: i64) -> Option<&Value> {
        rows(&self.detail["versions"])
            .iter()
            .find(|row| id(&row["id"]) == version)
    }

    fn open_view(&mut self, ids: Vec<i64>, place: Destination) {
        self.epoch += 1;
        self.ids = ids;
        self.selection = place;
        self.pane = Pane::default();
        self.list_changes = false;
        self.request(
            "view",
            self.epoch,
            json!({"doc":self.doc,"ids":self.ids,
            "mode": if self.selection.pages {"pages"} else {"auto"}, "row_key":self.selection.row_key}),
        );
    }
    fn previous_version(&self, version: i64) -> i64 {
        self.record(version).map_or(0, |row| id(&row["base_id"]))
    }

    fn display(&mut self, version: i64, base: Option<i64>) {
        self.comparison = Value::Null;
        let ids = match base {
            Some(base) => vec![base, version],
            None => vec![version],
        };
        self.open_view(
            ids,
            Destination {
                pages: self.selection.pages,
                page: self.pane.page,
                row_key: self.selection.row_key.clone(),
                ..Default::default()
            },
        );
    }

    fn select_version(&mut self, version: i64, pending: bool) {
        if self.ids.last() == Some(&version) {
            return;
        }
        let previous = self.previous_version(version);
        let base = if let Some(anchor) = self.comparison_anchor {
            [
                anchor,
                previous,
                self.ids.last().copied().unwrap_or(0),
                self.head(),
            ]
            .into_iter()
            .find(|&candidate| candidate > 0 && candidate != version)
        } else {
            (pending && previous > 0).then_some(previous)
        };
        self.display(version, base);
    }
    fn go_to_page(&mut self, page: usize, highlight: Option<usize>) {
        let total = count(&self.pane.data["pages"]);
        if total > 0 {
            let page = page.clamp(1, total);
            self.pane.page = page;
            self.pane.selected_region = highlight.map(|region| (page, region));
            self.pane.page_jump = Some(page);
            self.pane.follow_region = false;
            self.pane.seek = None;
        }
    }

    fn request_page(&mut self, page: usize) {
        const PENDING_LIMIT: usize = 4;
        if self.pane.previews.contains_key(&page)
            || self
                .pane
                .previews
                .values()
                .filter(|preview| preview.is_none())
                .count()
                >= PENDING_LIMIT
        {
            return;
        }
        self.pane.previews.insert(page, None);
        let comparable = rows(&self.pane.data["counts"]).iter().all(|n| count(n) > 0);
        self.request(
            "page",
            self.epoch,
            json!({"doc":self.doc,"ids":self.ids,"number":page,"comparable":comparable,
                "paths":self.pane.data["paths"],"pdfium_directory":self.pane.data["pdfium_directory"],
                "pdf_notes":self.pane.data["pdf_notes"]}),
        );
    }

    fn open_form(&mut self, action: Action, title: &'static str, values: Value) {
        self.form = Some(Form::new(action, title, values));
    }
    fn edit_form(&mut self) {
        let extension = self
            .record(self.head())
            .map(|row| text(&row["extension"]))
            .unwrap_or("");
        let mode = match extension {
            "docx" | "pptx" | "hwp" | "hwpx" => "replace",
            "xlsx" => "cell",
            "pdf" => "page",
            _ => return,
        }
        .to_owned();
        self.open_form(
            Action::Edit,
            "문서 편집",
            json!({"doc":self.doc,"branch":self.branch,"base":self.head()}),
        );
        if let Some(form) = &mut self.form {
            form.mode = mode;
            form.sheet = self
                .pane
                .sheets
                .get(self.pane.sheet)
                .cloned()
                .unwrap_or_default();
            form.cell = self.pane.cell.clone();
            form.page = self.pane.page.max(1);
        }
    }
    fn file_form(&mut self, action: Action, path: Option<PathBuf>) {
        let path = path.or_else(|| {
            rfd::FileDialog::new()
                .add_filter(
                    "문서",
                    &[
                        "hwp", "hwpx", "doc", "docx", "docm", "pdf", "csv", "tsv", "ppt", "pptx",
                        "pptm", "xls", "xlsx", "xlsm", "odt", "ods", "odp",
                    ],
                )
                .pick_file()
        });
        if let Some(path) = path {
            if action == Action::Register {
                self.open_form(action, "문서 등록", json!({}));
            } else {
                self.open_form(
                    action,
                    "수정안 제출",
                    json!({"doc":self.doc,"branch":self.branch,"base":self.head()}),
                );
            }
            if let Some(form) = &mut self.form {
                form.name = path
                    .file_stem()
                    .and_then(|v| v.to_str())
                    .unwrap_or("")
                    .into();
                form.file = Some(path);
            }
        }
    }

    fn receive(&mut self, ctx: &Context, frame: &eframe::Frame, reply: Reply) {
        if reply.op != "changes" {
            self.busy = self.busy.saturating_sub(1);
        }
        let wanted = match reply.op {
            "page" | "detail" | "view" | "changes" | "branch_compare" => reply.scope == self.epoch,
            _ => true,
        };
        if !wanted {
            return;
        }
        if reply.op == "page"
            && !reply
                .page
                .is_some_and(|number| self.pane.previews.contains_key(&number))
        {
            return;
        }
        let result = match reply.result {
            Ok(result) => result,
            Err(error) => {
                if let Some(number) = reply.page.filter(|_| reply.op == "page") {
                    self.pane
                        .previews
                        .insert(number, Some(PagePreview::default()));
                }
                if reply.op == "changes" {
                    self.pane.indexing = false;
                    self.pane.seek = None;
                    let pages = count(&self.pane.data["pages"]);
                    self.pane.index_failures += pages.saturating_sub(self.pane.indexed_through);
                    self.pane.indexed_through = pages;
                }
                if reply.op == "edit"
                    && let Some(form) = self.form.as_mut().filter(|form| form.submitting)
                {
                    form.submitting = false;
                    form.error = Some(error);
                    return;
                }
                self.notify(error, true);
                return;
            }
        };
        match reply.op {
            "library" => self.library = result,
            "detail" => {
                self.detail = result;
                self.branch = text(&self.detail["branch"]["name"]).to_owned();
                let mut place = std::mem::take(&mut self.selection);
                let mut ids = std::mem::take(&mut place.ids);
                if ids.is_empty() {
                    ids.push(self.head());
                }
                if ids.first() == Some(&0) {
                    ids[0] = self
                        .record(ids[1])
                        .map(|v| id(&v["base_id"]))
                        .filter(|n| *n > 0)
                        .unwrap_or(self.head());
                }
                self.comparison_anchor = (ids.len() == 2 && ids[0] != ids[1]).then(|| ids[0]);
                self.compare_base = self.comparison_anchor.unwrap_or_else(|| {
                    let previous = self.previous_version(ids[0]);
                    if previous > 0 { previous } else { self.head() }
                });
                self.branch_target = rows(&self.detail["branches"])
                    .iter()
                    .map(|row| text(&row["name"]))
                    .find(|name| *name != self.branch)
                    .unwrap_or("")
                    .into();
                self.open_view(ids, place);
            }
            "view" => {
                self.pane.data = result;
                if let Some(books) = self.pane.data["books"].as_array() {
                    for book in books {
                        for sheet in rows(book) {
                            let key = text(&sheet["name"]).to_owned();
                            if !self.pane.sheets.contains(&key) {
                                self.pane.sheets.push(key);
                            }
                        }
                    }
                    if let Some(sheet) = &self.selection.sheet {
                        self.pane.sheet = self
                            .pane
                            .sheets
                            .iter()
                            .position(|name| name == sheet)
                            .unwrap_or(0);
                    }
                    self.pane.cell = self.selection.cell.clone().unwrap_or_default();
                    self.pane.jump_cell = self.pane.cell.clone();
                    self.pane.table_start =
                        cell_position(&self.pane.cell).map_or(0, |(row, _)| row.saturating_sub(3));
                    self.pane.sheet_changes = sheet_changes(books, &self.pane.sheets);
                    for (sheet, address) in &self.pane.sheet_changes {
                        self.pane
                            .changed_cells
                            .entry(sheet.clone())
                            .or_default()
                            .insert(address.clone());
                    }
                } else if count(&self.pane.data["pages"]) > 0 {
                    self.pane.ratios = serde_json::from_value(self.pane.data["ratios"].clone())
                        .unwrap_or_default();
                    self.go_to_page(self.selection.page.max(1), self.selection.change);
                } else if let Some(fault) = rows(&self.pane.data["faults"]).first() {
                    self.notify(text(fault).to_owned(), true);
                }
            }
            "page" => {
                let Some(number) = reply.page else { return };
                let textures = reply
                    .images
                    .into_iter()
                    .enumerate()
                    .map(|(side, image)| {
                        image.map(|color| {
                            ctx.load_texture(
                                format!("page-{number}-{side}"),
                                color,
                                TextureOptions::LINEAR,
                            )
                        })
                    })
                    .collect();
                self.pane.previews.insert(
                    number,
                    Some(PagePreview {
                        textures,
                        regions: serde_json::from_value(result["regions"].clone())
                            .unwrap_or_default(),
                        notes: serde_json::from_value(result["notes"].clone()).unwrap_or_default(),
                    }),
                );
                if self.pane.follow_region
                    && self
                        .pane
                        .selected_region
                        .is_some_and(|(page, _)| page == number)
                {
                    self.pane.page_jump = Some(number);
                    self.pane.follow_region = false;
                }
            }
            "changes" => {
                self.pane.indexing = false;
                let Some(number) = reply.page else { return };
                self.pane.indexed_through = number;
                if result.is_null() {
                    self.pane.index_failures += 1;
                } else {
                    self.pane
                        .index
                        .extend((0..count(&result)).map(|delta| (number, delta)));
                }
                if let Some((last, forward)) = self.pane.seek.take() {
                    self.seek_pages(last, forward);
                }
            }
            "branch_compare" => {
                let ids = vec![id(&result["base"]), id(&result["target"])];
                self.comparison_anchor = (ids[0] != ids[1]).then(|| ids[0]);
                self.comparison = result;
                self.open_view(ids, Destination::default());
            }
            "register" => {
                self.refresh();
                self.open_doc(id(&result), Destination::default());
                self.notify("문서를 등록했습니다.", false);
            }
            "branch" => {
                self.open_doc(
                    self.doc,
                    Destination {
                        branch: Some(text(&result).into()),
                        ..Default::default()
                    },
                );
                self.notify("브랜치를 만들었습니다.", false);
            }
            "submit" | "edit" => {
                if reply.op == "edit" {
                    self.form = None;
                }
                let place = Destination {
                    ids: vec![self.head(), id(&result)],
                    branch: Some(self.branch.clone()),
                    ..Default::default()
                };
                self.refresh();
                self.open_doc(self.doc, place);
                self.notify("수정안을 검토 대기로 저장했습니다.", false);
            }
            "review" | "restore" | "merge" => {
                self.refresh();
                self.open_doc(
                    self.doc,
                    Destination {
                        branch: Some(self.branch.clone()),
                        ..Default::default()
                    },
                );
                self.notify("버전 이력을 갱신했습니다.", false);
            }
            "browser" => self.notify("브라우저에서 문서를 열었습니다.", false),
            "print" => {
                match crate::printing::print(
                    text(&result["path"]),
                    text(&result["title"]),
                    text(&result["pdfium_directory"]),
                    frame,
                ) {
                    Ok(true) => self.notify("인쇄 작업을 보냈습니다.", false),
                    Ok(false) => self.notify("", false),
                    Err(error) => self.notify(error, true),
                }
            }
            "save" => self.notify(
                format!("{} 파일을 저장했습니다.", text(&result).to_uppercase()),
                false,
            ),
            _ => {}
        }
    }

    fn title(&mut self, ctx: &Context) {
        TopBottomPanel::top("title").show(ctx, |ui| {
            ui.add_space(5.0);
            ui.horizontal(|ui| {
                ui.heading("GitDoc");
                ui.separator();
                ui.label("문서 버전 관리");
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    if ui.button("＋ 문서 등록").clicked() {
                        self.file_form(Action::Register, None);
                    }
                    if ui.button("새로 고침").clicked() {
                        self.refresh();
                        if self.doc > 0 {
                            self.open_doc(
                                self.doc,
                                Destination {
                                    branch: Some(self.branch.clone()),
                                    ids: self.ids.clone(),
                                    page: self.pane.page,
                                    ..Default::default()
                                },
                            );
                        }
                    }
                    if self.busy > 0 {
                        ui.spinner();
                    }
                });
            });
            ui.add_space(5.0);
        });
    }
    fn sidebar(&mut self, ctx: &Context) {
        let mut chosen = 0;
        let mut branch = None;
        let mut selected = None;
        SidePanel::left("library")
            .resizable(true)
            .default_width(290.0)
            .width_range(220.0..=425.0)
            .show(ctx, |ui| {
                ui.heading("문서함");
                let search =
                    ui.add(TextEdit::singleline(&mut self.query).hint_text("제목 또는 파일명"));
                if search.changed() {
                    self.refresh();
                }
                ScrollArea::vertical()
                    .id_salt("documents")
                    .max_height((ui.available_height() * 0.44).max(105.0))
                    .show(ui, |ui| {
                        for doc in rows(&self.library) {
                            let doc_id = id(&doc["id"]);
                            let label =
                                format!("{}  {}", text(&doc["title"]), text(&doc["version_label"]));
                            if ui.selectable_label(doc_id == self.doc, label).clicked() {
                                chosen = doc_id;
                            }
                        }
                        if rows(&self.library).is_empty() {
                            ui.weak("문서가 없습니다.");
                        }
                    });
                if self.doc <= 0 || self.detail.is_null() {
                    return;
                }
                ui.separator();
                ui.horizontal(|ui| {
                    ui.heading("브랜치");
                    ComboBox::from_id_salt("branches")
                        .selected_text(&self.branch)
                        .show_ui(ui, |ui| {
                            for row in rows(&self.detail["branches"]) {
                                let name = text(&row["name"]);
                                if ui.selectable_label(name == self.branch, name).clicked()
                                    && name != self.branch
                                {
                                    branch = Some(name.to_owned());
                                }
                            }
                        });
                    if ui.button("＋").on_hover_text("브랜치 만들기").clicked() {
                        let selected = self
                            .ids
                            .last()
                            .copied()
                            .and_then(|id| self.record(id))
                            .filter(|row| row["status"] == "approved")
                            .map_or(self.head(), |row| id(&row["id"]));
                        self.open_form(
                            Action::Branch,
                            "브랜치 만들기",
                            json!({"doc":self.doc,"start":selected}),
                        );
                    }
                });
                ui.separator();
                ui.heading("버전 이력");
                ScrollArea::vertical().id_salt("history").show(ui, |ui| {
                    for version in rows(&self.detail["versions"]) {
                        let v = id(&version["id"]);
                        let badge = if v == self.head() {
                            "최신"
                        } else {
                            status(&version["status"])
                        };
                        let label = format!(
                            "{}  ·  {}\n{}",
                            text(&version["version_label"]),
                            badge,
                            text(&version["reason"])
                        );
                        if ui
                            .selectable_label(self.ids.last() == Some(&v), label)
                            .clicked()
                        {
                            selected = Some((v, version["status"] == "pending"));
                        }
                    }
                });
            });
        if chosen > 0 {
            self.open_doc(chosen, Destination::default());
        } else if let Some(branch) = branch {
            self.open_doc(
                self.doc,
                Destination {
                    branch: Some(branch),
                    ..Default::default()
                },
            );
        } else if let Some((version, compare)) = selected {
            self.select_version(version, compare);
        }
    }
    fn save_as(&mut self, selected: i64, filename: &str, format: &str) {
        let suggested = Path::new(filename).with_extension(format);
        let Some(path) = rfd::FileDialog::new()
            .set_file_name(suggested.to_string_lossy())
            .add_filter(format.to_uppercase(), &[format])
            .save_file()
        else {
            return;
        };
        if !path
            .extension()
            .and_then(|ext| ext.to_str())
            .is_some_and(|ext| ext.eq_ignore_ascii_case(format))
        {
            self.notify(format!(".{format} 확장자로 저장하세요."), true);
            return;
        }
        self.request(
            "save",
            0,
            json!({"doc": self.doc, "version": selected, "path": path, "format": format}),
        );
    }

    fn right_header(&mut self, ui: &mut Ui) {
        let selected = self.ids.last().copied().unwrap_or(0);
        let row = self.record(selected);
        let base = row.map_or(0, |v| id(&v["base_id"]));
        let approved = row.is_some_and(|v| v["status"] == "approved");
        let pending = row.is_some_and(|v| v["status"] == "pending");
        let filename = text(&self.pane.data["filename"]).to_owned();
        let formats = rows(&self.pane.data["save_as"]).to_vec();
        let extension = row.map_or("", |v| text(&v["extension"])).to_owned();
        ui.heading(RichText::new(text(&self.detail["title"])).size(23.0));
        ui.horizontal_wrapped(|ui|{
            ui.label(format!("{}  ·  {}",self.branch,rows(&self.pane.data["versions"]).iter().map(text).collect::<Vec<_>>().join(" → ")));
            if !formats.is_empty() {
                ui.menu_button("저장", |ui| {
                    for (index, format) in formats.iter().map(text).enumerate() {
                        let label = if index == 0 {
                            format!("원본 ({})", format.to_uppercase())
                        } else {
                            format.to_uppercase()
                        };
                        if ui.button(label).clicked() {
                            ui.close();
                            self.save_as(selected, &filename, format);
                        }
                    }
                });
            }
            if ui.add_enabled(selected > 0 && self.busy == 0, Button::new("인쇄")).clicked() {
                self.notify("인쇄용 문서를 준비하는 중입니다.", false);
                self.request("print", 0, json!({"doc":self.doc, "version":selected}));
            }
            if ui.add_enabled(selected > 0 && self.busy == 0, Button::new("브라우저에서 보기")).clicked() {
                let sheet = self.pane.sheets.get(self.pane.sheet);
                self.request("browser", 0, json!({"doc":self.doc, "ids":self.ids, "branch":self.branch,
                    "page":self.pane.page.max(1), "sheet":sheet, "cell":self.pane.cell,
                    "row_key":self.pane.data["row_key"], "pages":self.selection.pages}));
            }
            if ui.button("수정안 제출").clicked() { self.file_form(Action::Submit,None); }
            if ui.add_enabled(selected==self.head() && matches!(extension.as_str(),"hwp"|"hwpx"|"docx"|"pptx"|"xlsx"|"pdf"),Button::new("문서 편집")).clicked() {
                self.edit_form();
            }
            if ui.add_enabled(approved && selected!=self.head(),Button::new("이 버전 복원")).clicked() {
                self.open_form(Action::Restore,"이 버전 복원",json!({"doc":self.doc,"branch":self.branch,"base":self.head(),"version":selected}));
            }
        });
        ui.horizontal_wrapped(|ui| {
            if ui
                .add_enabled(
                    selected > 0 && self.ids.len() == 2,
                    Button::new("한 버전 보기"),
                )
                .clicked()
            {
                self.comparison_anchor = None;
                self.display(selected, None);
            }
            let choice = self
                .record(self.compare_base)
                .map_or("기준 버전", |v| text(&v["version_label"]))
                .to_owned();
            ComboBox::from_id_salt("compare-base")
                .selected_text(choice)
                .show_ui(ui, |ui| {
                    for version in rows(&self.detail["versions"]) {
                        let value = id(&version["id"]);
                        ui.selectable_value(
                            &mut self.compare_base,
                            value,
                            text(&version["version_label"]),
                        );
                    }
                });
            if ui
                .add_enabled(
                    selected > 0 && self.compare_base > 0 && self.compare_base != selected,
                    Button::new("두 버전 비교"),
                )
                .clicked()
            {
                self.comparison_anchor = Some(self.compare_base);
                self.display(selected, self.comparison_anchor);
            }
            if ui
                .add_enabled(
                    base > 0 && self.ids.as_slice() != [base, selected],
                    Button::new("이전본과 비교"),
                )
                .clicked()
            {
                self.compare_base = base;
                self.comparison_anchor = Some(base);
                self.display(selected, self.comparison_anchor);
            }
            if rows(&self.detail["branches"]).len() > 1 {
                ComboBox::from_id_salt("merge-branch")
                    .selected_text(&self.branch_target)
                    .show_ui(ui, |ui| {
                        for b in rows(&self.detail["branches"]) {
                            let name = text(&b["name"]);
                            if name != self.branch {
                                ui.selectable_value(&mut self.branch_target, name.to_owned(), name);
                            }
                        }
                    });
                if !self.branch_target.is_empty() && ui.button("브랜치 대조").clicked() {
                    self.request(
                        "branch_compare",
                        self.epoch,
                        json!({"doc":self.doc,"target":self.branch,"source":self.branch_target}),
                    );
                }
            }
        });
        if pending || !self.comparison.is_null() {
            ui.horizontal_wrapped(|ui|{
                if pending {
                    let correct_base=self.ids.as_slice()==[base,selected] && base==self.head();
                    if ui.add_enabled(correct_base,Button::new("승인")).clicked() {
                        self.open_form(Action::Approve,"수정안 승인",json!({"doc":self.doc,"version":selected,"base":base,"action":"approve"}));
                    }
                    if ui.button("반려").clicked() {
                        self.open_form(Action::Reject,"수정안 반려",json!({"doc":self.doc,"version":selected,"base":base,"action":"reject"}));
                    }
                    if !correct_base { ui.weak("승인하려면 최신 확정본과 수정안을 비교하세요."); }
                }
                if !self.comparison.is_null() {
                    let state=text(&self.comparison["state"]);
                    ui.label(format!("공통 조상 {}",text(&self.comparison["ancestor"])));
                    if state=="conflict" { ui.weak("충돌 해결본을 제출해야 합니다."); }
                    if ui.add_enabled(state!="merged",Button::new("병합")).clicked() {
                        self.open_form(Action::Merge,"브랜치 병합",json!({"doc":self.doc,"target":self.branch,
                            "source":self.comparison["source"],"base":self.comparison["base"],"head":self.comparison["target"],"conflict":state=="conflict"}));
                    }
                }
            });
        }
        ui.separator();
    }
    fn pager(&mut self, ui: &mut Ui) {
        let pages = count(&self.pane.data["pages"]);
        if pages == 0 {
            return;
        }
        let comparison =
            self.ids.len() == 2 && rows(&self.pane.data["counts"]).iter().all(|n| count(n) > 0);
        let mut page = self.pane.page.max(1);
        let selected_region = self
            .pane
            .selected_region
            .filter(|&(number, _)| number == page)
            .map(|(_, region)| region);
        let current = (page, selected_region);
        ui.horizontal_wrapped(|ui| {
            if ui.add_enabled(page > 1, Button::new("◀ 이전 쪽")).clicked() {
                self.go_to_page(page - 1, None);
            }
            if ui.add(DragValue::new(&mut page).range(1..=pages)).changed() {
                self.go_to_page(page, None);
            }
            ui.label(format!("/ {pages}"));
            if ui
                .add_enabled(page < pages, Button::new("다음 쪽 ▶"))
                .clicked()
            {
                self.go_to_page(page + 1, None);
            }
            ui.separator();
            if ui
                .add(
                    Slider::new(&mut self.zoom, 50.0..=200.0)
                        .suffix("%")
                        .text("확대"),
                )
                .changed()
            {
                self.go_to_page(page, selected_region);
            }
        });
        if comparison {
            ui.horizontal_wrapped(|ui| {
                if ui.button("이전 변경").clicked() {
                    self.seek_pages(current, false);
                }
                if ui.button("다음 변경").clicked() {
                    self.seek_pages(current, true);
                }
                if let Some(delta) = selected_region {
                    ui.label(format!("이 페이지의 변경 {}", delta + 1));
                }
                if self.pane.indexed_through < pages {
                    ui.label(format!(
                        "전체 변경 확인 중 · {} / {pages}쪽",
                        self.pane.indexed_through
                    ));
                } else if self.pane.index_failures == 0 {
                    ui.label(format!("전체 변경 {}건", self.pane.index.len()));
                } else {
                    ui.label(format!(
                        "전체 변경 {}건 · 확인 실패 {}쪽",
                        self.pane.index.len(),
                        self.pane.index_failures
                    ));
                }
                ui.checkbox(&mut self.list_changes, "변경 목록");
            });
            if self.list_changes {
                let mut selected = None;
                ScrollArea::vertical()
                    .id_salt("change-list")
                    .max_height(116.0)
                    .show(ui, |ui| {
                        if self.pane.index.is_empty() && self.pane.indexed_through == pages {
                            ui.weak("표시할 변경이 없습니다.");
                        }
                        for &(p, delta) in &self.pane.index {
                            if ui
                                .selectable_label(
                                    current == (p, Some(delta)),
                                    format!("{p}쪽 · 변경 {}", delta + 1),
                                )
                                .clicked()
                            {
                                selected = Some((p, delta));
                            }
                        }
                    });
                if let Some((p, delta)) = selected {
                    self.pane.seek = None;
                    self.go_to_page(p, Some(delta));
                }
            }
        }
        self.continuous_pages(ui, pages);
        if comparison && self.pane.indexed_through < pages {
            self.request_index();
        }
    }

    fn continuous_pages(&mut self, ui: &mut Ui, pages: usize) {
        let gap = 12.0;
        let side_count = self.ids.len();
        let available = (ui.available_width() - 32.0).max(0.0);
        let width = ((available - gap * (side_count - 1) as f32) / side_count as f32)
            .clamp(160.0, 790.0)
            * self.zoom
            / 100.0;
        let span = width * side_count as f32 + gap * (side_count - 1) as f32;
        let paper = paper::layout(&self.pane.ratios, pages, width);
        let height = paper[pages - 1].bottom;
        let left = ((available - span) / 2.0).max(0.0);
        let jump = self.pane.page_jump.take();
        let mut area = ScrollArea::both()
            .id_salt("paper")
            .auto_shrink([false, false]);
        if let Some(number) = jump {
            let row = &paper[number - 1];
            let mut offset = row.top;
            let region = self
                .pane
                .selected_region
                .filter(|&(page, _)| page == number);
            if let Some((_, change)) = region {
                if let Some(preview) = self.pane.previews.get(&number).and_then(Option::as_ref) {
                    if let Some((region, height)) = preview.regions.get(change).zip(
                        preview
                            .textures
                            .iter()
                            .zip(row.heights)
                            .find_map(|(texture, height)| texture.as_ref().and(height)),
                    ) {
                        offset += paper::HEADER + (region[1] + region[3]) * height / 2.0
                            - ui.available_height() / 2.0;
                    }
                } else {
                    self.pane.follow_region = true;
                }
            }
            area = area.vertical_scroll_offset(
                offset.clamp(0.0, (height - ui.available_height()).max(0.0)),
            );
        }
        let viewport = area.show_viewport(ui, |ui, viewport| {
            let origin = ui.cursor().min;
            ui.set_min_size(vec2(span + left, height));
            let visible = paper::visible(&paper, viewport.top(), viewport.bottom());
            for index in visible.clone() {
                self.draw_paper_row(
                    ui,
                    origin + vec2(left, 0.0),
                    &paper[index],
                    index + 1,
                    width,
                    gap,
                );
            }
            (visible, viewport)
        });
        let (visible, bounds) = viewport.inner;
        let offset = bounds.top();
        if jump.is_none()
            && self
                .pane
                .paper_offset
                .is_some_and(|previous| (offset - previous).abs() > 0.5)
        {
            let focus = offset + bounds.height().min(56.0);
            let near_end = offset > 0.0 && bounds.bottom() >= height - 1.0;
            let page = if near_end {
                pages
            } else {
                paper.partition_point(|row| row.top <= focus).max(1)
            };
            if self.pane.page != page {
                self.pane.page = page;
                self.pane.selected_region = None;
                self.pane.follow_region = false;
                self.pane.seek = None;
                ui.ctx().request_repaint();
            }
        }
        self.pane.paper_offset = Some(offset);

        // Keep textures close to the viewport and render the next row ahead of scrolling.
        let keep_from = visible.start.saturating_sub(1).max(1);
        let keep_to = (visible.end + 2).min(pages);
        self.pane
            .previews
            .retain(|number, preview| preview.is_none() || (keep_from..=keep_to).contains(number));
        if let Some(page) = jump {
            self.request_page(page);
        }
        for index in visible.clone() {
            self.request_page(index + 1);
        }
        if visible.end < pages {
            self.request_page(visible.end + 1);
        }
    }

    fn draw_paper_row(
        &self,
        ui: &mut Ui,
        origin: Pos2,
        row: &PaperRow,
        number: usize,
        width: f32,
        gap: f32,
    ) {
        let preview = self.pane.previews.get(&number).and_then(Option::as_ref);
        for side in 0..self.ids.len() {
            let start = origin + vec2(side as f32 * (width + gap), row.top);
            let header = Rect::from_min_size(start, vec2(width, paper::HEADER));
            ui.scope_builder(
                UiBuilder::new()
                    .id_salt(("page-header", number, side))
                    .max_rect(header),
                |ui| {
                    ui.horizontal(|ui| {
                        ui.strong(format!(
                            "{} · {number}쪽",
                            text(&self.pane.data["versions"][side])
                        ));
                        if side == 0
                            && let Some(preview) = preview.filter(|page| !page.notes.is_empty())
                        {
                            ui.menu_button("페이지 메모", |ui| {
                                for note in &preview.notes {
                                    ui.label(note);
                                }
                            });
                        }
                    });
                },
            );
            let page_origin = start + vec2(0.0, paper::HEADER);
            let rect =
                Rect::from_min_size(page_origin, vec2(width, row.heights[side].unwrap_or(36.0)));
            if let Some((preview, texture)) =
                preview.and_then(|page| Some((page, page.textures.get(side)?.as_ref()?)))
            {
                let image = ui
                    .put(rect, Image::new(texture).fit_to_exact_size(rect.size()))
                    .rect;
                let shade = if side == 0 {
                    Color32::from_rgba_unmultiplied(207, 49, 65, 59)
                } else {
                    Color32::from_rgba_unmultiplied(30, 169, 112, 59)
                };
                for (index, region) in preview.regions.iter().enumerate() {
                    let bounds = Rect::from_min_max(
                        image.min + vec2(region[0] * image.width(), region[1] * image.height()),
                        image.min + vec2(region[2] * image.width(), region[3] * image.height()),
                    );
                    ui.painter().rect_filled(bounds, 0.0, shade);
                    if self.pane.selected_region == Some((number, index)) {
                        ui.painter().rect_stroke(
                            bounds,
                            0.0,
                            Stroke::new(2.0_f32, Color32::from_rgb(31, 102, 174)),
                            StrokeKind::Inside,
                        );
                    }
                }
            } else {
                ui.painter()
                    .rect_filled(rect, 0.0, ui.visuals().faint_bg_color);
                let fault = text(&self.pane.data["faults"][side]);
                let message = if !fault.is_empty() {
                    fault
                } else if row.heights[side].is_none() {
                    "이 버전에는 이 쪽이 없습니다."
                } else if preview.is_none() {
                    "페이지 불러오는 중…"
                } else {
                    "이 쪽을 표시할 수 없습니다."
                };
                let label = Rect::from_min_size(rect.min, vec2(width, 28.0));
                ui.put(label, Label::new(RichText::new(message).weak()).truncate());
            }
        }
    }

    fn request_index(&mut self) {
        if self.pane.indexing {
            return;
        }
        let number = self.pane.indexed_through + 1;
        self.pane.indexing = self.request(
            "changes",
            self.epoch,
            json!({"number":number, "counts":self.pane.data["counts"], "paths":self.pane.data["paths"],
                "pdfium_directory":self.pane.data["pdfium_directory"]}),
        );
    }

    fn seek_pages(&mut self, last: (usize, Option<usize>), forward: bool) {
        let indexed = self.pane.indexed_through;
        let complete = indexed >= count(&self.pane.data["pages"]);
        if !forward && indexed < last.0 && !complete {
            self.pane.seek = Some((last, forward));
            return;
        }
        let index = &self.pane.index;
        let chosen = if forward {
            index
                .iter()
                .find(|p| last.1.map_or(p.0 >= last.0, |delta| **p > (last.0, delta)))
        } else {
            index
                .iter()
                .rev()
                .find(|p| last.1.map_or(p.0 <= last.0, |delta| **p < (last.0, delta)))
        };
        if let Some(&(page, delta)) = chosen {
            self.go_to_page(page, Some(delta));
        } else if forward && !complete {
            self.pane.seek = Some((last, forward));
            return;
        } else {
            self.notify("더 이동할 변경이 없습니다.", false);
        }
        self.pane.seek = None;
    }

    fn sheets(&mut self, ui: &mut Ui) {
        let is_table = self.pane.data["delimited"] == true;
        if !is_table {
            ui.horizontal_wrapped(|ui| {
                for (i, name) in self.pane.sheets.iter().enumerate() {
                    if ui.selectable_label(i == self.pane.sheet, name).clicked() {
                        self.pane.sheet = i;
                        self.pane.cell.clear();
                        self.pane.jump_cell.clear();
                    }
                }
            });
        }
        let name = self
            .pane
            .sheets
            .get(self.pane.sheet)
            .cloned()
            .unwrap_or_default();
        if is_table && self.ids.len() == 2 {
            let selected = self.pane.data["row_key"].as_str();
            let mut key = selected.map(str::to_owned);
            ui.horizontal(|ui| {
                ui.label("행 맞추기");
                ComboBox::from_id_salt("row-key")
                    .selected_text(selected.unwrap_or("행 번호"))
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut key, None, "행 번호");
                        for name in rows(&self.pane.data["row_keys"])
                            .iter()
                            .filter_map(Value::as_str)
                        {
                            ui.selectable_value(&mut key, Some(name.into()), name);
                        }
                    });
            });
            if key.as_deref() != selected {
                self.open_view(
                    self.ids.clone(),
                    Destination {
                        row_key: key,
                        ..Default::default()
                    },
                );
                return;
            }
        }
        ui.horizontal(|ui| {
            let input = ui.add(
                TextEdit::singleline(&mut self.pane.jump_cell)
                    .hint_text("A1")
                    .desired_width(62.0),
            );
            if ui.button("셀 이동").clicked()
                || input.lost_focus() && ui.input(|i| i.key_pressed(Key::Enter))
            {
                let test = self.pane.jump_cell.trim().to_uppercase();
                if let Some((row, _)) = cell_position(&test) {
                    self.pane.cell = test;
                    self.pane.table_start = row.saturating_sub(3);
                    self.pane.scroll_to = true;
                }
            }
            if self.ids.len() == 2 {
                if ui.button("이전 변경").clicked() {
                    self.seek_sheets(false);
                }
                if ui.button("다음 변경").clicked() {
                    self.seek_sheets(true);
                }
                ui.checkbox(&mut self.list_changes, "변경 목록");
            }
        });
        if self.list_changes && self.ids.len() == 2 {
            let mut selected = None;
            ScrollArea::vertical()
                .id_salt("sheet-changes")
                .max_height(100.0)
                .show(ui, |ui| {
                    if self.pane.sheet_changes.is_empty() {
                        ui.weak("변경이 없습니다.");
                    }
                    for (tab, addr) in &self.pane.sheet_changes {
                        let label = if addr.is_empty() {
                            format!("{tab} · 시트 추가 또는 삭제")
                        } else {
                            format!("{tab} · {addr}")
                        };
                        if ui.button(label).clicked() {
                            selected = Some((tab.clone(), addr.clone()));
                        }
                    }
                });
            if let Some((tab, addr)) = selected {
                self.select_cell(&tab, &addr);
            }
        }
        let name = self.pane.sheets.get(self.pane.sheet).unwrap_or(&name);
        let mut cell = None;
        let start = if self.pane.scroll_to {
            cell_position(&self.pane.cell).unwrap_or_default()
        } else {
            cell_position(&self.pane.jump_cell).unwrap_or_default()
        };
        const ROWS_PER_WINDOW: usize = 32;
        let mut row_start = start.0.saturating_sub(3);
        let col_start = start.1.saturating_sub(2);
        let mut row_end = (row_start + ROWS_PER_WINDOW).min(1048576);
        let mut col_end = (col_start + 10).min(16384);
        if is_table {
            let tables = rows(&self.pane.data["books"])
                .iter()
                .filter_map(|book| rows(book).first());
            let row_count = tables
                .clone()
                .map(|table| count(&table["row_count"]))
                .max()
                .unwrap_or(0);
            let col_count = tables
                .map(|table| count(&table["column_count"]))
                .max()
                .unwrap_or(0);
            ui.horizontal(|ui| {
                if ui
                    .add_enabled(self.pane.table_start > 0, Button::new("이전 행"))
                    .clicked()
                {
                    self.pane.table_start = self.pane.table_start.saturating_sub(ROWS_PER_WINDOW);
                }
                if ui
                    .add_enabled(
                        self.pane.table_start + ROWS_PER_WINDOW < row_count,
                        Button::new("다음 행"),
                    )
                    .clicked()
                {
                    self.pane.table_start += ROWS_PER_WINDOW;
                }
            });
            row_start = self.pane.table_start.min(row_count.saturating_sub(1));
            row_end = (row_start + ROWS_PER_WINDOW).min(row_count);
            col_end = col_end.min(col_count);
        }
        let changes = self.pane.changed_cells.get(name);
        ScrollArea::both()
            .id_salt("worksheet")
            .auto_shrink([false, false])
            .show(ui, |ui| {
                ui.horizontal_top(|ui| {
                    for (side, book) in rows(&self.pane.data["books"]).iter().enumerate() {
                        ui.vertical(|ui| {
                            ui.strong(text(&self.pane.data["versions"][side]));
                            let sheet =
                                rows(book).iter().find(|sheet| text(&sheet["name"]) == name);
                            if let Some(sheet) = sheet {
                                Grid::new(("sheet", side))
                                    .striped(true)
                                    .min_col_width(76.0)
                                    .show(ui, |ui| {
                                        ui.weak("");
                                        for column in col_start..col_end {
                                            ui.strong(column_name(column));
                                        }
                                        ui.end_row();
                                        for row in row_start..row_end {
                                            ui.weak(format!("{}", row + 1));
                                            for col in col_start..col_end {
                                                let addr =
                                                    format!("{}{}", column_name(col), row + 1);
                                                let cell_value = &sheet["cells"][&addr];
                                                let val = text(&cell_value[0]);
                                                let value = if val.is_empty()
                                                    && !text(&cell_value[1]).is_empty()
                                                {
                                                    format!("={}", text(&cell_value[1]))
                                                } else {
                                                    val.to_owned()
                                                };
                                                let changed = changes.is_some_and(|locations| {
                                                    locations.contains(&addr)
                                                });
                                                let mut label =
                                                    RichText::new(if value.is_empty() {
                                                        " ".into()
                                                    } else {
                                                        value
                                                    });
                                                if cell_value[2]["bold"] == true {
                                                    label = label.strong();
                                                }
                                                if changed {
                                                    label = label.background_color(if side == 0 {
                                                        Color32::from_rgb(250, 213, 217)
                                                    } else {
                                                        Color32::from_rgb(212, 243, 226)
                                                    });
                                                }
                                                let response = ui
                                                    .add_sized(
                                                        [84.0, 22.0],
                                                        Button::selectable(
                                                            self.pane.cell == addr,
                                                            label,
                                                        ),
                                                    )
                                                    .on_hover_text(&addr);
                                                if response.clicked() {
                                                    cell = Some(addr);
                                                }
                                            }
                                            ui.end_row();
                                        }
                                    });
                            } else {
                                ui.weak("이 버전에는 시트가 없습니다.");
                            }
                        });
                    }
                });
            });
        if let Some(addr) = cell {
            self.pane.cell = addr;
        }
        self.pane.scroll_to = false;
    }
    fn select_cell(&mut self, name: &str, address: &str) {
        if let Some(sheet) = self.pane.sheets.iter().position(|tab| tab == name) {
            self.pane.sheet = sheet;
            self.pane.cell = address.into();
            self.pane.jump_cell = address.into();
            self.pane.table_start =
                cell_position(address).map_or(0, |(row, _)| row.saturating_sub(3));
            self.pane.scroll_to = true;
        }
    }
    fn seek_sheets(&mut self, forward: bool) {
        let name = self.pane.sheets.get(self.pane.sheet);
        let changes = &self.pane.sheet_changes;
        let current = changes
            .iter()
            .position(|(tab, cell)| Some(tab) == name && cell == &self.pane.cell);
        let next = if forward {
            current.map_or(Some(0), |n| n.checked_add(1))
        } else {
            current.map_or(changes.len().checked_sub(1), |n| n.checked_sub(1))
        };
        if let Some((sheet, address)) = next.and_then(|index| changes.get(index)).cloned() {
            self.select_cell(&sheet, &address);
        } else {
            self.notify("더 이동할 변경이 없습니다.", false);
        }
    }
    fn central(&mut self, ctx: &Context) {
        CentralPanel::default().show(ctx, |ui| {
            if self.doc == 0 || self.detail.is_null() {
                ui.vertical_centered(|ui| {
                    ui.add_space(96.0);
                    ui.heading("문서를 선택하세요");
                    ui.weak("문서함에서 원본과 버전 이력을 확인할 수 있습니다.");
                    if ui.button("파일 선택").clicked() {
                        self.file_form(Action::Register, None);
                    }
                });
                return;
            }
            self.right_header(ui);
            ui.horizontal(|ui| {
                if self.pane.data["workbook"] == true {
                    let sheets = !self.selection.pages;
                    if ui
                        .button(if sheets {
                            "인쇄 레이아웃"
                        } else {
                            "시트 보기"
                        })
                        .clicked()
                    {
                        self.open_view(
                            self.ids.clone(),
                            Destination {
                                pages: sheets,
                                row_key: self.selection.row_key.clone(),
                                ..Default::default()
                            },
                        );
                    }
                }
                if self.ids.len() == 2 {
                    ui.checkbox(&mut self.show_diff, "추출 텍스트 차이");
                }
            });
            if self.ids.len() == 2 && self.show_diff {
                let report = text(&self.pane.data["diff"]);
                ScrollArea::vertical()
                    .id_salt("diff")
                    .max_height(160.0)
                    .show(ui, |ui| {
                        if report.is_empty() {
                            ui.weak("추출 텍스트 차이가 없습니다.");
                        } else {
                            ui.monospace(report);
                        }
                    });
            }
            if self.pane.data["books"].is_array() {
                self.sheets(ui);
            } else {
                self.pager(ui);
            }
        });
    }
    fn edit_dialog(&mut self, ctx: &Context) {
        let Some(form) = &mut self.form else {
            return;
        };
        let mut save = false;
        let mut cancel = false;
        let modal = Modal::new(Id::new(("form", form.title))).show(ctx, |ui| {
            ui.set_width(390.0);
            ui.heading(form.title);
            ui.add_enabled_ui(!form.submitting, |ui| {
                if form.needs_file() {
                    if let Some(path) = &form.file {
                        ui.label(path.file_name().unwrap_or_default().to_string_lossy());
                    }
                    if ui.button("파일 선택").clicked() {
                        form.file = rfd::FileDialog::new().pick_file();
                    }
                }
                if matches!(form.action, Action::Branch | Action::Register) {
                    ui.label(if form.action == Action::Register {
                        "문서 제목"
                    } else {
                        "브랜치 이름"
                    });
                    ui.text_edit_singleline(&mut form.name);
                }
                if form.action == Action::Edit {
                    match form.mode.as_str() {
                        "replace" => {
                            let label = ui.label("찾을 문구");
                            ui.text_edit_singleline(&mut form.find)
                                .labelled_by(label.id);
                            let label = ui.label("바꿀 문구");
                            ui.text_edit_singleline(&mut form.replacement)
                                .labelled_by(label.id);
                        }
                        "cell" => {
                            ui.label("시트 이름");
                            ui.text_edit_singleline(&mut form.sheet);
                            ui.label("셀 주소");
                            ui.text_edit_singleline(&mut form.cell);
                            ComboBox::from_id_salt("value-kind")
                                .selected_text(match form.kind.as_str() {
                                    "number" => "숫자",
                                    "formula" => "수식",
                                    "clear" => "값 지우기",
                                    _ => "텍스트",
                                })
                                .show_ui(ui, |ui| {
                                    for (key, label) in [
                                        ("text", "텍스트"),
                                        ("number", "숫자"),
                                        ("formula", "수식"),
                                        ("clear", "값 지우기"),
                                    ] {
                                        ui.selectable_value(&mut form.kind, key.into(), label);
                                    }
                                });
                            if form.kind != "clear" {
                                ui.text_edit_multiline(&mut form.replacement);
                            }
                        }
                        _ => {
                            ui.horizontal(|ui| {
                                ui.label("페이지");
                                ui.add(DragValue::new(&mut form.page).range(1..=usize::MAX));
                            });
                            if form.kind == "text" {
                                form.kind = "right".into();
                            }
                            ComboBox::from_id_salt("pdf-operation")
                                .selected_text(match form.kind.as_str() {
                                    "note" => "메모 추가",
                                    "left" => "왼쪽 회전",
                                    _ => "오른쪽 회전",
                                })
                                .show_ui(ui, |ui| {
                                    for (key, label) in [
                                        ("right", "오른쪽 회전"),
                                        ("left", "왼쪽 회전"),
                                        ("note", "메모 추가"),
                                    ] {
                                        ui.selectable_value(&mut form.kind, key.into(), label);
                                    }
                                });
                            if form.kind == "note" {
                                ui.text_edit_multiline(&mut form.replacement);
                            }
                        }
                    }
                }
                let required_reason = matches!(
                    form.action,
                    Action::Edit | Action::Submit | Action::Merge | Action::Restore
                );
                let required_name = matches!(form.action, Action::Register | Action::Branch);
                if required_reason || matches!(form.action, Action::Approve | Action::Reject) {
                    let label = ui.label(if required_reason {
                        "변경 사유"
                    } else {
                        "검토 의견"
                    });
                    ui.text_edit_multiline(&mut form.reason)
                        .labelled_by(label.id);
                }
                let valid = (!form.needs_file() || form.file.is_some())
                    && (!required_reason || !form.reason.trim().is_empty())
                    && (!required_name || !form.name.trim().is_empty());
                ui.horizontal(|ui| {
                    save = ui.add_enabled(valid, Button::new("확인")).clicked();
                    cancel = ui.button("취소").clicked();
                });
            });
            if form.submitting {
                ui.horizontal(|ui| {
                    ui.spinner();
                    ui.label("수정안을 저장하는 중…");
                });
            }
            if let Some(error) = &form.error {
                ui.colored_label(ui.visuals().error_fg_color, error);
            }
        });
        let escape = !form.submitting
            && modal.is_top_modal
            && !modal.any_popup_open
            && ctx.input_mut(|input| input.consume_key(Modifiers::NONE, Key::Escape));
        if save {
            let mut args = form.values.clone();
            let operation = match form.action {
                Action::Register => {
                    args["title"] = json!(form.name);
                    "register"
                }
                Action::Branch => {
                    args["name"] = json!(form.name);
                    "branch"
                }
                Action::Submit => "submit",
                Action::Edit => {
                    args["fields"] = match form.mode.as_str() {
                        "replace" => json!({"find":form.find,"replacement":form.replacement}),
                        "cell" => {
                            json!({"sheet":form.sheet,"cell":form.cell,"value_kind":form.kind,"value":form.replacement})
                        }
                        _ => {
                            json!({"page":form.page,"operation":form.kind,"note":form.replacement})
                        }
                    };
                    "edit"
                }
                Action::Approve | Action::Reject => {
                    args["note"] = json!(form.reason);
                    "review"
                }
                Action::Restore => "restore",
                Action::Merge => {
                    args.as_object_mut()
                        .expect("merge arguments")
                        .remove("conflict");
                    "merge"
                }
            };
            if let Some(file) = &form.file {
                args["file"] = json!(file);
            }
            if matches!(
                form.action,
                Action::Edit | Action::Submit | Action::Merge | Action::Restore
            ) {
                args["reason"] = json!(form.reason);
            }
            if form.action == Action::Edit {
                let sent = self.request(operation, 0, args);
                if let Some(form) = &mut self.form {
                    form.submitting = sent;
                    form.error = (!sent).then(|| self.notice.clone());
                }
            } else {
                self.form = None;
                self.request(operation, 0, args);
            }
        } else if cancel || escape {
            self.form = None;
        }
    }
}

impl eframe::App for GitDoc {
    fn update(&mut self, ctx: &Context, frame: &mut eframe::Frame) {
        while let Ok(reply) = self.bridge.read.try_recv() {
            self.receive(ctx, frame, reply);
        }
        if self.form.is_none()
            && let Some(path) = ctx.input(|i| {
                i.raw
                    .dropped_files
                    .iter()
                    .find_map(|file| file.path.clone())
            })
        {
            self.file_form(Action::Register, Some(path));
        }
        self.title(ctx);
        TopBottomPanel::bottom("status").show(ctx, |ui| {
            ui.add_space(3.0);
            ui.colored_label(
                if self.error {
                    Color32::from_rgb(164, 47, 43)
                } else {
                    Color32::DARK_GRAY
                },
                &self.notice,
            );
            ui.add_space(3.0);
        });
        self.sidebar(ctx);
        self.central(ctx);
        self.edit_dialog(ctx);
    }
}

fn cell_position(address: &str) -> Option<(usize, usize)> {
    let p = address.find(|c: char| c.is_ascii_digit())?;
    if p == 0 || p > 3 {
        return None;
    }
    let column = address[..p].bytes().try_fold(0usize, |n, c| {
        c.is_ascii_uppercase()
            .then(|| n * 26 + usize::from(c - b'A' + 1))
    })?;
    let row = address[p..].parse::<usize>().ok()?;
    if !(1..=1_048_576).contains(&row) || !(1..=16_384).contains(&column) {
        return None;
    }
    Some((row - 1, column - 1))
}

fn column_name(col: usize) -> String {
    let mut number = col + 1;
    let mut letters = Vec::new();
    while number > 0 {
        number -= 1;
        letters.push((b'A' + (number % 26) as u8) as char);
        number /= 26;
    }
    letters.into_iter().rev().collect()
}

fn sheet_changes(books: &[Value], names: &[String]) -> Vec<(String, String)> {
    if books.len() != 2 {
        return vec![];
    }
    let mut output = Vec::new();
    for name in names {
        let sheet = |i: usize| {
            rows(&books[i])
                .iter()
                .find(|sheet| text(&sheet["name"]) == name)
        };
        if let (Some(left), Some(right)) = (sheet(0), sheet(1)) {
            let keys = |s: &Value, field: &str| {
                s[field]
                    .as_object()
                    .into_iter()
                    .flat_map(|a| a.keys().cloned())
                    .collect::<std::collections::BTreeSet<_>>()
            };
            let addresses = keys(left, "cells")
                .union(&keys(right, "cells"))
                .cloned()
                .collect::<Vec<_>>();
            let mut changed = addresses
                .into_iter()
                .filter(|addr| left["cells"][addr] != right["cells"][addr])
                .collect::<Vec<_>>();
            for span in rows(&left["merges"]).iter().chain(rows(&right["merges"])) {
                if rows(&left["merges"]).contains(span) != rows(&right["merges"]).contains(span)
                    && let Some((origin, _)) = text(span).split_once(':')
                {
                    changed.push(origin.into());
                }
            }
            changed.sort_by_key(|s| cell_position(s));
            changed.dedup();
            output.extend(changed.into_iter().map(|addr| (name.clone(), addr)));
        } else {
            output.push((name.clone(), String::new()));
        }
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn spreadsheet_addresses_are_bounded() {
        assert_eq!(cell_position("AB92"), Some((91, 27)));
        assert_eq!(column_name(27), "AB");
        assert_eq!(cell_position("XFE1"), None);
        assert_eq!(cell_position("A0"), None);
    }
    #[test]
    fn aligns_sheets_by_name() {
        let books = json!([[{"name":"예산","cells":{"A1":["4"]}},{"name":"근거","cells":{}}],
                         [{"name":"근거","cells":{}},{"name":"예산","cells":{"A1":["5"]}}]]);
        assert_eq!(
            sheet_changes(rows(&books), &["예산".into(), "근거".into()]),
            vec![("예산".into(), "A1".into())]
        );
    }
}
