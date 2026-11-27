use eframe::egui::*;

pub type Lines = Vec<Vec<(String, [f32; 4])>>;
type GlyphIndex = (usize, usize);

pub struct Selection {
    page: usize,
    side: usize,
    anchor: GlyphIndex,
    end: GlyphIndex,
}

impl Selection {
    fn limits(&self) -> (GlyphIndex, GlyphIndex) {
        (self.anchor.min(self.end), self.anchor.max(self.end))
    }

    fn value(&self, lines: &Lines) -> String {
        let ((first, start), (last, end)) = self.limits();
        lines[first..=last]
            .iter()
            .enumerate()
            .map(|(offset, line)| {
                let row = first + offset;
                let begin = if row == first { start } else { 0 };
                let finish = if row == last { end + 1 } else { line.len() };
                line[begin..finish]
                    .iter()
                    .map(|(glyph, _)| glyph.as_str())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }
}

fn page_box(image: Rect, [left, top, right, bottom]: [f32; 4]) -> Rect {
    Rect::from_min_max(
        image.min + vec2(left * image.width(), top * image.height()),
        image.min + vec2(right * image.width(), bottom * image.height()),
    )
}

fn nearest(image: Rect, lines: &Lines, point: Pos2) -> Option<GlyphIndex> {
    let distance =
        |rect: Rect| rect.distance_sq_to_pos(point) + 0.001 * rect.center().distance_sq(point);
    lines
        .iter()
        .enumerate()
        .flat_map(|(row, line)| {
            line.iter()
                .enumerate()
                .map(move |(column, item)| ((row, column), item.1))
        })
        .min_by(|(_, a), (_, b)| {
            distance(page_box(image, *a)).total_cmp(&distance(page_box(image, *b)))
        })
        .map(|(index, _)| index)
}

pub fn interact(
    ui: &mut Ui,
    image: Rect,
    page: usize,
    side: usize,
    lines: &Lines,
    selection: &mut Option<Selection>,
) {
    if lines.is_empty() {
        return;
    }
    let response = ui
        .interact(
            image,
            ui.id().with(("page-text", page, side)),
            Sense::click_and_drag(),
        )
        .on_hover_cursor(CursorIcon::Text);
    if response.drag_started_by(PointerButton::Primary)
        && let Some(point) = ui.input(|input| input.pointer.press_origin())
        && let Some(anchor) = nearest(image, lines, point)
    {
        *selection = Some(Selection {
            page,
            side,
            anchor,
            end: anchor,
        });
        response.request_focus();
    }
    if response.clicked_by(PointerButton::Primary) {
        *selection = None;
    }
    let Some(selected) = selection
        .as_mut()
        .filter(|value| value.page == page && value.side == side)
    else {
        return;
    };
    if [selected.anchor, selected.end]
        .iter()
        .any(|&(row, column)| lines.get(row).and_then(|line| line.get(column)).is_none())
    {
        *selection = None;
        return;
    }
    if (response.dragged_by(PointerButton::Primary)
        || response.drag_stopped_by(PointerButton::Primary))
        && let Some(point) = response
            .interact_pointer_pos()
            .or_else(|| ui.input(|input| input.pointer.latest_pos()))
        && let Some(end) = nearest(image, lines, point)
    {
        selected.end = end;
    }
    let (first, last) = selected.limits();
    for (row, line) in lines.iter().enumerate().take(last.0 + 1).skip(first.0) {
        let begin = if row == first.0 { first.1 } else { 0 };
        let end = if row == last.0 {
            last.1 + 1
        } else {
            line.len()
        };
        let highlight = line[begin..end]
            .iter()
            .map(|(_, bounds)| page_box(image, *bounds))
            .reduce(Rect::union);
        if let Some(rect) = highlight {
            ui.painter()
                .rect_filled(rect, 0.0, Color32::from_rgba_unmultiplied(42, 117, 231, 92));
        }
    }
    if response.has_focus()
        && ui.input(|input| {
            input
                .events
                .iter()
                .any(|event| matches!(event, Event::Copy))
        })
    {
        ui.ctx().copy_text(selected.value(lines));
    }
    response.context_menu(|ui| {
        if ui.button("선택한 텍스트 복사").clicked() {
            ui.ctx().copy_text(selected.value(lines));
            ui.close();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backward_selection_copies_in_document_order_with_line_breaks() {
        let lines = [vec!["한", "글"], vec!["A", "B"]]
            .into_iter()
            .map(|line| line.into_iter().map(|c| (c.into(), [0.0; 4])).collect())
            .collect();
        let selection = Selection {
            page: 1,
            side: 0,
            anchor: (1, 0),
            end: (0, 1),
        };
        assert_eq!(selection.value(&lines), "글\nA");
    }

    #[test]
    fn dragging_and_copying_emits_text_to_the_platform_clipboard() {
        let context = Context::default();
        let screen = Rect::from_min_size(Pos2::ZERO, vec2(200.0, 200.0));
        let image = Rect::from_min_size(pos2(15.0, 15.0), vec2(100.0, 100.0));
        let lines = vec![vec![
            ("한".into(), [0.1, 0.1, 0.2, 0.2]),
            ("글".into(), [0.2, 0.1, 0.3, 0.2]),
        ]];
        let mut selection = None;
        let mut frame = |events| {
            context.run(
                RawInput {
                    screen_rect: Some(screen),
                    events,
                    ..Default::default()
                },
                |ctx| {
                    CentralPanel::default()
                        .show(ctx, |ui| interact(ui, image, 1, 0, &lines, &mut selection));
                },
            )
        };
        let button = |pos, pressed| Event::PointerButton {
            pos,
            button: PointerButton::Primary,
            pressed,
            modifiers: Modifiers::NONE,
        };
        let start = pos2(30.0, 30.0);
        let end = pos2(40.0, 30.0);
        frame(vec![]);
        frame(vec![Event::PointerMoved(start), button(start, true)]);
        frame(vec![Event::PointerMoved(end)]);
        frame(vec![button(end, false)]);
        let output = frame(vec![Event::Copy]);
        assert!(
            output
                .platform_output
                .commands
                .contains(&OutputCommand::CopyText("한글".into()))
        );
    }
}
