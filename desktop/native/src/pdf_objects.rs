use crate::pages::PageImage;
use eframe::egui::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::sync::atomic::{AtomicU64, Ordering};

const TYPES: [(&str, &str); 5] = [
    ("text", "텍스트 상자"),
    ("rectangle", "사각형"),
    ("ellipse", "타원"),
    ("triangle", "삼각형"),
    ("arrow", "화살표"),
];

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
struct Object {
    id: Option<String>,
    kind: String,
    #[serde(rename = "box")]
    bounds: [f32; 4],
    text: String,
}

#[derive(Default)]
enum Tool {
    #[default]
    Select,
    Insert(&'static str, Option<Pos2>),
    Move,
}

enum Change {
    Open,
    Edit(Vec<Object>, Option<usize>),
    History(usize),
}

pub struct PdfObjects {
    scope: u64,
    preview: Option<TextureHandle>,
    history: Vec<Vec<Object>>,
    position: usize,
    selected: Option<usize>,
    pending: Option<Change>,
    tool: Tool,
    input: String,
    pub error: Option<String>,
}

impl PdfObjects {
    pub fn new() -> Self {
        static NEXT_SCOPE: AtomicU64 = AtomicU64::new(1);
        Self {
            scope: NEXT_SCOPE.fetch_add(1, Ordering::Relaxed),
            preview: None,
            history: Vec::new(),
            position: 0,
            selected: None,
            pending: Some(Change::Open),
            tool: Tool::Select,
            input: String::new(),
            error: None,
        }
    }

    pub fn scope(&self) -> u64 {
        self.scope
    }

    fn objects(&self) -> &[Object] {
        &self.history[self.position]
    }

    pub fn load(&mut self, ctx: &Context, reply: Result<Value, String>, image: Option<PageImage>) {
        let Some(change) = self.pending.take() else {
            return;
        };
        let result = reply.and_then(|value| match image {
            Some(bitmap) => Ok((value, bitmap)),
            None => Err("PDF 미리보기를 읽을 수 없습니다.".into()),
        });
        let (value, bitmap) = match result {
            Ok(data) => data,
            Err(error) => {
                self.error = Some(error);
                return;
            }
        };
        match change {
            Change::Open => {
                let Ok(initial) = serde_json::from_value(value["objects"].clone()) else {
                    self.error = Some("PDF 개체 목록을 읽을 수 없습니다.".into());
                    return;
                };
                self.history.push(initial);
            }
            Change::Edit(objects, selected) => {
                self.history.truncate(self.position + 1);
                self.history.push(objects);
                self.position += 1;
                self.selected = selected;
                self.tool = Tool::Select;
            }
            Change::History(position) => {
                self.position = position;
                self.selected = None;
                self.tool = Tool::Select;
            }
        }
        self.preview = Some(ctx.load_texture(
            format!("pdf-object-preview-{}", self.scope),
            bitmap,
            TextureOptions::LINEAR,
        ));
        self.error = None;
    }

    pub fn request_failed(&mut self, error: String) {
        self.pending = None;
        self.error = Some(error);
    }

    pub fn fields(&self, page: usize) -> Option<Value> {
        if self.pending.is_some() || self.history.is_empty() || self.objects() == self.history[0] {
            return None;
        }
        Some(json!({"operation":"edit_page_objects", "page":page, "objects":self.objects()}))
    }

    fn dispatch(&mut self, change: Change) -> Value {
        let payload = match &change {
            Change::Open => Value::Null,
            Change::History(index) if self.history[*index] == self.history[0] => Value::Null,
            Change::History(index) => json!(&self.history[*index]),
            Change::Edit(objects, _) if objects == &self.history[0] => Value::Null,
            Change::Edit(objects, _) => json!(objects),
        };
        self.pending = Some(change);
        self.error = None;
        payload
    }

    fn point(&mut self, point: Pos2) -> Option<Change> {
        match &mut self.tool {
            Tool::Select => {
                self.selected = self.objects().iter().rposition(|object| {
                    let [x, y, right, bottom] = object.bounds;
                    Rect::from_min_max(pos2(x, y), pos2(right, bottom)).contains(point)
                });
                None
            }
            Tool::Insert(kind, corner) => {
                if *kind == "text" && self.input.trim().is_empty() {
                    self.error = Some("텍스트 상자의 내용을 입력하세요.".into());
                    return None;
                }
                let Some(first) = *corner else {
                    *corner = Some(point);
                    return None;
                };
                let rect = Rect::from_two_pos(first, point);
                if rect.width() < 0.02 || rect.height() < 0.02 {
                    self.error = Some("개체의 두 모서리를 더 넓게 지정하세요.".into());
                    return None;
                }
                let object = Object {
                    id: None,
                    kind: (*kind).into(),
                    bounds: [rect.left(), rect.top(), rect.right(), rect.bottom()],
                    text: if *kind == "text" {
                        self.input.clone()
                    } else {
                        String::new()
                    },
                };
                let mut objects = self.objects().to_vec();
                let index = objects.len();
                objects.push(object);
                Some(Change::Edit(objects, Some(index)))
            }
            Tool::Move => {
                let selected = self.selected?;
                let mut objects = self.objects().to_vec();
                let [left, top, right, bottom] = objects[selected].bounds;
                let width = right - left;
                let height = bottom - top;
                let x = (point.x - width / 2.0).clamp(0.0, 1.0 - width);
                let y = (point.y - height / 2.0).clamp(0.0, 1.0 - height);
                if (x - left).abs() < 0.00001 && (y - top).abs() < 0.00001 {
                    self.tool = Tool::Select;
                    return None;
                }
                objects[selected].bounds = [x, y, x + width, y + height];
                Some(Change::Edit(objects, Some(selected)))
            }
        }
    }

