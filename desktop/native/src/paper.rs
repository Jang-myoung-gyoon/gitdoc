use std::ops::Range;

pub const HEADER: f32 = 30.0;
pub const GAP: f32 = 20.0;

pub struct PaperRow {
    pub top: f32,
    pub bottom: f32,
    pub heights: [Option<f32>; 2],
}

pub fn layout(ratios: &[Vec<f32>], pages: usize, width: f32) -> Vec<PaperRow> {
    let mut top = 0.0;
    (0..pages)
        .map(|index| {
            let heights =
                std::array::from_fn(|side| ratios.get(side)?.get(index).map(|ratio| ratio * width));
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stacks_mixed_paper_sizes_and_aligns_missing_comparison_pages() {
        let rows = layout(&[vec![1.4, 0.5], vec![0.5, 1.0, 2.0]], 3, 100.0);
        assert_eq!(rows[0].heights, [Some(140.0), Some(50.0)]);
        assert_eq!(rows[1].top, 190.0);
        assert_eq!(rows[1].bottom, 320.0);
        assert_eq!(rows[2].heights, [None, Some(200.0)]);
        assert_eq!(visible(&rows, 160.0, 300.0), 0..2);
        assert_eq!(visible(&rows, 170.0, 190.0), 1..1);
        assert_eq!(visible(&rows, 350.0, 500.0), 2..3);
    }
}
