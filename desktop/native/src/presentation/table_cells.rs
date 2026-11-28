use super::{Editor, Format, button, project};
use eframe::egui::*;
use serde_json::{Value, json};

#[derive(serde::Deserialize)]
pub(super) struct PageCell {
    coordinate: [u32; 2],
    text: Option<String>,
    #[serde(default)]
    layout: bool,
    #[serde(default)]
    split: bool,
    #[serde(rename = "box")]
    pub(super) bounds: [f32; 4],
    span: Option<[u32; 2]>,
}

impl PageCell {
    fn name(&self) -> String {
        let [row, col] = self.coordinate;
        match self.span {
            Some([rows, columns]) => format!("{}행 {}열 · 병합 {rows}×{columns}", row + 1, col + 1),
            None => format!("{}행 {}열", row + 1, col + 1),
        }
    }
}

pub(super) struct CellPaint {
    side: &'static str,
    width: &'static str,
    style: &'static str,
    border_color: [u8; 3],
    background: [u8; 3],
}

impl Default for CellPaint {
    fn default() -> Self {
        Self {
            side: "all",
            width: "0.3",
            style: "solid",
            border_color: [0, 0, 0],
            background: [252, 232, 175],
        }
    }
}

impl CellPaint {
    fn show(&mut self, ui: &mut Ui, mut apply: impl FnMut(&str, Value)) {
        let rgb = |[r, g, b]: [u8; 3]| format!("#{r:02X}{g:02X}{b:02X}");
        ui.menu_button("셀 테두리", |ui| {
            ui.horizontal_wrapped(|ui| {
                for (label, value) in [
                    ("네 면", "all"),
                    ("왼쪽", "left"),
                    ("오른쪽", "right"),
                    ("위", "top"),
                    ("아래", "bottom"),
                ] {
                    ui.selectable_value(&mut self.side, value, label);
                }
            });
            ui.horizontal(|ui| {
                ui.selectable_value(&mut self.style, "solid", "실선");
                ui.selectable_value(&mut self.style, "none", "선 없음");
            });
            ui.add_enabled_ui(self.style == "solid", |ui| {
                ui.horizontal(|ui| {
                    for (label, value) in [
                        ("0.2 mm", "0.2"),
                        ("0.3 mm", "0.3"),
                        ("0.5 mm", "0.5"),
                        ("0.7 mm", "0.7"),
                    ] {
                        ui.selectable_value(&mut self.width, value, label);
                    }
                });
                let label = ui.label("선 색");
                ui.color_edit_button_srgb(&mut self.border_color)
                    .labelled_by(label.id);
            });
            if ui.button("테두리 적용").clicked() {
                apply(
                    "border",
                    json!({"side":self.side, "style":self.style,
                    "width":self.width, "color":rgb(self.border_color)}),
                );
                ui.close();
            }
        })
        .response
        .on_hover_text("선택 범위의 각 셀에 지정한 면의 테두리를 적용합니다.");
        ui.menu_button("셀 배경", |ui| {
            let label = ui.label("배경색");
            ui.color_edit_button_srgb(&mut self.background)
                .labelled_by(label.id);
            if ui.button("배경색 적용").clicked() {
                apply("background", json!(rgb(self.background)));
                ui.close();
            }
            if ui.button("배경 없음").clicked() {
                apply("background", Value::Null);
                ui.close();
            }
        })
        .response
        .on_hover_text("선택 범위의 각 셀에 단색 배경을 적용합니다.");
    }
}

pub(super) struct CellDraft {
    text: String,
    focus: bool,
}

#[derive(Clone, Copy)]
pub(super) enum GridRemoval {
    Row,
    Column,
}

impl GridRemoval {
    fn action(self) -> (&'static str, &'static str) {
        match self {
            Self::Row => ("줄 지우기", "row_delete"),
            Self::Column => ("칸 지우기", "col_delete"),
        }
    }
}

