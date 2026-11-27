use eframe::egui::{Color32, ColorImage};
use pdfium_render::prelude::{PdfBitmap, PdfBitmapFormat, Pdfium, PdfiumLibraryBindingsAccessor};
use std::{collections::VecDeque, path::PathBuf, sync::Arc};

pub type PageImage = Arc<ColorImage>;
pub type Region = [f32; 4];

pub struct Pages {
    pdfium: Pdfium,
    cache: VecDeque<((PathBuf, usize), PageImage)>,
}

impl Pages {
    pub fn new(directory: &str) -> Result<Self, String> {
        let library = Pdfium::pdfium_platform_library_name_at_path(&directory);
        let bindings = Pdfium::bind_to_library(library)
            .map_err(|error| format!("PDFium을 불러올 수 없습니다. {error}"))?;
        Ok(Self {
            pdfium: Pdfium::new(bindings),
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
    const REGION_LIMIT: usize = 160;

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
                (left * TILE) as f32 / width as f32,
                (top * TILE) as f32 / height as f32,
                ((right + 1) * TILE).min(width) as f32 / width as f32,
                ((bottom + 1) * TILE).min(height) as f32 / height as f32,
            ]);
            if result.len() == REGION_LIMIT {
                return result;
            }
        }
    }
    result
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
}
