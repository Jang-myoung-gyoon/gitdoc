use std::ops::Range;

pub const HEADER: f32 = 30.0;
pub const GAP: f32 = 20.0;

pub struct PaperRow {
    pub top: f32,
    pub bottom: f32,
    pub heights: [Option<f32>; 2],
}

pub fn layout(ratios: &[Vec<f32>], pages: &[Vec<Option<usize>>], width: f32) -> Vec<PaperRow> {
    let mut top = 0.0;
    pages
        .iter()
        .map(|pair| {
            let heights = std::array::from_fn(|side| {
                let page = (*pair.get(side)?)?;
                ratios
                    .get(side)?
                    .get(page.checked_sub(1)?)
                    .map(|ratio| ratio * width)
            });
            let height = heights
                .into_iter()
                .flatten()
                .reduce(f32::max)
                .unwrap_or(width);
            let bottom = top + HEADER + height;
            let row = PaperRow {
                top,
                bottom,
                heights,
            };
            top = bottom + GAP;
            row
        })
        .collect()
}

/// Return the zero-based rows overlapping the viewport.
pub fn visible(rows: &[PaperRow], top: f32, bottom: f32) -> Range<usize> {
    rows.partition_point(|row| row.bottom <= top)..rows.partition_point(|row| row.top < bottom)
}

/// Prioritize the selected page and visible rows before their immediate neighbors.
pub fn render_order(
    visible: Range<usize>,
    pages: usize,
    selected: usize,
) -> impl Iterator<Item = usize> + Clone {
    let neighbors = [
        (visible.start > 0).then_some(visible.start),
        (visible.end < pages).then_some(visible.end + 1),
    ];
    std::iter::once(selected)
        .chain(visible.map(|index| index + 1))
        .chain(neighbors.into_iter().flatten())
        .enumerate()
        .filter_map(move |(index, page)| (index == 0 || page != selected).then_some(page))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stacks_mixed_paper_sizes_and_aligns_missing_comparison_pages() {
        let pairs = vec![
            vec![Some(1), Some(1)],
            vec![Some(2), Some(2)],
            vec![None, Some(3)],
        ];
        let rows = layout(&[vec![1.4, 0.5], vec![0.5, 1.0, 2.0]], &pairs, 100.0);
        assert_eq!(rows[0].heights, [Some(140.0), Some(50.0)]);
        assert_eq!(rows[1].top, 190.0);
        assert_eq!(rows[1].bottom, 320.0);
        assert_eq!(rows[2].heights, [None, Some(200.0)]);
        assert_eq!(visible(&rows, 160.0, 300.0), 0..2);
        assert_eq!(visible(&rows, 170.0, 190.0), 1..1);
        assert_eq!(visible(&rows, 350.0, 500.0), 2..3);
    }

    #[test]
    fn renders_visible_rows_before_prefetching_one_on_each_side() {
        let order = |visible, selected| render_order(visible, 8, selected).collect::<Vec<_>>();
        assert_eq!(order(2..4, 3), [3, 4, 2, 5]);
        assert_eq!(order(2..4, 4), [4, 3, 2, 5]);
        assert_eq!(order(0..1, 1), [1, 2]);
        assert_eq!(order(7..8, 8), [8, 7]);
        assert_eq!(order(4..4, 4), [4, 5]);
    }
}
