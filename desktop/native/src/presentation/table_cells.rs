use super::{Editor, Format, button, project};
use eframe::egui::*;
use serde_json::json;

#[derive(serde::Deserialize)]
pub(super) struct PageCell {
    coordinate: [u32; 2],
    text: String,
    #[serde(rename = "box")]
    pub(super) bounds: [f32; 4],
}

impl PageCell {
    fn name(&self) -> String {
        let [row, col] = self.coordinate;
        format!("{}행 {}열", row + 1, col + 1)
    }
}

pub(super) struct CellDraft {
    text: String,
    focus: bool,
}

impl Editor {
    pub(super) fn selected_table_cell(&self) -> Option<&PageCell> {
        self.page_tables
            .get(self.selected_table?)?
            .cells
            .get(self.selected_cell?)
    }

    pub(super) fn select_table_at(&mut self, canvas: Rect, pointer: Option<Pos2>) {
        self.selected_table = None;
        self.selected_cell = None;
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
            let mut cells = table
                .cells
                .iter()
                .enumerate()
                .filter(|(_, cell)| project(canvas, cell.bounds).contains(point));
            if let Some((index, _)) = cells.next()
                && cells.next().is_none()
            {
                self.selected_cell = Some(index);
            }
        }
    }

    pub(super) fn table_cell_tools(&mut self, ui: &mut Ui) {
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
                .on_disabled_hover_text(
                    "이 페이지에서 편집 가능한 한 줄 텍스트 셀을 찾지 못했습니다.",
                );
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
                        self.reveal_table = true;
                        ui.close();
                    }
                }
            });
        })
        .response
        .on_hover_text("행과 열로 셀을 선택합니다. 페이지에서 셀을 눌러 선택할 수도 있습니다.");
        let text = self.selected_table_cell().map(|cell| cell.text.clone());
        button(
            ui,
            text.is_some() && self.text_edit().is_none(),
            "셀 내용 편집",
            "선택한 셀의 한 줄 텍스트를 바꿉니다. 입력 중인 본문은 먼저 반영하세요.",
            || self.cell_draft = text.map(|text| CellDraft { text, focus: true }),
        );
    }

    pub(super) fn cell_dialog(&mut self, ctx: &Context) -> Option<super::Command> {
        let (Some(table), Some(cell)) = (
            self.selected_table.and_then(|i| self.page_tables.get(i)),
            self.selected_table_cell(),
        ) else {
            self.cell_draft = None;
            return None;
        };
        let (name, original) = (cell.name(), cell.text.clone());
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