impl Editor {
    pub(super) fn selected_table_cell(&self) -> Option<&PageCell> {
        self.page_tables
            .get(self.selected_table?)?
            .cells
            .get(self.selected_cell?)
    }

    pub(super) fn selected_cell_bounds(&self, canvas: Rect) -> Option<Rect> {
        let first = project(canvas, self.selected_table_cell()?.bounds);
        let end = self
            .cell_range_end
            .and_then(|index| self.page_tables.get(self.selected_table?)?.cells.get(index));
        Some(end.map_or(first, |cell| first.union(project(canvas, cell.bounds))))
    }

    pub(super) fn select_table_at(&mut self, canvas: Rect, pointer: Option<Pos2>, extend: bool) {
        let first = self.selected_cell;
        let selected = self.selected_table;
        self.selected_table = None;
        self.selected_cell = None;
        self.cell_range_end = None;
        let Some(point) = pointer else { return };
        let mut tables = self.page_tables.iter().enumerate().filter(|(_, table)| {
            table
                .bounds
                .is_some_and(|bounds| project(canvas, bounds).contains(point))
        });
        if let Some((index, table)) = tables.next()
            && tables.next().is_none()
        {
            self.selected_table = Some(index);
            self.hangul_tab = super::hangul_ribbon::HangulTab::Table;
            let mut cells = table
                .cells
                .iter()
                .enumerate()
                .filter(|(_, cell)| project(canvas, cell.bounds).contains(point));
            if let Some((index, _)) = cells.next()
                && cells.next().is_none()
            {
                self.selected_cell = Some(index);
                if extend
                    && selected == self.selected_table
                    && let Some(anchor) = first
                    && anchor != index
                {
                    self.selected_cell = first;
                    self.cell_range_end = Some(index);
                }
            }
        }
    }

