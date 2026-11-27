use eframe::egui::{
    Color32, Context, FontData, FontDefinitions, FontFamily, FontId, Galley, Id, Ui, text,
};
use serde_json::Value;
use std::{collections::BTreeMap, fs, sync::Arc};

const BASE: &str = "gitdoc-base-font-definitions";
const FONT_LIMIT: usize = 80 * 1024 * 1024;

#[derive(Clone, Debug, serde::Deserialize)]
pub struct Span(pub usize, pub String, pub f32, pub Option<[u8; 3]>);

struct Face {
    data: Arc<FontData>,
    family: FontFamily,
}

#[derive(Default)]
pub struct Fonts {
    resources: BTreeMap<String, Face>,
    names: BTreeMap<String, (String, String)>,
}

pub fn remember_base(ctx: &Context, definitions: &FontDefinitions) {
    ctx.data_mut(|data| data.insert_temp(Id::new(BASE), definitions.clone()));
}

fn base(ctx: &Context) -> Option<FontDefinitions> {
    ctx.data(|data| data.get_temp(Id::new(BASE)))
}

impl Fonts {
    pub fn update(&mut self, ctx: &Context, value: &Value) {
        let Some(mut definitions) = base(ctx) else {
            return;
        };
        let fallback = definitions
            .families
            .get(&FontFamily::Proportional)
            .cloned()
            .unwrap_or_default();
        let mut previous = std::mem::take(&mut self.resources);
        let mut names = BTreeMap::new();
        let mut used = 0;
        for (name, source) in value.as_object().into_iter().flatten() {
            let Some(path) = source["path"].as_str() else {
                continue;
            };
            let index = source["index"].as_u64().unwrap_or(0) as u32;
            let key = format!("gitdoc-document-face:{path}#{index}");
            if !self.resources.contains_key(&key) {
                let face = if let Some(face) = previous.remove(&key) {
                    face
                } else {
                    let Some(bytes) = source["size"].as_u64() else {
                        continue;
                    };
                    if bytes as usize > FONT_LIMIT - used {
                        continue;
                    };
                    let Ok(bytes) = fs::read(path) else { continue };
                    if bytes.len() > FONT_LIMIT - used
                        || ttf_parser::Face::parse(&bytes, index).is_err()
                    {
                        continue;
                    }
                    let mut data = FontData::from_owned(bytes);
                    data.index = index;
                    Face {
                        data: Arc::new(data),
                        family: FontFamily::Name(key.clone().into()),
                    }
                };
                if face.data.font.len() > FONT_LIMIT - used {
                    continue;
                }
                used += face.data.font.len();
                definitions
                    .font_data
                    .insert(key.clone(), Arc::clone(&face.data));
                definitions.families.insert(
                    face.family.clone(),
                    std::iter::once(key.clone())
                        .chain(fallback.iter().cloned())
                        .collect(),
                );
                self.resources.insert(key.clone(), face);
            }
            let label = source["label"].as_str().unwrap_or(name).to_owned();
            names.insert(name.clone(), (key, label));
        }
        if self.names != names {
            ctx.set_fonts(definitions);
        }
        self.names = names;
    }

    pub fn reset(&mut self, ctx: &Context) {
        if !self.resources.is_empty()
            && let Some(definitions) = base(ctx)
        {
            ctx.set_fonts(definitions);
        }
        self.resources.clear();
        self.names.clear();
    }

    pub fn label<'a>(&'a self, name: &'a str) -> &'a str {
        self.names.get(name).map_or(name, |(_, label)| label)
    }

    pub fn layout(
        &self,
        ui: &Ui,
        input: &str,
        reference: &str,
        spans: &[Span],
        scale: f32,
        fallback: FontId,
        color: Color32,
        width: f32,
        justify: bool,
    ) -> Arc<Galley> {
        let mut job = text::LayoutJob::default();
        job.wrap.max_width = width;
        job.justify = justify;
        if spans.is_empty() {
            job.append(input, 0.0, text::TextFormat::simple(fallback, color));
        } else {
            let families = ui.fonts(|fonts| fonts.families());
            let styles: Vec<_> = spans
                .iter()
                .map(|Span(_, name, size, ink)| {
                    let family = self
                        .names
                        .get(name)
                        .and_then(|(key, _)| self.resources.get(key))
                        .map(|face| face.family.clone())
                        .filter(|family| families.contains(family))
                        .unwrap_or_else(|| fallback.family.clone());
                    let size = if size.is_finite() && *size > 0.0 {
                        (size * scale).clamp(4.0, 160.0)
                    } else {
                        fallback.size
                    };
                    let color = if color == Color32::TRANSPARENT {
                        color
                    } else {
                        ink.map_or(color, |[r, g, b]| Color32::from_rgb(r, g, b))
                    };
                    text::TextFormat::simple(FontId::new(size, family), color)
                })
                .collect();

            let (prefix, old_suffix, new_suffix) = change_bounds(reference, input);
            let style_at = |i| {
                let index = if i < prefix {
                    i
                } else if i >= new_suffix {
                    old_suffix + i - new_suffix
                } else if old_suffix > prefix {
                    prefix
                } else {
                    prefix.saturating_sub(1)
                };
                spans
                    .partition_point(|span| span.0 <= index)
                    .saturating_sub(1)
            };
            let mut first = 0;
            let mut style = style_at(0);
            for (index, (byte, _)) in input.char_indices().enumerate().skip(1) {
                let next = style_at(index);
                if next != style {
                    job.append(&input[first..byte], 0.0, styles[style].clone());
                    first = byte;
                    style = next;
                }
            }
            job.append(&input[first..], 0.0, styles[style].clone());
        }
        ui.fonts_mut(|fonts| fonts.layout_job(job))
    }
}

pub(crate) fn change_bounds(before: &str, after: &str) -> (usize, usize, usize) {
    let prefix = before
        .chars()
        .zip(after.chars())
        .take_while(|(a, b)| a == b)
        .count();
    let old = before.chars().count();
    let new = after.chars().count();
    let suffix = before
        .chars()
        .rev()
        .zip(after.chars().rev())
        .take((old.min(new)).saturating_sub(prefix))
        .take_while(|(a, b)| a == b)
        .count();
    (prefix, old - suffix, new - suffix)
}

#[cfg(test)]
mod tests {
    use super::change_bounds;

    #[test]
    fn insertion_and_replacement_keep_unicode_suffix_indexes() {
        assert_eq!(change_bounds("원본 Arial", "원본 새😀 Arial"), (3, 3, 6));
        assert_eq!(change_bounds("Bold normal", "새 normal"), (0, 4, 1));
        assert_eq!(change_bounds("한글", "한글"), (2, 2, 2));
    }
}