    fn remove(&self) -> Option<Change> {
        let mut objects = self.objects().to_vec();
        objects.remove(self.selected?);
        Some(Change::Edit(objects, None))
    }

    pub fn show(&mut self, ui: &mut Ui) -> Option<Value> {
        if self.preview.is_none() {
            if self.pending.is_some() {
                ui.spinner();
                ui.label("PDF 페이지를 불러오는 중…");
            }
            if let Some(error) = &self.error {
                ui.colored_label(ui.visuals().error_fg_color, error);
            }
            return None;
        }
        let mut change = None;
        let busy = self.pending.is_some();
        ui.add_enabled_ui(!busy, |ui| {
            ui.horizontal_wrapped(|ui| {
                if ui
                    .selectable_label(matches!(self.tool, Tool::Select), "선택")
                    .clicked()
                {
                    self.tool = Tool::Select;
                }
                ui.add_enabled_ui(self.objects().len() < 64, |ui| {
                    if ui.button("텍스트 상자").clicked() {
                        self.tool = Tool::Insert("text", None);
                    }
                    ui.menu_button("도형", |ui| {
                        for (kind, label) in &TYPES[1..] {
                            if ui.button(*label).clicked() {
                                self.tool = Tool::Insert(kind, None);
                                ui.close();
                            }
                        }
                    });
                });
                if ui
                    .add_enabled(self.selected.is_some(), Button::new("이동"))
                    .clicked()
                {
                    self.tool = Tool::Move;
                }
                if ui
                    .add_enabled(self.selected.is_some(), Button::new("삭제"))
                    .clicked()
                {
                    change = self.remove();
                }
                let can_undo = self.position > 0;
                let can_redo = self.position + 1 < self.history.len();
                if ui.add_enabled(can_undo, Button::new("실행 취소")).clicked() {
                    change = Some(Change::History(self.position - 1));
                }
                if ui.add_enabled(can_redo, Button::new("다시 실행")).clicked() {
                    change = Some(Change::History(self.position + 1));
                }
            });
            if matches!(self.tool, Tool::Insert("text", _)) {
                let label = ui.label("상자 내용");
                ui.add(
                    TextEdit::multiline(&mut self.input)
                        .desired_rows(2)
                        .char_limit(400),
                )
                .labelled_by(label.id);
            }
            ui.label(match &self.tool {
                Tool::Select => "페이지나 목록에서 GitDoc으로 삽입한 개체를 선택하세요.",
                Tool::Insert(_, None) => "페이지에서 첫 번째 모서리를 클릭하세요.",
                Tool::Insert(_, Some(_)) => "반대쪽 모서리를 클릭하세요.",
                Tool::Move => "개체의 새 중심을 클릭하세요.",
            });
            if let Some(texture) = &self.preview {
                let height = (ui.ctx().content_rect().height() * 0.45).clamp(120.0, 480.0);
                let response = ui.add(
                    Image::new(texture)
                        .max_size(vec2(ui.available_width(), height))
                        .sense(Sense::click()),
                );
                let paper = response.rect;
                let on_paper = |x, y| paper.min + paper.size() * vec2(x, y);
                let color = ui.visuals().selection.stroke.color;
                for (index, object) in self.objects().iter().enumerate() {
                    let [x, y, right, bottom] = object.bounds;
                    ui.painter().rect_stroke(
                        Rect::from_min_max(on_paper(x, y), on_paper(right, bottom)),
                        0.0,
                        Stroke::new(
                            if self.selected == Some(index) {
                                2.0
                            } else {
                                1.0_f32
                            },
                            color,
                        ),
                        StrokeKind::Inside,
                    );
                }
                if let Tool::Insert(_, Some(corner)) = self.tool {
                    ui.painter()
                        .circle_filled(on_paper(corner.x, corner.y), 3.0, color);
                }
                if response.clicked()
                    && let Some(pointer) = response.interact_pointer_pos()
                {
                    let pos = (pointer - paper.min) / paper.size();
                    response.request_focus();
                    if let Some(action) = self.point(pos2(pos.x, pos.y)) {
                        change = Some(action);
                    }
                }
                if response.has_focus() && change.is_none() {
                    let (delete, undo, redo) = ui.input_mut(|input| {
                        (
                            input.consume_key(Modifiers::NONE, Key::Delete),
                            input.consume_key(Modifiers::COMMAND, Key::Z),
                            input.consume_key(Modifiers::COMMAND | Modifiers::SHIFT, Key::Z)
                                || input.consume_key(Modifiers::COMMAND, Key::Y),
                        )
                    });
                    change = if delete {
                        self.remove()
                    } else if redo && self.position + 1 < self.history.len() {
                        Some(Change::History(self.position + 1))
                    } else if undo && self.position > 0 {
                        Some(Change::History(self.position - 1))
                    } else {
                        None
                    };
                }
            }
            let current = self.selected.map_or("개체 선택".into(), |index| {
                label(index, &self.objects()[index])
            });
            let mut selection = self.selected;
            ComboBox::from_id_salt("pdf_object_target")
                .selected_text(current)
                .show_ui(ui, |ui| {
                    for (index, object) in self.objects().iter().enumerate() {
                        ui.selectable_value(&mut selection, Some(index), label(index, object));
                    }
                });
            if selection != self.selected {
                self.selected = selection;
                self.tool = Tool::Select;
            }
        });
        if busy {
            ui.spinner();
        }
        if let Some(error) = &self.error {
            ui.colored_label(ui.visuals().error_fg_color, error);
        }
        change.map(|edited| self.dispatch(edited))
    }
}

fn label(index: usize, object: &Object) -> String {
    let kind = TYPES
        .iter()
        .find(|(kind, _)| *kind == object.kind)
        .map_or("개체", |(_, label)| label);
    let excerpt: String = object.text.chars().take(24).collect();
    format!("{} · {kind} {excerpt}", index + 1)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bitmap() -> PageImage {
        ColorImage::filled([260, 220], Color32::WHITE).into()
    }

    #[test]
    fn canvas_click_selects_one_duplicate_and_undo_restores_its_position() {
        let ctx = Context::default();
        let mut editor = PdfObjects::new();
        editor.load(
            &ctx,
            Ok(json!({"objects":[
                {"id":"left","kind":"rectangle","box":[0.1,0.2,0.4,0.4],"text":""},
                {"id":"right","kind":"rectangle","box":[0.5,0.2,0.8,0.4],"text":""}
            ]})),
            Some(bitmap()),
        );
        let color = ctx.style().visuals.selection.stroke.color;
        let frame = |editor: &mut PdfObjects, events| {
            ctx.run(
                RawInput {
                    events,
                    ..Default::default()
                },
                |ctx| {
                    CentralPanel::default().show(ctx, |ui| {
                        editor.show(ui);
                    });
                },
            )
        };
        let initial = frame(&mut editor, vec![]);
        let hit = initial
            .shapes
            .iter()
            .filter_map(|shape| match &shape.shape {
                epaint::Shape::Rect(rect)
                    if rect.stroke.color == color && rect.stroke.width == 1.0 =>
                {
                    Some(rect.rect.center())
                }
                _ => None,
            })
            .max_by(|a, b| a.x.total_cmp(&b.x))
            .expect("right object outline");
        let mouse = |pressed| Event::PointerButton {
            pos: hit,
            button: PointerButton::Primary,
            pressed,
            modifiers: Modifiers::NONE,
        };
        frame(&mut editor, vec![Event::PointerMoved(hit), mouse(true)]);
        frame(&mut editor, vec![mouse(false)]);
        assert_eq!(editor.selected, Some(1));

        editor.tool = Tool::Move;
        let motion = editor.point(pos2(0.8, 0.55)).unwrap();
        let move_payload = editor.dispatch(motion);
        assert!(editor.fields(1).is_none());
        assert_eq!(move_payload[0]["id"], "left");
        assert_eq!(move_payload[1]["id"], "right");
        editor.load(&ctx, Ok(json!({})), Some(bitmap()));
        assert!(editor.fields(1).is_some());

        let deleted = editor.remove().unwrap();
        let removal = editor.dispatch(deleted);
        assert_eq!(removal.as_array().unwrap().len(), 1);
        assert_eq!(removal[0]["id"], "left");
        editor.load(&ctx, Ok(json!({})), Some(bitmap()));
        assert_eq!(editor.dispatch(Change::History(1)), move_payload);
        editor.load(&ctx, Ok(json!({})), Some(bitmap()));
        assert_eq!(editor.fields(1).unwrap()["objects"], move_payload);
        assert_eq!(editor.dispatch(Change::History(0)), Value::Null);
        editor.load(&ctx, Ok(json!({})), Some(bitmap()));
        assert!(editor.fields(1).is_none());
        assert_eq!(editor.dispatch(Change::History(1)), move_payload);
    }
}
