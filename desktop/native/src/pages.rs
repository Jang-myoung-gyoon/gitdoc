use eframe::egui::{Color32, ColorImage};
use pdfium_render::prelude::{
    PdfBitmap, PdfBitmapFormat, Pdfium, PdfiumError, PdfiumLibraryBindingsAccessor,
};
use std::{collections::VecDeque, path::PathBuf, sync::Arc};

pub type PageImage = Arc<ColorImage>;
pub type Region = [f32; 4];
type Bounds = [usize; 4];

pub struct Pages {
    pdfium: Pdfium,
    cache: VecDeque<((PathBuf, usize), PageImage)>,
}

pub fn load_pdfium(directory: &str) -> Result<Pdfium, String> {
    let library = Pdfium::pdfium_platform_library_name_at_path(&directory);
    match Pdfium::bind_to_library(library) {
        Ok(bindings) => Ok(Pdfium::new(bindings)),
        Err(PdfiumError::PdfiumLibraryBindingsAlreadyInitialized) => Ok(Pdfium::default()),
        Err(error) => Err(format!("PDFium을 불러올 수 없습니다. {error}")),
    }
}

impl Pages {
    pub fn new(directory: &str) -> Result<Self, String> {
        Ok(Self {
            pdfium: load_pdfium(directory)?,
            cache: VecDeque::new(),
        })
    }

    pub fn image(&mut self, path: Option<&str>, number: usize) -> Option<PageImage> {
        let key = (PathBuf::from(path?), number);
        if let Some(position) = self.cache.iter().position(|(cached, _)| *cached == key) {
            let item = self.cache.remove(position)?;
            let image = Arc::clone(&item.1);
            self.cache.push_front(item);
            return Some(image);
        }
        let document = self.pdfium.load_pdf_from_file(&key.0, None).ok()?;
        let page = document
            .pages()
            .get(i32::try_from(number.checked_sub(1)?).ok()?)
            .ok()?;
        let (width, height) = (
            f64::from(page.width().value),
            f64::from(page.height().value),
        );
        if !width.is_finite() || !height.is_finite() || width <= 0.0 || height <= 0.0 {
            return None;
        }
        let scale = (1350.0 / width.max(height)).min(4.0);
        let size = [
            (width * scale).ceil() as usize,
            (height * scale).ceil() as usize,
        ];
        let (width, height) = (size[0] as i32, size[1] as i32);
        let bitmap = PdfBitmap::empty(width, height, PdfBitmapFormat::BGR).ok()?;
        let bindings = self.pdfium.bindings();
        let bitmap_handle = bindings.get_handle_from_bitmap(&bitmap);
        let page_handle = bindings.get_handle_from_page(&page);
        // Match the old page renderer, which draws annotations but does not initialize forms.
        const FPDF_ANNOT: i32 = 0x01;
        // The page and bitmap own these handles and outlive both synchronous PDFium calls.
        unsafe {
            bindings.FPDFBitmap_FillRect(bitmap_handle, 0, 0, width, height, 0xffffffff);
            bindings.FPDF_RenderPageBitmap(
                bitmap_handle,
                page_handle,
                0,
                0,
                width,
                height,
                0,
                FPDF_ANNOT,
            );
        }
        let bytes = bitmap.as_rgba_bytes();
        if bytes.len() != size[0] * size[1] * 4 {
            return None;
        }
        let pixels = bytes
            .chunks_exact(4)
            .map(|pixel| Color32::from_rgb(pixel[0], pixel[1], pixel[2]))
            .collect();
        let image = Arc::new(ColorImage::new(size, pixels));
        self.cache.push_front((key, Arc::clone(&image)));
        self.cache.truncate(16);
        Some(image)
    }
}

pub fn regions(before: Option<&PageImage>, after: Option<&PageImage>) -> Vec<Region> {
    let full_page = vec![[0.0, 0.0, 1.0, 1.0]];
    let (before, after) = match (before, after) {
        (Some(before), Some(after)) => (before, after),
        (None, None) => return vec![],
        _ => return full_page,
    };
    if before.size != after.size {
        return full_page;
    }
    if Arc::ptr_eq(before, after) || before.pixels == after.pixels {
        return vec![];
    }

    const TILE: usize = 28;
    const THRESHOLD: u8 = 28;

    let [width, height] = before.size;
    let cols = width.div_ceil(TILE);
    let rows = height.div_ceil(TILE);
    let mut dirty = vec![false; cols * rows];
    for y in 0..rows {
        for x in 0..cols {
            let left = x * TILE;
            let right = (left + TILE).min(width);
            let top = y * TILE;
            let bottom = (top + TILE).min(height);
            dirty[y * cols + x] = (top..bottom).any(|row| {
                let span = row * width + left..row * width + right;
                before.pixels[span.clone()]
                    .iter()
                    .zip(&after.pixels[span])
                    .any(|(a, b)| {
                        a.r().abs_diff(b.r()) >= THRESHOLD
                            || a.g().abs_diff(b.g()) >= THRESHOLD
                            || a.b().abs_diff(b.b()) >= THRESHOLD
                    })
            });
        }
    }

    let mut result = Vec::new();
    let mut pending = Vec::new();
    for y in 0..rows {
        for x in 0..cols {
            if !std::mem::take(&mut dirty[y * cols + x]) {
                continue;
            }
            let (mut left, mut top, mut right, mut bottom) = (x, y, x, y);
            pending.push((x, y));
            while let Some((cx, cy)) = pending.pop() {
                left = left.min(cx);
                top = top.min(cy);
                right = right.max(cx);
                bottom = bottom.max(cy);
                for ny in cy.saturating_sub(1)..=(cy + 1).min(rows - 1) {
                    for nx in cx.saturating_sub(1)..=(cx + 1).min(cols - 1) {
                        if std::mem::take(&mut dirty[ny * cols + nx]) {
                            pending.push((nx, ny));
                        }
                    }
                }
            }
            result.push([
                left * TILE,
                top * TILE,
                ((right + 1) * TILE).min(width),
                ((bottom + 1) * TILE).min(height),
            ]);
        }
    }
    compact_regions(result, width, height)
        .into_iter()
        .map(|[left, top, right, bottom]| {
            [
                left as f32 / width as f32,
                top as f32 / height as f32,
                right as f32 / width as f32,
                bottom as f32 / height as f32,
            ]
        })
        .collect()
}

