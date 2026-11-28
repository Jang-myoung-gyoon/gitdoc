use super::{Editor, button};
use eframe::egui::*;
use serde_json::{Value, json};

#[derive(Clone, Copy, PartialEq, Default)]
pub(super) enum HangulTab {
    #[default]
    Edit,
    Format,
    Insert,
    Table,
}

impl Editor {
    pub(super) fn hangul_tools(&mut self, ui: &mut Ui) -> Option<(&'static str, Value)> {
        let mut action = None;
        if self.hangul_tab == HangulTab::Table && self.page_tables.is_empty() {
            self.hangul_tab = HangulTab::Edit;
        }
        ui.add_enabled_ui(!self.waiting, |ui| {
            ui.horizontal_wrapped(|ui| {
                for (value, label) in [
                    (HangulTab::Edit, "편집"),
                    (HangulTab::Format, "서식"),
                    (HangulTab::Insert, "입력"),
                ] {
                    ui.selectable_value(&mut self.hangul_tab, value, label);
                }
                if !self.page_tables.is_empty() {
                    ui.selectable_value(&mut self.hangul_tab, HangulTab::Table, "표 레이아웃");
                }
            });
            ui.separator();
            match self.hangul_tab {
                HangulTab::Edit => {
                    ui.horizontal_wrapped(|ui| {
                        ui.strong("클립보드");
                        for (label, shortcut, operation) in [
                            ("오려 두기", "Ctrl/⌘+X", "cut"),
                            ("복사하기", "Ctrl/⌘+C", "copy"),
                            ("붙이기", "Ctrl/⌘+V", "paste"),
                        ] {
                            button(ui, self.text.is_some(), label, shortcut, || {
                                action = Some((operation, Value::Null))
                            });
                        }
                        ui.separator();
                        self.hangul_history_tools(ui, &mut action);
                        ui.separator();
                        ui.strong("서식");
                        if ui.button("글자 · 문단 모양").clicked() {
                            self.hangul_tab = HangulTab::Format;
                        }
                    });
                    ui.horizontal_wrapped(|ui| {
                        ui.strong("입력");
                        self.insertion_tools(ui, &mut action);
                    });
                }
                HangulTab::Format => self.hangul_format_tools(ui, &mut action),
                HangulTab::Insert => {
                    ui.horizontal_wrapped(|ui| {
                        ui.strong("개체");
                        self.insertion_tools(ui, &mut action);
                    });
                }
                HangulTab::Table => {
                    ui.horizontal_wrapped(|ui| {
                        self.hangul_history_tools(ui, &mut action);
                        ui.separator();
                        self.table_tools(ui, &mut action);
                    });
                }
            }
            self.paged_shape_tools(ui, &mut action);
        });
        action
    }

    fn hangul_history_tools(&self, ui: &mut Ui, action: &mut Option<(&'static str, Value)>) {
        button(
            ui,
            self.undo || self.text_edit().is_some(),
            "↶ 실행 취소",
            "Ctrl/⌘+Z",
            || *action = Some(("undo_tool", Value::Null)),
        );
        button(
            ui,
            self.redo || self.text.is_some(),
            "↷ 다시 실행",
            "Ctrl/⌘+Shift+Z",
            || *action = Some(("redo_tool", Value::Null)),
        );
    }

    pub(super) fn can_format_hangul(&self) -> bool {
        self.text_edit().is_none()
            && self.selected.is_some_and(|target| {
                self.objects
                    .iter()
                    .any(|object| object.target == target && object.hangul_format)
            })
    }

    fn hangul_format_tools(&mut self, ui: &mut Ui, action: &mut Option<(&str, Value)>) {
        let enabled = self.can_format_hangul();
        let color_range = self
            .text
            .as_ref()
            .and_then(|edit| edit.rendered_range)
            .is_some_and(|range| !range.is_empty());
        ui.add_enabled_ui(enabled, |ui| {
            ui.horizontal_wrapped(|ui| {
                ui.strong("글자");
                if !self.hangul_fonts.is_empty() {
                    ui.menu_button("글꼴", |ui| {
                        for face in &self.hangul_fonts {
                            if ui.button(face).clicked() {
                                *action = Some(("font", json!(face)));
                                ui.close();
                            }
                        }
                    })
                    .response
                    .on_hover_text("문서에 등록된 공통 글꼴을 문단 전체에 적용합니다.");
                }
                ui.menu_button("글자 크기", |ui| {
                    for size in [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72] {
                        if ui.button(format!("{size} pt")).clicked() {
                            *action = Some(("size", json!(size)));
                            ui.close();
                        }
                    }
                });
                for (label, tooltip, operation) in [
                    ("B 진하게", "Ctrl/⌘+B", "bold"),
                    ("I 기울임", "Ctrl/⌘+I", "italic"),
                    ("U 밑줄", "Ctrl/⌘+U", "underline"),
                ] {
                    button(ui, true, label, tooltip, || {
                        *action = Some((operation, Value::Null))
                    });
                }
                ui.add_enabled_ui(color_range, |ui| {
                    ui.menu_button("글자 색", |ui| {
                        for (name, value) in [
                            ("검정", "000000"),
                            ("파랑", "1852A3"),
                            ("빨강", "B03040"),
                            ("초록", "24704D"),
                        ] {
                            if ui.button(name).clicked() {
                                *action = Some(("color", json!(value)));
                                ui.close();
                            }
                        }
                    })
                    .response
                    .on_disabled_hover_text("화면에서 위치를 확인할 수 있는 본문 글자를 먼저 선택하세요.");
                });
            });
            ui.horizontal_wrapped(|ui| {
                ui.strong("문단");
                for (label, value) in [
                    ("양쪽", "justify"),
                    ("왼쪽", "left"),
                    ("가운데", "center"),
                    ("오른쪽", "right"),
                ] {
                    button(ui, true, label, "문단 정렬", || {
                        *action = Some(("align", json!(value)))
                    });
                }
                ui.menu_button("줄 간격", |ui| {
                    for value in [100, 120, 140, 160, 180, 200, 250] {
                        if ui.button(format!("{value}%")).clicked() {
                            *action = Some(("spacing", json!(value)));
                            ui.close();
                        }
                    }
                });
            });
        });
        if !enabled {
            ui.weak("서식을 적용할 본문 문단을 선택하세요. 입력 중인 글자는 먼저 반영해야 합니다.");
        }
    }
}
