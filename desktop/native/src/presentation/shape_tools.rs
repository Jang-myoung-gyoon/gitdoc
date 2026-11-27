use super::{Editor, Format, button};
use eframe::egui::*;
use serde_json::{Value, json};

#[derive(serde::Deserialize)]
pub(super) struct PageShape {
    pub(super) address: [u32; 2],
    pub(super) kind: String,
    pub(super) size: [u32; 2],
    pub(super) move_up: bool,
    pub(super) move_down: bool,
    pub(super) resize: bool,
}

impl PageShape {
    fn name(&self) -> &str {
        match self.kind.as_str() {
            "rectangle" => "사각형",
            "ellipse" => "타원",
            "triangle" => "삼각형",
            "arrow" => "화살표",
            _ => "다각형",
        }
    }

    fn location(&self, format: Format) -> String {
        if format == Format::Hangul {
            format!(
                "구역 {} · 문단 {}",
                self.address[0] + 1,
                self.address[1] + 1
            )
        } else {
            format!("본문 문단 {}", self.address[1] + 1)
        }
    }
}

impl Editor {
    pub(super) fn paged_shape_tools(&mut self, ui: &mut Ui, action: &mut Option<(&str, Value)>) {
        if self.page_shapes.is_empty() {
            return;
        }
        ui.horizontal_wrapped(|ui| {
            ui.strong(if self.format == Format::Word {
                "도형 서식"
            } else {
                "도형 도구"
            });
            ui.menu_button("도형 선택", |ui| {
                ScrollArea::vertical().max_height(240.0).show(ui, |ui| {
                    for (index, shape) in self.page_shapes.iter().enumerate() {
                        let label = format!(
                            "{} · {} · {}×{} mm",
                            shape.name(),
                            shape.location(self.format),
                            shape.size[0],
                            shape.size[1]
                        );
                        if ui
                            .selectable_label(self.selected_shape == Some(index), label)
                            .clicked()
                        {
                            self.selected_shape = Some(index);
                            self.selected_table = None;
                            self.shape_size = shape.size;
                            if self.text_edit().is_none() {
                                self.text = None;
                                self.selected = None;
                            }
                            ui.close();
                        }
                    }
                });
            })
            .response
            .on_hover_text("크기와 원본 문단 번호로 본문의 도형을 구분합니다.");

            let Some(shape) = self
                .selected_shape
                .and_then(|index| self.page_shapes.get(index))
            else {
                return;
            };
            let shape_value = json!({"address": shape.address, "digest": self.edit_digest});
            let ready = self.text_edit().is_none();
            ui.label(format!(
                "{} · {}",
                shape.name(),
                shape.location(self.format)
            ));
            let move_tools = |ui: &mut Ui, action: &mut Option<(&str, Value)>| {
                ui.separator();
                ui.label("배치");
                for (enabled, label, name, tooltip) in [
                    (
                        shape.move_up,
                        "↑ 문단 위로",
                        "shape_up",
                        "앞 문단과 도형의 순서를 바꿉니다.",
                    ),
                    (
                        shape.move_down,
                        "↓ 문단 아래로",
                        "shape_down",
                        "다음 문단과 도형의 순서를 바꿉니다.",
                    ),
                ] {
                    button(ui, ready && enabled, label, tooltip, || {
                        *action = Some((name, shape_value.clone()))
                    });
                }
            };
            let size = &mut self.shape_size;
            let mut size_tools = |ui: &mut Ui, action: &mut Option<(&str, Value)>| {
                ui.separator();
                ui.label("크기");
                ui.add_enabled_ui(ready && shape.resize, |ui| {
                    let width = ui.label("너비");
                    ui.add(DragValue::new(&mut size[0]).range(2..=180).suffix(" mm"))
                        .labelled_by(width.id)
                        .on_hover_text("도형 너비 · 2–180 mm");
                    let height = ui.label("높이");
                    ui.add(DragValue::new(&mut size[1]).range(2..=250).suffix(" mm"))
                        .labelled_by(height.id)
                        .on_hover_text("도형 높이 · 2–250 mm");
                    button(
                        ui,
                        *size != shape.size,
                        "크기 적용",
                        "너비와 높이를 선택한 도형에 적용합니다.",
                        || {
                            let mut payload = shape_value.clone();
                            payload["size"] = json!(size);
                            *action = Some(("shape_size", payload));
                        },
                    );
                });
            };
            if self.format == Format::Word {
                move_tools(ui, action);
                size_tools(ui, action);
            } else {
                size_tools(ui, action);
                move_tools(ui, action);
            }
            ui.separator();
            button(
                ui,
                ready,
                "도형 삭제",
                "선택한 도형을 삭제합니다. 저장 전 실행 취소할 수 있습니다.",
                || {
                    *action = Some(("delete_shape", shape_value));
                },
            );
        });
    }
}
