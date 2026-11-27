use eframe::egui::*;
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;

struct Block {
    id: u64,
    section: String,
    text: String,
}

struct Change {
    text: String,
    style: Map<String, Value>,
}

pub struct Editor {
    blocks: Vec<Block>,
    changes: BTreeMap<u64, Change>,
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
                .map(|(id, change)| json!({"id":id, "text":change.text, "style":change.style}))
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
                        .map_or(block.text.as_str(), |change| &change.text);
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
        let update = self.changes.entry(block.id).or_insert_with(|| Change {
            text: block.text.clone(),
            style: Map::new(),
        });
        ui.separator();
        ui.horizontal_wrapped(|ui| {
            for (name, label) in [
                ("bold", "굵게"),
                ("italic", "기울임"),
                ("underline", "밑줄"),
            ] {
                let active = update.style.get(name).and_then(Value::as_bool);
                if ui.selectable_label(active == Some(true), label).clicked() {
                    update
                        .style
                        .insert(name.into(), json!(active != Some(true)));
                }
            }
            let mut size = update
                .style
                .get("size")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            ui.label("글자 크기");
            if ui
                .add(DragValue::new(&mut size).range(0..=120).suffix(" pt"))
                .changed()
            {
                if size == 0 {
                    update.style.remove("size");
                } else {
                    update
                        .style
                        .insert("size".into(), json!(size.clamp(6, 120)));
                }
            }
            let mut align = update
                .style
                .get("align")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned();
            let options = [
                ("", "기존 정렬"),
                ("left", "왼쪽"),
                ("center", "가운데"),
                ("right", "오른쪽"),
                ("justify", "양쪽 맞춤"),
            ];
            ComboBox::from_id_salt("office-align")
                .selected_text(
                    options
                        .iter()
                        .find(|(key, _)| *key == align)
                        .map_or("기존 정렬", |(_, label)| *label),
                )
                .show_ui(ui, |ui| {
                    for (key, label) in options {
                        ui.selectable_value(&mut align, key.into(), label);
                    }
                });
            if align.is_empty() {
                update.style.remove("align");
            } else {
                update.style.insert("align".into(), json!(align));
            }
            let color = update
                .style
                .get("color")
                .and_then(Value::as_str)
                .and_then(|value| u32::from_str_radix(value, 16).ok());
            let mut change_color = color.is_some();
            let toggled = ui.checkbox(&mut change_color, "글자 색").changed();
            if change_color {
                let color = color.unwrap_or(0);
                let mut channels = [(color >> 16) as u8, (color >> 8) as u8, color as u8];
                if ui.color_edit_button_srgb(&mut channels).changed() || toggled {
                    update.style.insert(
                        "color".into(),
                        json!(format!(
                            "{:02X}{:02X}{:02X}",
                            channels[0], channels[1], channels[2]
                        )),
                    );
                }
            } else if toggled {
                update.style.remove("color");
            }
        });
        let mut font = update
            .style
            .get("font")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        if ui
            .add(TextEdit::singleline(&mut font).hint_text("글꼴 이름 (비우면 기존 글꼴)"))
            .changed()
        {
            if font.trim().is_empty() {
                update.style.remove("font");
            } else {
                update.style.insert("font".into(), json!(font.trim()));
            }
        }
        let text = ui.label("본문");
        if ui
            .add(
                TextEdit::multiline(&mut update.text)
                    .id_salt(("office-body", block.id))
                    .desired_rows(4)
                    .desired_width(f32::INFINITY)
                    .return_key(None),
            )
            .labelled_by(text.id)
            .changed()
        {
            update.text = update.text.replace(['\r', '\n', '\t'], " ");
        }
        let unchanged = update.text == block.text && update.style.is_empty();
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
