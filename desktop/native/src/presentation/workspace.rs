use super::{Command, Editor, Format, button};
use eframe::egui::*;
use serde_json::{Value, json};

type Action = (&'static str, Value);

impl Format {
    fn navigator(self) -> &'static str {
        match self {
            Self::Slides => "슬라이드",
            Self::Word => "페이지",
            Self::Hangul => "본문 문단",
        }
    }

    fn accent(self) -> Color32 {
        match self {
            Self::Slides => Color32::from_rgb(35, 104, 192),
            Self::Word => Color32::from_rgb(43, 78, 144),
            Self::Hangul => Color32::from_rgb(23, 113, 101),
        }
    }

    fn surface(self) -> Color32 {
        match self {
            Self::Slides => Color32::from_rgb(228, 234, 242),
            Self::Word => Color32::from_rgb(232, 236, 242),
            Self::Hangul => Color32::from_rgb(240, 237, 230),
        }
    }
}

impl Editor {
    pub(super) fn workspace(
        &mut self,
        ui: &mut Ui,
        title: &str,
        version: &str,
    ) -> (Option<Action>, Option<Command>) {
        let accent = self.format.accent();
        let line = ui.visuals().widgets.noninteractive.bg_stroke;
        Frame::new()
            .fill(Color32::from_rgb(250, 251, 253))
            .inner_margin(Margin::symmetric(12, 8))
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                        if ui
                            .add_enabled(!self.waiting, Button::new("편집 종료"))
                            .clicked()
                        {
                            self.closing = true;
                        }
                        if ui
                            .add_enabled(
                                !self.waiting && self.changed(),
                                Button::new(RichText::new("수정안 저장").color(Color32::WHITE))
                                    .fill(accent),
                            )
                            .clicked()
                        {
                            self.saving = true;
                            self.focus_reason = true;
                        }
                        ui.allocate_ui_with_layout(
                            vec2(ui.available_width(), 28.0),
                            Layout::left_to_right(Align::Center),
                            |ui| {
                                let available = (ui.available_width() - 54.0).max(0.0);
                                ui.add_sized(
                                    [available.min(300.0), 26.0],
                                    Label::new(RichText::new(title).size(19.0).strong())
                                        .truncate()
                                        .halign(Align::Min),
                                )
                                .on_hover_text(title);
                                ui.label(RichText::new(&self.extension).color(accent).strong());
                                if ui.available_width() > 65.0 {
                                    ui.add(Label::new(RichText::new(version).weak()).truncate())
                                        .on_hover_text(version);
                                }
                            },
                        );
                    });
                });
            });

        let mut action = Frame::new()
            .fill(Color32::from_rgb(247, 249, 252))
            .stroke(line)
            .inner_margin(Margin::symmetric(9, 6))
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.spacing_mut().item_spacing = vec2(5.0, 5.0);
                ui.spacing_mut().button_padding = vec2(5.0, 3.0);
                self.tool(ui)
            })
            .inner;

        if let Some(error) = &self.error {
            ui.colored_label(ui.visuals().error_fg_color, error);
        }
        ui.add_space(4.0);
        let spacing = ui.spacing().item_spacing.y;
        let body_height = (ui.available_height() - 46.0 - spacing).max(80.0);
        let body_width = ui.available_width();
        let mut command = None;
        ui.allocate_ui_with_layout(
            vec2(body_width, body_height),
            Layout::left_to_right(Align::Min),
            |ui| {
                ui.spacing_mut().item_spacing.x = 0.0;
                let rail = self.show_navigator && body_width >= 430.0;
                if rail {
                    let rail_width: f32 = match self.format {
                        Format::Slides => 128.0,
                        Format::Word => 104.0,
                        Format::Hangul => 168.0,
                    };
                    let rail_width = rail_width.min(body_width * 0.28);
                    let side = ui.allocate_ui_with_layout(
                        vec2(rail_width, body_height),
                        Layout::top_down(Align::Min),
                        |ui| {
                            ui.set_width(rail_width);
                            ui.set_height(body_height);
                            self.navigator(ui, rail_width, body_height)
                        },
                    );
                    if side.inner.is_some() {
                        action = side.inner;
                    }
                }

                ui.allocate_ui_with_layout(
                    vec2(ui.available_width(), body_height),
                    Layout::top_down(Align::Min),
                    |ui| {
                        let background = ui.max_rect();
                        ui.set_clip_rect(ui.clip_rect().intersect(background));
                        ui.painter()
                            .rect_filled(background, 0.0, self.format.surface());
                        ui.painter()
                            .rect_stroke(background, 0.0, line, StrokeKind::Inside);
                        ScrollArea::both()
                            .id_salt(("editor-paper", self.page))
                            .scroll_source(
                                scroll_area::ScrollSource::MOUSE_WHEEL
                                    | scroll_area::ScrollSource::SCROLL_BAR,
                            )
                            .auto_shrink([false; 2])
                            .show(ui, |ui| {
                                ui.add_space(12.0);
                                command = self.canvas(ui);
                                ui.add_space(12.0);
                            });
                    },
                );
            },
        );

        let footer = Frame::new()
            .fill(Color32::from_rgb(250, 251, 253))
            .stroke(line)
            .inner_margin(Margin::symmetric(9, 6))
            .show(ui, |ui| self.footer(ui));
        if footer.inner.is_some() {
            action = footer.inner;
        }
        (action, command)
    }

    fn footer(&mut self, ui: &mut Ui) -> Option<Action> {
        let mut action = None;
        ui.set_width(ui.available_width());
        ui.horizontal_wrapped(|ui| {
            let ready = !self.waiting && self.count > 0;
            button(ui, ready && self.page > 1, "‹ 이전", "Page Up", || {
                action = Some(("page", json!(self.page - 1)));
            });
            let mut position = self.page;
            if ui
                .add_enabled(
                    ready,
                    DragValue::new(&mut position).range(1..=self.count.max(1)),
                )
                .on_hover_text("페이지 번호")
                .changed()
                && position != self.page
            {
                action = Some(("page", json!(position)));
            }
            ui.label(format!("/ {}", self.count.max(1)));
            button(
                ui,
                ready && self.page < self.count,
                "다음 ›",
                "Page Down",
                || {
                    action = Some(("page", json!(self.page + 1)));
                },
            );
            ui.separator();
            if ui.button("−").on_hover_text("축소").clicked() {
                self.zoom = (self.zoom - 10.0).max(50.0);
            }
            ui.scope(|ui| {
                ui.spacing_mut().slider_width = 78.0;
                ui.add(
                    Slider::new(&mut self.zoom, 50.0..=200.0)
                        .show_value(false)
                        .text("확대"),
                );
            });
            if ui.button("+").on_hover_text("확대").clicked() {
                self.zoom = (self.zoom + 10.0).min(200.0);
            }
            if ui
                .button(format!("{:.0}%", self.zoom))
                .on_hover_text("100%로 되돌리기")
                .clicked()
            {
                self.zoom = 100.0;
            }
            if ui.available_width() > 70.0 {
                ui.separator();
            }
            ui.toggle_value(&mut self.show_navigator, self.format.navigator())
                .on_hover_text("탐색 영역 표시");
            if self.waiting {
                ui.spinner();
            }
        });
        action
    }

    fn navigator(&mut self, ui: &mut Ui, width: f32, height: f32) -> Option<Action> {
        let mut action = None;
        let accent = self.format.accent();
        ui.visuals_mut().selection.bg_fill = accent.gamma_multiply(0.15);
        ui.visuals_mut().selection.stroke = Stroke::new(1.0_f32, accent);
        let rect = ui.max_rect();
        ui.painter()
            .rect_filled(rect, 0.0, Color32::from_rgb(247, 249, 251));
        ui.painter().line_segment(
            [rect.right_top(), rect.right_bottom()],
            Stroke::new(1.0_f32, Color32::from_gray(214)),
        );
        ui.add_space(8.0);
        ui.horizontal(|ui| {
            ui.add_space(8.0);
            ui.label(
                RichText::new(self.format.navigator())
                    .strong()
                    .color(accent),
            );
            if self.format == Format::Slides
                && ui
                    .add_enabled(!self.waiting && self.count > 0, Button::new("+"))
                    .on_hover_text("새 슬라이드")
                    .clicked()
            {
                action = Some(("new_slide", Value::Null));
            }
            if self.format != Format::Hangul {
                ui.add_enabled_ui(!self.waiting && !self.objects.is_empty(), |ui| {
                    ui.menu_button("⋯", |ui| {
                        ScrollArea::vertical().max_height(220.0).show(ui, |ui| {
                            for item in self
                                .objects
                                .iter()
                                .filter(|item| item.target.cell.is_none())
                            {
                                if ui
                                    .selectable_label(
                                        self.selected == Some(item.target),
                                        &item.name,
                                    )
                                    .clicked()
                                {
                                    action = Some(("select", item.target.json()));
                                    ui.close();
                                }
                            }
                        });
                    })
                    .response
                    .on_hover_text(if self.format == Format::Slides {
                        "개체 선택"
                    } else {
                        "문단 선택"
                    });
                });
            }
        });
        ui.separator();
        let inner_width = (width - 15.0).max(20.0);
        ScrollArea::vertical()
            .id_salt(("editor-navigator", &self.extension))
            .max_height((height - 44.0).max(40.0))
            .auto_shrink([false; 2])
            .show(ui, |ui| {
                if self.format == Format::Hangul {
                    let mut blocks = self
                        .objects
                        .iter()
                        .filter(|item| item.text.is_some())
                        .peekable();
                    if blocks.peek().is_none() && !self.waiting {
                        ui.label(RichText::new("편집할 수 있는 문단이 없습니다.").weak());
                    }
                    for (i, item) in blocks.enumerate() {
                        let label = format!("{}. {}", i + 1, item.name.trim());
                        let row = Button::selectable(self.selected == Some(item.target), &label)
                            .truncate();
                        if ui
                            .add_enabled_ui(!self.waiting, |ui| {
                                ui.add_sized([inner_width, 27.0], row)
                            })
                            .inner
                            .on_hover_text(&label)
                            .clicked()
                        {
                            action = Some(("select", item.target.json()));
                        }
                    }
                } else {
                    // Keep the current rendered sheet as the thumbnail. Other pages
                    // stay immediately reachable without requesting extra renders.
                    for page in 1..=self.count {
                        let current = page == self.page;
                        let row = Button::selectable(current, page.to_string());
                        if ui
                            .add_enabled_ui(!self.waiting, |ui| {
                                ui.add_sized([inner_width, 23.0], row)
                            })
                            .inner
                            .clicked()
                            && !current
                        {
                            action = Some(("page", json!(page)));
                        }
                        if current && let Some(image) = &self.image {
                            ui.vertical_centered(|ui| {
                                let size = vec2(inner_width - 8.0, 92.0);
                                ui.add(
                                    Image::new(image).max_size(size).maintain_aspect_ratio(true),
                                );
                            });
                        }
                    }
                }
            });
        action
    }

    pub(super) fn ruler(&self, ui: &Ui, paper: Rect) {
        if self.format != Format::Word {
            return;
        }
        let per_cm = paper.width() * 72.0 / (2.54 * self.width);
        let rect = Rect::from_min_max(
            paper.left_top() - vec2(0.0, 21.0),
            paper.right_top() - vec2(0.0, 4.0),
        );
        let painter = ui.painter().with_clip_rect(rect);
        painter.rect_filled(rect, 0.0, Color32::from_rgb(248, 249, 251));
        let gray = Color32::from_gray(129);
        for unit in 0..=((paper.width() / per_cm * 4.0).floor() as usize).min(500) {
            let x = rect.left() + unit as f32 * per_cm / 4.0;
            let long = unit % 4 == 0;
            painter.line_segment(
                [
                    pos2(x, rect.bottom()),
                    pos2(x, rect.bottom() - if long { 7.0 } else { 4.0 }),
                ],
                Stroke::new(1.0_f32, gray),
            );
            if long && unit > 0 {
                painter.text(
                    pos2(x, rect.top() + 1.0),
                    Align2::CENTER_TOP,
                    (unit / 4).to_string(),
                    FontId::proportional(8.0),
                    gray,
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::presentation::{Object, PageTable, Target};

    fn find_text(shapes: &[epaint::ClippedShape], label: &str) -> Pos2 {
        shapes
            .iter()
            .find_map(|shape| match &shape.shape {
                Shape::Text(text) if text.galley.job.text == label => {
                    Some(text.pos + text.galley.size() / 2.0)
                }
                _ => None,
            })
            .unwrap_or_else(|| panic!("Missing editor control: {label}"))
    }

    fn fixture(format: Format, ctx: &Context) -> Editor {
        let mut editor = Editor::new(1, Id::new("test-editor"), format, "SAMPLE".into());
        editor.waiting = false;
        editor.count = 3;
        editor.width = 612.0;
        editor.height = 792.0;
        editor.image = Some(ctx.load_texture(
            "test-page",
            ColorImage::filled([16, 16], Color32::WHITE),
            TextureOptions::LINEAR,
        ));
        editor.objects.push(
            Object::read(&json!({"id": 1, "box": [0.2, 0.3, 0.8, 0.4],
                                "text": "Body", "name": "Body"}))
            .unwrap(),
        );
        editor
    }

    fn frame(
        ctx: &Context,
        editor: &mut Editor,
        size: Vec2,
        events: Vec<Event>,
    ) -> (FullOutput, Option<Command>) {
        let mut command = None;
        let output = ctx.run(
            RawInput {
                screen_rect: Some(Rect::from_min_size(Pos2::ZERO, size)),
                events,
                ..Default::default()
            },
            |ctx| {
                CentralPanel::default().show(ctx, |ui| {
                    command = editor.show(ui, "Sample", "main · v1.0.0");
                });
            },
        );
        (output, command)
    }

    fn click(ctx: &Context, editor: &mut Editor, size: Vec2, pos: Pos2) -> Option<Command> {
        let event = |pressed| Event::PointerButton {
            pos,
            button: PointerButton::Primary,
            pressed,
            modifiers: Modifiers::NONE,
        };
        frame(
            ctx,
            editor,
            size,
            vec![Event::PointerMoved(pos), event(true)],
        );
        frame(ctx, editor, size, vec![event(false)]).1
    }

    #[test]
    fn slide_and_page_navigation_have_a_fixed_footer_and_send_page_commands() {
        for (format, size) in [
            (Format::Slides, vec2(1080.0, 820.0)),
            (Format::Word, vec2(560.0, 530.0)),
        ] {
            let ctx = Context::default();
            let mut editor = fixture(format, &ctx);
            frame(&ctx, &mut editor, size, vec![]);
            let (ui, _) = frame(&ctx, &mut editor, size, vec![]);
            let rail = find_text(&ui.shapes, format.navigator());
            let next = find_text(&ui.shapes, "다음 ›");
            assert!(next.y > size.y - 60.0 && rail.y < next.y);
            let page_two = find_text(&ui.shapes, "2");
            let command = click(&ctx, &mut editor, size, page_two);
            assert!(matches!(command, Some(Command::Page(params)) if params["number"] == 2));
        }
    }

    #[test]
    fn all_canvas_formats_send_table_and_shape_from_the_toolbar() {
        for (format, extension) in [
            (Format::Slides, "PPTX"),
            (Format::Word, "DOC"),
            (Format::Word, "DOCX"),
            (Format::Hangul, "HWP"),
            (Format::Hangul, "HWPX"),
        ] {
            let ctx = Context::default();
            let size = vec2(1080.0, 820.0);
            let mut editor = fixture(format, &ctx);
            editor.extension = extension.into();
            editor.table = [3, 4];
            frame(&ctx, &mut editor, size, vec![]);
            let (ui, _) = frame(&ctx, &mut editor, size, vec![]);
            find_text(&ui.shapes, extension);
            let table = find_text(
                &ui.shapes,
                if format == Format::Slides {
                    "표"
                } else {
                    "▦ 표"
                },
            );
            assert!(click(&ctx, &mut editor, size, table).is_none());
            let (menu, _) = frame(&ctx, &mut editor, size, vec![]);
            let insert = find_text(&menu.shapes, "표 삽입");
            assert!(matches!(click(&ctx, &mut editor, size, insert),
                             Some(Command::Page(params)) if params["operation"] == "table"
                                 && params["value"] == json!([3, 4])));
            editor.waiting = false;
            let (ui, _) = frame(&ctx, &mut editor, size, vec![]);
            let shape = find_text(
                &ui.shapes,
                if format == Format::Slides {
                    "도형"
                } else {
                    "◇ 도형"
                },
            );
            assert!(click(&ctx, &mut editor, size, shape).is_none());
            let (menu, _) = frame(&ctx, &mut editor, size, vec![]);
            let arrow = find_text(&menu.shapes, "화살표");
            assert!(matches!(click(&ctx, &mut editor, size, arrow),
                             Some(Command::Page(params)) if params["operation"] == "shape"
                                 && params["value"] == "arrow"));
        }
    }

    #[test]
    fn shape_toolbar_selects_the_second_duplicate_and_respects_the_contextual_order() {
        for (format, size, title, second_shape) in [
            (
                Format::Word,
                vec2(1080.0, 820.0),
                "도형 서식",
                "사각형 · 본문 문단 4 · 32×18 mm",
            ),
            (
                Format::Hangul,
                vec2(560.0, 720.0),
                "도형 도구",
                "사각형 · 구역 1 · 문단 4 · 32×18 mm",
            ),
        ] {
            let ctx = Context::default();
            let mut editor = fixture(format, &ctx);
            editor.edit_digest = "snapshot".into();
            editor.dirty = true;
            editor.undo = true;
            editor.page_shapes = serde_json::from_value(json!([
                {"address": [0, 2], "kind": "rectangle", "size": [32, 18],
                 "move_up": false, "move_down": true, "resize": true},
                {"address": [0, 3], "kind": "rectangle", "size": [32, 18],
                 "move_up": true, "move_down": false, "resize": true}
            ]))
            .unwrap();
            frame(&ctx, &mut editor, size, vec![]);
            let (initial, _) = frame(&ctx, &mut editor, size, vec![]);
            find_text(&initial.shapes, title);
            let select = find_text(&initial.shapes, "도형 선택");
            assert!(click(&ctx, &mut editor, size, select).is_none());
            let (menu, _) = frame(&ctx, &mut editor, size, vec![]);
            let second = find_text(&menu.shapes, second_shape);
            assert!(click(&ctx, &mut editor, size, second).is_none());

            let (selected, _) = frame(&ctx, &mut editor, size, vec![]);
            let arrange = find_text(&selected.shapes, "배치");
            let dimensions = find_text(&selected.shapes, "크기");
            let reading_order =
                |a: Pos2, b: Pos2| a.y + 10.0 < b.y || ((a.y - b.y).abs() <= 10.0 && a.x < b.x);
            if format == Format::Word {
                assert!(reading_order(arrange, dimensions));
            } else {
                assert!(reading_order(dimensions, arrange));
            }
            let down = find_text(&selected.shapes, "↓ 문단 아래로");
            assert!(click(&ctx, &mut editor, size, down).is_none());
            let up = find_text(&selected.shapes, "↑ 문단 위로");
            assert!(matches!(click(&ctx, &mut editor, size, up),
                             Some(Command::Page(params)) if params["operation"] == "shape_up"
                                 && params["value"] == json!({"address": [0, 3], "digest": "snapshot"})));
            editor.waiting = false;
            editor.shape_size = [46, 22];
            let (selected, _) = frame(&ctx, &mut editor, size, vec![]);
            let apply = find_text(&selected.shapes, "크기 적용");
            assert!(matches!(click(&ctx, &mut editor, size, apply),
                             Some(Command::Page(params)) if params["operation"] == "shape_size"
                                 && params["value"]["size"] == json!([46, 22])
                                 && params["value"]["address"] == json!([0, 3])));
            editor.waiting = false;
            let (selected, _) = frame(&ctx, &mut editor, size, vec![]);
            let delete = find_text(&selected.shapes, "도형 삭제");
            assert!(matches!(click(&ctx, &mut editor, size, delete),
                             Some(Command::Page(params)) if params["operation"] == "delete_shape"
                                 && params["value"]["address"] == json!([0, 3])));
            editor.waiting = false;
            let undo = Event::Key {
                key: Key::Z,
                physical_key: None,
                pressed: true,
                repeat: false,
                modifiers: Modifiers::COMMAND,
            };
            assert!(matches!(frame(&ctx, &mut editor, size, vec![undo]).1,
                             Some(Command::Page(params)) if params["operation"] == "undo"));
        }
    }

    #[test]
    fn hangul_formatting_tab_routes_a_narrow_toolbar_click_to_the_selected_paragraph() {
        let ctx = Context::default();
        let size = vec2(560.0, 650.0);
        let mut editor = fixture(Format::Hangul, &ctx);
        editor.objects[0].hangul_format = true;
        editor.selected = Some(editor.objects[0].target);
        frame(&ctx, &mut editor, size, vec![]);
        let (ui, _) = frame(&ctx, &mut editor, size, vec![]);
        let tabs = find_text(&ui.shapes, "서식");
        assert!(click(&ctx, &mut editor, size, tabs).is_none());
        let (ui, _) = frame(&ctx, &mut editor, size, vec![]);
        let bold = find_text(&ui.shapes, "B 진하게");
        assert!(matches!(click(&ctx, &mut editor, size, bold),
            Some(Command::Page(params)) if params["operation"] == "bold"
                && params["target"]["id"] == 1));
    }

    #[test]
    fn table_toolbar_selects_and_deletes_one_table() {
        for (format, menu_name, address) in [
            (Format::Hangul, "기존 표", vec![0, 3, 2]),
            (Format::Word, "문서 표", vec![1]),
        ] {
            let ctx = Context::default();
            let size = vec2(1080.0, 820.0);
            let mut editor = fixture(format, &ctx);
            editor.edit_digest = "snapshot".into();
            editor.page_tables = vec![
                PageTable {
                    address: vec![0],
                    bounds: None,
                    label: String::new(),
                    rows: 2,
                    columns: 3,
                    cells: vec![],
                },
                PageTable {
                    address: address.clone(),
                    bounds: Some([0.1, 0.4, 0.8, 0.5]),
                    label: String::new(),
                    rows: 4,
                    columns: 2,
                    cells: vec![],
                },
            ];
            frame(&ctx, &mut editor, size, vec![]);
            let (ui, _) = frame(&ctx, &mut editor, size, vec![]);
            find_text(&ui.shapes, "표 레이아웃");
            let tables = find_text(&ui.shapes, menu_name);
            assert!(click(&ctx, &mut editor, size, tables).is_none());
            let (menu, _) = frame(&ctx, &mut editor, size, vec![]);
            let selected = find_text(&menu.shapes, "표 2 · 4행 × 2열");
            assert!(click(&ctx, &mut editor, size, selected).is_none());
            let (ui, _) = frame(&ctx, &mut editor, size, vec![]);
            let delete = find_text(&ui.shapes, "표 전체 삭제");
            assert!(matches!(click(&ctx, &mut editor, size, delete),
                             Some(Command::Page(params)) if params["operation"] == "delete_table"
                                 && params["value"] == json!({"address": address, "digest": "snapshot"})));
        }
    }

    #[test]
    fn hangul_cell_edit_uses_exact_row_and_column_from_the_narrow_ribbon() {
        let ctx = Context::default();
        let size = vec2(560.0, 650.0);
        let mut editor = fixture(Format::Hangul, &ctx);
        editor.edit_digest = "snapshot".into();
        editor.page_tables = serde_json::from_value(json!([{
            "address": [0, 2, 1], "box": [0.1, 0.44, 0.8, 0.54],
            "rows": 1, "columns": 2, "cells": [
                {"coordinate": [0, 0], "text": "Same", "box": [0.1, 0.44, 0.45, 0.54]},
                {"coordinate": [0, 1], "text": "Same", "box": [0.45, 0.44, 0.8, 0.54]}
            ]
        }]))
        .unwrap();

        frame(&ctx, &mut editor, size, vec![]);
        let (output, _) = frame(&ctx, &mut editor, size, vec![]);
        assert!(
            click(
                &ctx,
                &mut editor,
                size,
                find_text(&output.shapes, "기존 표")
            )
            .is_none()
        );
        let (output, _) = frame(&ctx, &mut editor, size, vec![]);
        assert!(
            click(
                &ctx,
                &mut editor,
                size,
                find_text(&output.shapes, "표 1 · 1행 × 2열")
            )
            .is_none()
        );
        let (output, _) = frame(&ctx, &mut editor, size, vec![]);
        assert!(
            click(
                &ctx,
                &mut editor,
                size,
                find_text(&output.shapes, "셀 선택")
            )
            .is_none()
        );
        let (output, _) = frame(&ctx, &mut editor, size, vec![]);
        assert!(
            click(
                &ctx,
                &mut editor,
                size,
                find_text(&output.shapes, "1행 2열")
            )
            .is_none()
        );
        let (output, _) = frame(&ctx, &mut editor, size, vec![]);
        assert!(
            click(
                &ctx,
                &mut editor,
                size,
                find_text(&output.shapes, "셀 내용 편집")
            )
            .is_none()
        );
        assert!(editor.cell_draft.is_some());
        let (output, _) = frame(&ctx, &mut editor, size, vec![]);
        assert!(click(&ctx, &mut editor, size, find_text(&output.shapes, "취소")).is_none());
        assert!(editor.cell_draft.is_none());

        let (output, _) = frame(&ctx, &mut editor, size, vec![]);
        assert!(
            click(
                &ctx,
                &mut editor,
                size,
                find_text(&output.shapes, "셀 내용 편집")
            )
            .is_none()
        );
        frame(&ctx, &mut editor, size, vec![]);
        let select_all = Event::Key {
            key: Key::A,
            physical_key: None,
            pressed: true,
            repeat: false,
            modifiers: Modifiers::COMMAND,
        };
        let (output, _) = frame(
            &ctx,
            &mut editor,
            size,
            vec![select_all, Event::Text("새 값".into())],
        );
        assert!(
            matches!(click(&ctx, &mut editor, size, find_text(&output.shapes, "셀에 적용")),
            Some(Command::Page(params)) if params["operation"] == "edit_table_cell"
                && params["value"] == json!({
                    "address": [0, 2, 1], "coordinate": [0, 1], "text": "새 값", "digest": "snapshot"
                }))
        );

        editor.select_table_at(
            Rect::from_min_size(Pos2::ZERO, vec2(100.0, 100.0)),
            Some(pos2(25.0, 50.0)),
        );
        assert_eq!(editor.selected_cell, Some(0));
    }

    #[test]
    fn hangul_paragraph_navigation_reselects_without_losing_unsaved_text() {
        let ctx = Context::default();
        let size = vec2(940.0, 720.0);
        let mut editor = fixture(Format::Hangul, &ctx);
        frame(&ctx, &mut editor, size, vec![]);
        let (ui, _) = frame(&ctx, &mut editor, size, vec![]);
        assert!(
            !ui.shapes.iter().any(
                |shape| matches!(&shape.shape, Shape::Text(text) if text.galley.job.text == "B")
            )
        );
        let location = find_text(&ui.shapes, "1. Body");
        assert!(click(&ctx, &mut editor, size, location).is_none());
        assert!(matches!(editor.selected, Some(Target { id: 1, .. })));
        frame(&ctx, &mut editor, size, vec![Event::Text(" text".into())]);
        let edited = editor.text.as_ref().unwrap().value.clone();
        assert!(edited.contains(" text"));
        assert!(click(&ctx, &mut editor, size, location).is_none());
        assert_eq!(editor.text.as_ref().unwrap().value, edited);
    }
}