fn bounding_box(a: Bounds, b: Bounds) -> Bounds {
    [
        a[0].min(b[0]),
        a[1].min(b[1]),
        a[2].max(b[2]),
        a[3].max(b[3]),
    ]
}

fn area([left, top, right, bottom]: Bounds) -> usize {
    right.saturating_sub(left) * bottom.saturating_sub(top)
}

fn covered_area(regions: &[Bounds]) -> usize {
    let mut edges: Vec<_> = regions.iter().flat_map(|r| [r[0], r[2]]).collect();
    edges.sort_unstable();
    edges.dedup();
    edges
        .windows(2)
        .map(|strip| {
            let mut spans: Vec<_> = regions
                .iter()
                .filter(|r| r[0] < strip[1] && r[2] > strip[0])
                .map(|r| (r[1], r[3]))
                .collect();
            spans.sort_unstable();
            let mut end = 0;
            let height: usize = spans
                .into_iter()
                .map(|(top, bottom)| {
                    let length = bottom.saturating_sub(top.max(end));
                    end = end.max(bottom);
                    length
                })
                .sum();
            (strip[1] - strip[0]) * height
        })
        .sum()
}

fn compact_regions(mut regions: Vec<Bounds>, width: usize, height: usize) -> Vec<Bounds> {
    const MAX_REGIONS: usize = 7;
    const BINS: usize = 8;
    // Limit the pair search for pages with many isolated marks.
    if regions.len() > BINS * BINS {
        let mut bins = [None; BINS * BINS];
        for r in regions {
            let x = (r[0] + r[2]) * BINS / (width * 2);
            let y = (r[1] + r[3]) * BINS / (height * 2);
            let slot = &mut bins[y * BINS + x];
            *slot = Some(slot.map_or(r, |old| bounding_box(old, r)));
        }
        regions = bins.into_iter().flatten().collect();
    }
    while regions.len() > MAX_REGIONS {
        let mut best = (usize::MAX, 0, 1);
        for (i, &a) in regions.iter().enumerate() {
            for (j, &b) in regions.iter().enumerate().skip(i + 1) {
                let overlap = area([
                    a[0].max(b[0]),
                    a[1].max(b[1]),
                    a[2].min(b[2]),
                    a[3].min(b[3]),
                ]);
                let added = area(bounding_box(a, b)) - (area(a) + area(b) - overlap);
                best = best.min((added, i, j));
            }
        }
        let (_, i, j) = best;
        regions[i] = bounding_box(regions[i], regions[j]);
        regions.remove(j);
    }
    if covered_area(&regions) * 5 >= width * height * 4 {
        return vec![[0, 0, width, height]];
    }
    regions.sort_unstable_by_key(|r| (r[1], r[0], r[3], r[2]));
    regions
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn regions_keep_threshold_diagonal_connectivity_and_order() {
        let base = Arc::new(ColorImage::filled([84, 112], Color32::WHITE));
        let mut modified = (*base).clone();
        modified[(0, 0)] = Color32::from_rgb(227, 255, 255);
        modified[(28, 28)] = Color32::from_rgb(255, 255, 227);
        modified[(0, 90)] = Color32::from_rgb(255, 227, 255);
        modified[(82, 0)] = Color32::from_rgb(228, 255, 255);
        assert_eq!(
            regions(Some(&base), Some(&Arc::new(modified))),
            vec![[0.0, 0.0, 2.0 / 3.0, 0.5], [0.0, 0.75, 1.0 / 3.0, 1.0]]
        );
        assert!(regions(Some(&base), Some(&base)).is_empty());
        assert_eq!(regions(None, Some(&base)), vec![[0.0, 0.0, 1.0, 1.0]]);
    }

    #[test]
    fn merges_nearby_boxes_without_discarding_changes() {
        let mut boxes = vec![[4, 4, 8, 8], [10, 4, 14, 8]];
        boxes.extend((1..=6).map(|i| [20 * i, 40, 20 * i + 4, 44]));
        let compacted = compact_regions(boxes.clone(), 150, 100);
        assert_eq!(compacted.len(), 7);
        assert_eq!(compacted[0], [4, 4, 14, 8]);
        assert!(
            boxes
                .into_iter()
                .all(|b| compacted.iter().any(|&r| bounding_box(b, r) == r))
        );
    }

    #[test]
    fn counts_overlaps_once_and_marks_exactly_eighty_percent_as_a_full_page() {
        let boxes = vec![[0, 0, 60, 100], [40, 0, 79, 100]];
        assert_eq!(compact_regions(boxes.clone(), 100, 100), boxes);
        let boxes = vec![[0, 0, 60, 100], [40, 0, 80, 100]];
        assert_eq!(compact_regions(boxes, 100, 100), vec![[0, 0, 100, 100]]);
    }
}
