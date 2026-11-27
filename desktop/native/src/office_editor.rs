use eframe::egui::*;
use serde_json::{Value, json};
use std::collections::BTreeMap;

struct Block {
    id: u64,
    section: String,
    text: String,
}

pub struct Editor {
    blocks: Vec<Block>,
    changes: BTreeMap<u64, String>,
    selected: usize,
    query: String,
}

impl Editor {
    pub fn new(data: &Value) -> Self {
        let blocks = data
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|block| {
                Some(Block {
                    id: block["id"].as_u64()?,
                    section: block["section"].as_str()?.into(),
                    text: block["text"].as_str()?.into(),
                })
            })
            .collect();
        Self {
            blocks,
            changes: BTreeMap::new(),
            selected: 0,
            query: String::new(),
        }
    }

    pub fn has_changes(&self) -> bool {
        !self.changes.is_empty()
    }

    pub fn edits(&self) -> Value {
        json!(
            self.changes
                .iter()
                .map(|(id, change)| json!({"id":id, "text":change}))
                .collect::<Vec<_>>()
        )
    }

    pub fn show(&mut self, ui: &mut Ui) {
        if self.blocks.is_empty() {
            ui.weak("편집할 수 있는 본문 텍스트가 없습니다.");
            return;
        }
        ui.add(TextEdit::singleline(&mut self.query).hint_text("문단 또는 슬라이드 내용 검색"));
        let query = self.query.to_lowercase();
        ScrollArea::vertical()
            .id_salt("blocks")
            .max_height(145.0)
            .show(ui, |ui| {
                for (index, block) in self.blocks.iter().enumerate() {
                    let value = self
                        .changes
                        .get(&block.id)
                        .map_or(block.text.as_str(), String::as_str);
                    if !query.is_empty()
                        && !value.to_lowercase().contains(&query)
                        && !block.section.to_lowercase().contains(&query)
                    {
                        continue;
                    }
                    let preview: String = value.chars().take(90).collect();
                    let changed = if self.changes.contains_key(&block.id) {
                        " · 수정됨"
                    } else {
                        ""
                    };
                    if ui
                        .selectable_label(
                            self.selected == index,
                            format!("{} · {}{}\n{preview}", block.section, block.id, changed),
                        )
                        .clicked()
                    {
                        self.selected = index;
                    }
                }
            });
        let block = &self.blocks[self.selected];
        let update = self
            .changes
            .entry(block.id)
            .or_insert_with(|| block.text.clone());
        ui.separator();
        let text = ui.label("본문");
        if ui
            .add(
                TextEdit::multiline(update)
                    .id_salt(("office-body", block.id))
                    .desired_rows(4)
                    .desired_width(f32::INFINITY)
                    .return_key(None),
            )
            .labelled_by(text.id)
            .changed()
        {
            *update = update.replace(['\r', '\n', '\t'], " ");
        }
        let unchanged = *update == block.text;
        if ui
            .add_enabled(!unchanged, Button::new("이 단락 변경 취소"))
            .clicked()
            || unchanged
        {
            self.changes.remove(&block.id);
        }
        ui.weak(format!("변경한 단락 {}개", self.changes.len()));
    }
}