    pub(super) fn table_grid_tools(
        &mut self,
        ui: &mut Ui,
        action: &mut Option<(&'static str, Value)>,
    ) {
        if self.format != Format::Hangul {
            return;
        }
        let Some(table) = self.selected_table.and_then(|i| self.page_tables.get(i)) else {
            return;
        };
        let coordinate = self.selected_table_cell().map(|cell| cell.coordinate);
        let enabled = table.editable_grid
            && coordinate.is_some()
            && self.cell_range_end.is_none()
            && self.text_edit().is_none();
        ui.add_enabled_ui(enabled, |ui| {
            ui.menu_button("줄/칸 편집", |ui| {
                for (label, change) in [
                    ("위에 줄 추가", "row_above"),
                    ("아래에 줄 추가", "row_below"),
                    ("왼쪽에 칸 추가", "col_left"),
                    ("오른쪽에 칸 추가", "col_right"),
                ] {
                    if ui.button(label).clicked()
                        && let Some(coordinate) = coordinate
                    {
                        *action = Some((
                            "edit_table_grid",
                            json!({
                                "address": table.address, "coordinate": coordinate,
                                "digest": self.edit_digest, "change": change
                            }),
                        ));
                        ui.close();
                    }
                }
                ui.separator();
                for (removal, count) in [
                    (GridRemoval::Row, table.rows),
                    (GridRemoval::Column, table.columns),
                ] {
                    if ui
                        .add_enabled(count > 1, Button::new(removal.action().0))
                        .on_disabled_hover_text("마지막 줄이나 칸은 지울 수 없습니다.")
                        .clicked()
                    {
                        self.grid_removal = Some(removal);
                        ui.close();
                    }
                }
            })
            .response
            .on_disabled_hover_text(if !table.editable_grid {
                "병합·보호 셀 또는 복잡한 표 구조가 있으면 줄과 칸을 편집할 수 없습니다."
            } else {
                "본문 입력을 먼저 반영하고 표의 셀 하나를 선택하세요."
            });
        });
    }

    pub(super) fn grid_dialog(&mut self, ctx: &Context) -> Option<super::Command> {
        let removal = self.grid_removal?;
        let selection = self
            .selected_table
            .and_then(|i| self.page_tables.get(i))
            .zip(self.selected_table_cell());
        let Some((table, cell)) = selection else {
            self.grid_removal = None;
            return None;
        };
        let (label, change) = removal.action();
        let modal = Modal::new(Id::new("hangul-grid-removal")).show(ctx, |ui| {
            ui.heading(label);
            let target = match removal {
                GridRemoval::Row => format!("{}행 전체", cell.coordinate[0] + 1),
                GridRemoval::Column => format!("{}열 전체", cell.coordinate[1] + 1),
            };
            ui.label(format!("{target}와 그 안의 셀 내용을 지웁니다."));
            ui.horizontal(|ui| (ui.button("취소").clicked(), ui.button("지우기").clicked()))
                .inner
        });
        if modal.should_close() || modal.inner.0 {
            self.grid_removal = None;
        } else if modal.inner.1 {
            let value = json!({"address": table.address, "coordinate": cell.coordinate,
                "digest": self.edit_digest, "change": change, "confirmed": true});
            self.grid_removal = None;
            return Some(self.request(ctx, "edit_table_grid", value));
        }
        None
    }

    pub(super) fn table_cell_tools(
        &mut self,
        ui: &mut Ui,
        action: &mut Option<(&'static str, Value)>,
    ) {
        if self.format != Format::Hangul {
            return;
        }
        let Some(table) = self.selected_table.and_then(|i| self.page_tables.get(i)) else {
            return;
        };
        ui.separator();
        ui.strong("셀 편집");
        let label = self
            .selected_table_cell()
            .map_or_else(|| "셀 선택".into(), PageCell::name);
        if table.cells.is_empty() {
            ui.add_enabled(false, Button::new(label))
                .on_disabled_hover_text("이 페이지에서 편집 가능한 셀을 찾지 못했습니다.");
            return;
        }
        ui.menu_button(label, |ui| {
            ScrollArea::vertical().max_height(220.0).show(ui, |ui| {
                for (index, cell) in table.cells.iter().enumerate() {
                    if ui
                        .selectable_label(self.selected_cell == Some(index), cell.name())
                        .clicked()
                    {
                        self.selected_cell = Some(index);
                        self.cell_range_end = None;
                        self.reveal_table = true;
                        ui.close();
                    }
                }
            });
        })
        .response
        .on_hover_text("행과 열로 셀을 선택합니다. 페이지에서 셀을 눌러 선택할 수도 있습니다.");
        let selected = self.selected_cell;
        let last = self.cell_range_end;
        let selection = selected.map(|index| &table.cells[index]);
        let editable = self.text_edit().is_none();
        if table.cells.len() > 1
            && let Some(index) = selected
        {
            ui.menu_button("범위 끝 선택", |ui| {
                ScrollArea::vertical()
                    .id_salt("range-end")
                    .max_height(200.0)
                    .show(ui, |ui| {
                        for (end, cell) in table
                            .cells
                            .iter()
                            .enumerate()
                            .filter(|(end, _)| *end != index)
                        {
                            if ui
                                .selectable_label(last == Some(end), cell.name())
                                .clicked()
                            {
                                self.cell_range_end = Some(end);
                                self.reveal_table = true;
                                ui.close();
                            }
                        }
                    });
            })
            .response
            .on_hover_text(
                "두 셀 사이의 직사각형 범위를 선택합니다. Shift를 누른 채 끝 셀을 눌러도 됩니다.",
            );
        }
        let last = self.cell_range_end.map(|index| &table.cells[index]);
        if let (Some(first), Some(last)) = (selection, last) {
            ui.label(format!("{} ↔ {}", first.name(), last.name()));
            button(
                ui,
                editable && table.editable_grid,
                "셀 합치기",
                "선택 범위의 내용을 첫 셀에 문단 순서로 합칩니다.",
                || {
                    *action = Some((
                        "edit_table_span",
                        json!({"address": table.address, "digest": self.edit_digest,
                    "coordinate": first.coordinate, "end": last.coordinate, "change": "merge"}),
                    ));
                },
            );
            if ui.button("범위 해제").clicked() {
                self.cell_range_end = None;
            }
            return;
        }
        if let Some(cell) = selection.filter(|cell| cell.span.is_some()) {
            button(
                ui,
                editable && cell.split,
                "병합 셀 나누기",
                "기존 줄·칸 경계로 나누고 내용은 왼쪽 위 셀에 둡니다.",
                || {
                    *action = Some((
                        "edit_table_span",
                        json!({"address": table.address, "digest": self.edit_digest,
                    "coordinate": cell.coordinate, "change": "split"}),
                    ));
                },
            );
            return;
        }
        let text = selection.and_then(|cell| cell.text.clone());
        button(
            ui,
            text.is_some() && self.text_edit().is_none(),
            "셀 내용 편집",
            "선택한 셀의 한 줄 텍스트를 바꿉니다. 입력 중인 본문은 먼저 반영하세요.",
            || self.cell_draft = text.map(|text| CellDraft { text, focus: true }),
        );
        if let Some(cell) = selection {
            let enabled = editable && table.subdivision;
            ui.add_enabled_ui(enabled, |ui| {
                ui.menu_button("행·열로 나누기", |ui| {
                    ui.label("원래 내용은 왼쪽 위 셀에 남습니다.");
                    for (index, label) in ["나눌 행 수", "나눌 열 수"].into_iter().enumerate() {
                        ui.horizontal(|ui| {
                            let title = ui.label(label);
                            ui.add(DragValue::new(&mut self.subdivision_counts[index]).range(1..=6))
                                .labelled_by(title.id);
                        });
                    }
                    let [rows, columns] = self.subdivision_counts;
                    if ui.add_enabled(rows > 1 || columns > 1, Button::new("나누기 적용"))
                        .on_hover_text("선택한 셀 안에 새 경계를 만듭니다.").clicked() {

                        *action = Some(("edit_table_subdivision", json!({
                            "address":table.address, "digest":self.edit_digest, "coordinate":cell.coordinate,
                            "rows":rows, "columns":columns
                        })));
                        ui.close();
                    }
                }).response.on_disabled_hover_text("병합·보호되지 않은 일반 표에서 한 셀을 선택하세요.");
            });
        }
    }

    pub(super) fn restore_table_selection(&mut self, selection: &Value) {
        let Ok(address) = serde_json::from_value::<Vec<u32>>(selection["address"].clone()) else {
            return;
        };
        let Ok(coordinate) = serde_json::from_value::<[u32; 2]>(selection["coordinate"].clone())
        else {
            return;
        };
        if let Some((index, table)) = self
            .page_tables
            .iter()
            .enumerate()
            .find(|(_, item)| item.address == address)
        {
            self.selected_table = Some(index);
            self.selected_cell = table
                .cells
                .iter()
                .position(|cell| cell.coordinate == coordinate);
            self.cell_range_end = serde_json::from_value::<[u32; 2]>(selection["end"].clone())
                .ok()
                .and_then(|end| table.cells.iter().position(|cell| cell.coordinate == end));
        }
    }

    pub(super) fn table_layout_tools(
        &mut self,
        ui: &mut Ui,
        action: &mut Option<(&'static str, Value)>,
    ) {
        if self.format != Format::Hangul {
            return;
        }
        let Some(table) = self
            .selected_table
            .and_then(|index| self.page_tables.get(index))
        else {
            return;
        };
        let Some(first) = self.selected_table_cell() else {
            return;
        };
        let last = self.cell_range_end.and_then(|index| table.cells.get(index));
        let mut selection = json!({"address":table.address, "digest":self.edit_digest, "coordinate":first.coordinate});
        if let Some(end) = last {
            selection["end"] = json!(end.coordinate);
        }
        let editable =
            self.text_edit().is_none() && first.layout && last.is_none_or(|cell| cell.layout);
        let mut apply = |change: &str, value: Value| {
            let mut params = selection.clone();
            params["change"] = json!(change);
            params["value"] = value;
            *action = Some(("edit_table_layout", params));
        };
        ui.separator();
        ui.strong("셀 속성");
        ui.add_enabled_ui(editable, |ui| {
            for (menu, change, items) in [
                (
                    "가로 맞춤",
                    "horizontal",
                    [("왼쪽", "left"), ("가운데", "center"), ("오른쪽", "right")],
                ),
                (
                    "세로 맞춤",
                    "vertical",
                    [("위", "TOP"), ("가운데", "CENTER"), ("아래", "BOTTOM")],
                ),
            ] {
                ui.menu_button(menu, |ui| {
                    for (label, value) in items {
                        if ui.button(label).clicked() {
                            apply(change, json!(value));
                            ui.close();
                        }
                    }
                });
            }
            self.cell_paint.show(ui, &mut apply);
            ui.add_enabled_ui(table.layout_grid, |ui| {
                ui.menu_button("셀 크기", |ui| {
                    ui.label("선택한 열과 행 전체에 적용합니다.");
                    for (index, label, button, change) in [
                        (0, "열 너비", "너비 적용", "width"),
                        (1, "최소 행 높이", "높이 적용", "height"),
                    ] {
                        ui.horizontal(|ui| {
                            let title = ui.label(label);
                            let value = &mut self.cell_dimensions_mm[index];
                            ui.add(DragValue::new(value).range(2..=180).suffix(" mm"))
                                .labelled_by(title.id);
                            if ui.button(button).clicked() {
                                apply(change, json!(*value));
                                ui.close();
                            }
                        });
                    }
                })
                .response
                .on_disabled_hover_text(
                    "셀 크기는 병합되지 않은 일반 표에서만 지정할 수 있습니다.",
                );
            });
        });
    }

    pub(super) fn cell_dialog(&mut self, ctx: &Context) -> Option<super::Command> {
        let (Some(table), Some(cell)) = (
            self.selected_table.and_then(|i| self.page_tables.get(i)),
            self.selected_table_cell(),
        ) else {
            self.cell_draft = None;
            return None;
        };
        let name = cell.name();
        let original = cell.text.clone()?;
        let (address, coordinate) = (table.address.clone(), cell.coordinate);
        let draft = self.cell_draft.as_mut()?;
        let modal = Modal::new(Id::new("hangul-cell-edit")).show(ctx, |ui| {
            ui.heading("셀 내용 편집");
            ui.label(name);
            if let Some(error) = &self.error {
                ui.colored_label(ui.visuals().error_fg_color, error);
            }
            let input = ui.add(
                TextEdit::singleline(&mut draft.text)
                    .desired_width(260.0)
                    .hint_text("셀에 넣을 텍스트"),
            );
            if std::mem::take(&mut draft.focus) {
                input.request_focus();
            }
            let changed = draft.text != original;
            let enter = input.lost_focus() && ui.input(|i| i.key_pressed(Key::Enter));
            ui.horizontal(|ui| {
                let cancel = ui.button("취소").clicked();
                let apply = ui.add_enabled(changed, Button::new("셀에 적용")).clicked();
                (cancel, changed && (apply || enter))
            })
            .inner
        });
        if modal.should_close() || modal.inner.0 {
            self.cell_draft = None;
        } else if modal.inner.1 {
            let value = json!({"address": address, "coordinate": coordinate,
                               "digest": self.edit_digest, "text": draft.text});
            return Some(self.request(ctx, "edit_table_cell", value));
        }
        None
    }
}
