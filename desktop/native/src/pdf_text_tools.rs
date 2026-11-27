use eframe::egui::*;
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
struct Target {
    index: usize,
    text: String,
    #[serde(rename = "box")]
    bounds: [f32; 4],
}

#[derive(Default)]
pub struct PdfTextEditor {
    targets: Vec<Target>,
    loaded: bool,
    selected: Option<usize>,
    replacement: String,
}

impl PdfTextEditor {
    pub fn load(&mut self, value: Value) {
        self.targets = serde_json::from_value(value).unwrap_or_default();
        self.loaded = true;
    }

    fn select(&mut self, index: usize) {
        if self.selected != Some(index) {
            self.selected = Some(index);
            self.replacement.clone_from(&self.targets[index].text);
        }
    }

    pub fn show(&mut self, ui: &mut Ui, preview: Option<&TextureHandle>, failed: bool) {
        if !self.loaded {
            if !failed {
                ui.label("편집할 PDF 본문을 불러오는 중…");
            }
            return;
        }
        if self.targets.is_empty() {
            ui.label("이 페이지에서 수정할 수 있는 본문 텍스트를 찾지 못했습니다.");
            return;
        }
        ui.label("페이지에서 본문을 선택하고 한 줄로 바꾸세요.");
        if let Some(texture) = preview {
            let image = ui.add(
                Image::new(texture)
                    .max_size(vec2(ui.available_width(), 330.0))
                    .sense(Sense::click()),
            );
            let bounds = |target: &Target| {
                let [x0, y0, x1, y1] = target.bounds;
                Rect::from_min_max(
                    image.rect.min + image.rect.size() * vec2(x0, y0),
                    image.rect.min + image.rect.size() * vec2(x1, y1),
                )
            };
            for (index, target) in self.targets.iter().enumerate() {
                let color = if self.selected == Some(index) {
                    ui.visuals().selection.stroke.color
                } else {
                    Color32::from_rgba_unmultiplied(53, 123, 184, 120)
                };
                ui.painter().rect_stroke(
                    bounds(target),
                    1.0,
                    Stroke::new(1.0_f32, color),
                    StrokeKind::Inside,
                );
            }
            if image.clicked()
                && let Some(position) = image.interact_pointer_pos()
            {
                let mut hits = self
                    .targets
                    .iter()
                    .enumerate()
                    .filter_map(|(index, target)| {
                        bounds(target).contains(position).then_some(index)
                    });
                if let Some(index) = hits.next()
                    && hits.next().is_none()
                {
                    self.select(index);
                }
            }
        }
        let label = self
            .selected
            .map_or("본문 선택", |index| self.targets[index].text.as_str());
        let mut chosen = self.selected;
        ComboBox::from_id_salt("pdf_text_target")
            .selected_text(label.chars().take(38).collect::<String>())
            .show_ui(ui, |ui| {
                for (index, target) in self.targets.iter().enumerate() {
                    let excerpt: String = target.text.chars().take(45).collect();
                    ui.selectable_value(
                        &mut chosen,
                        Some(index),
                        format!("{} · {excerpt}", index + 1),
                    );
                }
            });
        if let Some(index) = chosen {
            self.select(index);
            let label = ui.label("바꿀 본문");
            ui.add(TextEdit::singleline(&mut self.replacement).char_limit(2000))
                .labelled_by(label.id);
        }
    }

    pub fn fields(&self, page: usize) -> Option<Value> {
        let target = self.targets.get(self.selected?)?;
        (!self.replacement.trim().is_empty() && self.replacement != target.text).then(|| {
            json!({"operation":"replace_text_object", "page":page, "index":target.index,
                   "original":target.text, "replacement":self.replacement})
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preview_hit_targets_the_second_duplicate() {
        let ctx = Context::default();
        let mut editor = PdfTextEditor::default();
        editor.load(json!([
            {"index": 3, "text": "Duplicate", "box": [0.1, 0.15, 0.5, 0.25]},
            {"index": 7, "text": "Duplicate", "box": [0.1, 0.55, 0.5, 0.65]}
        ]));
        let preview = ctx.load_texture(
            "fixture",
            ColorImage::filled([200, 220], Color32::WHITE),
            TextureOptions::LINEAR,
        );
        let frame = |editor: &mut PdfTextEditor, events| {
            ctx.run(
                RawInput {
                    events,
                    ..Default::default()
                },
                |ctx| {
                    CentralPanel::default().show(ctx, |ui| editor.show(ui, Some(&preview), false));
                },
            )
        };
        let output = frame(&mut editor, vec![]);
        let point = output
            .shapes
            .iter()
            .find_map(|shape| match &shape.shape {
                epaint::Shape::Rect(rect)
                    if rect.stroke.color == Color32::from_rgba_unmultiplied(53, 123, 184, 120)
                        && rect.rect.center().y > 100.0 =>
                {
                    Some(rect.rect.center())
                }
                _ => None,
            })
            .expect("second visible hit region");
        let pointer = |pressed| Event::PointerButton {
            pos: point,
            button: PointerButton::Primary,
            pressed,
            modifiers: Modifiers::NONE,
        };
        frame(&mut editor, vec![Event::PointerMoved(point), pointer(true)]);
        frame(&mut editor, vec![pointer(false)]);
        assert_eq!(editor.selected, Some(1));
        editor.replacement = "Changed".into();
        assert_eq!(editor.fields(2).unwrap()["index"], 7);
    }
}
