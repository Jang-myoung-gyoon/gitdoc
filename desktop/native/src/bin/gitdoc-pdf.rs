use lopdf::{Dictionary, Document, Object, ObjectId, dictionary, text_string};
use serde_json::{Value, json};
use std::{collections::HashSet, env, error::Error, io, process::ExitCode};

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const INVALID: &str = "PDF 구조를 읽을 수 없습니다. 원본을 확인하세요.";

fn main() -> ExitCode {
    match edit() {
        Ok(receipt) => {
            println!("{receipt}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

fn edit() -> Result<Value> {
    let mut paths = env::args_os().skip(1);
    let input = paths.next().ok_or(INVALID)?;
    let output = paths.next().ok_or(INVALID)?;
    let request: Value = serde_json::from_reader(io::stdin().lock())?;
    let mut doc = Document::load(input).map_err(|_| INVALID)?;
    if encrypted(&doc) || signed(&doc) {
        return Err("암호가 걸렸거나 서명된 PDF입니다.".into());
    }

    let pages: Vec<_> = doc.page_iter().collect();
    let count = pages.len();
    let operation = request["operation"].as_str().unwrap_or_default();
    let index = request["page"]
        .as_u64()
        .and_then(|number| usize::try_from(number).ok())
        .and_then(|number| number.checked_sub(1))
        .filter(|&index| index < count || (operation == "insert" && index == count))
        .ok_or("PDF 페이지 번호를 확인하세요.")?;
    let number = index + 1;
    let mut inserted = 0;
    let mut deleted = None;
    match operation {
        "insert" => {
            let source = paths.next();
            inserted = insert_pages(&mut doc, &pages, index, source)?;
        }
        "left" | "right" => {
            let page = pages[index];
            let rotation = page_rotation(&doc, page)?;
            let delta = if operation == "right" { 90 } else { -90 };
            doc.get_dictionary_mut(page)?
                .set("Rotate", (rotation + delta).rem_euclid(360));
        }
        "delete" => {
            if count <= 1 {
                return Err("PDF에는 적어도 한 페이지가 있어야 합니다.".into());
            }
            let page = pages[index];
            let ancestors = ancestors(&doc, page)?;
            adjust_counts(&mut doc, &ancestors, -1)?;
            doc.delete_object(page).ok_or(INVALID)?;
            deleted = Some(number);
        }
        "note"
            if request["note"]
                .as_str()
                .is_some_and(|note| !note.trim().is_empty()) =>
        {
            add_note(&mut doc, pages[index], request["note"].as_str().unwrap())?;
        }
        _ => return Err("PDF 작업을 선택하거나 메모 내용을 입력하세요.".into()),
    }

    if doc.page_iter().count() != count + inserted - usize::from(deleted.is_some()) {
        return Err(INVALID.into());
    }
    doc.save(output)?;
    Ok(json!([
        count,
        (inserted > 0).then_some(number),
        inserted,
        deleted
    ]))
}

fn encrypted(doc: &Document) -> bool {
    doc.trailer.has(b"Encrypt") || doc.was_encrypted()
}

fn signed(doc: &Document) -> bool {
    doc.objects
        .values()
        .filter_map(|object| object.as_dict().ok())
        .any(|dict| {
            (dict.has(b"ByteRange") && dict.has(b"Contents"))
                || (dict
                    .get_deref(b"FT", doc)
                    .and_then(Object::as_name)
                    .is_ok_and(|name| name == b"Sig")
                    && dict
                        .get_deref(b"V", doc)
                        .is_ok_and(|value| !matches!(value, Object::Null)))
        })
}

fn ancestors(doc: &Document, page: ObjectId) -> Result<Vec<ObjectId>> {
    let mut ids = Vec::new();
    let mut seen = HashSet::from([page]);
    let mut dict = doc.get_dictionary(page)?;
    while let Ok(parent) = dict.get(b"Parent") {
        let (id, parent) = doc.dereference(parent)?;
        let id = id.ok_or(INVALID)?;
        if !seen.insert(id) {
            return Err(INVALID.into());
        }
        ids.push(id);
        dict = parent.as_dict()?;
    }
    Ok(ids)
}

fn inherited<'a>(doc: &'a Document, leaf: ObjectId, key: &[u8]) -> Result<Option<&'a Object>> {
    for id in std::iter::once(leaf).chain(ancestors(doc, leaf)?) {
        if let Ok(value) = doc.get_dictionary(id)?.get(key)
            && !matches!(doc.dereference(value)?.1, Object::Null)
        {
            return Ok(Some(value));
        }
    }
    Ok(None)
}

fn page_rotation(doc: &Document, page: ObjectId) -> Result<i64> {
    Ok(match inherited(doc, page, b"Rotate")? {
        Some(value) => doc.dereference(value)?.1.as_i64()?.rem_euclid(360),
        None => 0,
    })
}

fn page_box(doc: &Document, page: ObjectId, use_crop: bool) -> Result<[f32; 4]> {
    let crop = if use_crop {
        inherited(doc, page, b"CropBox")?
    } else {
        None
    };
    let object = match crop {
        Some(value) => value,
        None => inherited(doc, page, b"MediaBox")?.ok_or(INVALID)?,
    };
    let entries = doc.dereference(object)?.1.as_array()?;
    let [left, bottom, right, top] = entries.as_slice() else {
        return Err(INVALID.into());
    };
    let coord = |object| doc.dereference(object)?.1.as_float();
    Ok([coord(left)?, coord(bottom)?, coord(right)?, coord(top)?])
}

fn adjust_counts(doc: &mut Document, ancestors: &[ObjectId], delta: i64) -> Result<()> {
    for &id in ancestors {
        let count = doc.get_dictionary(id)?.get_deref(b"Count", doc)?.as_i64()?;
        doc.get_dictionary_mut(id)?
            .set("Count", count.checked_add(delta).ok_or(INVALID)?);
    }
    Ok(())
}

fn insert_pages(
    doc: &mut Document,
    pages: &[ObjectId],
    index: usize,
    source: Option<std::ffi::OsString>,
) -> Result<usize> {
    let at_end = index == pages.len();
    let reference = *pages.get(index).or_else(|| pages.last()).ok_or(INVALID)?;
    let ancestors = ancestors(doc, reference)?;
    let parent = *ancestors.first().ok_or(INVALID)?;
    let mut kids = doc
        .get_dictionary(parent)?
        .get_deref(b"Kids", doc)?
        .as_array()?
        .clone();
    let at = kids
        .iter()
        .position(|kid| doc.dereference(kid).ok().and_then(|(id, _)| id) == Some(reference))
        .ok_or(INVALID)?
        + usize::from(at_end);
    let new_pages = if let Some(source) = source {
        import_pages(doc, parent, Document::load(source).map_err(|_| INVALID)?)?
    } else {
        let [left, bottom, right, top] = page_box(doc, reference, false)?;
        let rotation = page_rotation(doc, reference)?;
        vec![doc.add_object(dictionary! {
            "Type" => "Page",
            "Parent" => parent,
            "MediaBox" => vec![0.into(), 0.into(), (right - left).abs().into(), (top - bottom).abs().into()],
            "CropBox" => vec![0.into(), 0.into(), (right - left).abs().into(), (top - bottom).abs().into()],
            "Rotate" => rotation,
            "Resources" => Dictionary::new(),
        })]
    };
    let count = new_pages.len();
    kids.splice(at..at, new_pages.into_iter().map(Object::Reference));
    doc.get_dictionary_mut(parent)?.set("Kids", kids);
    adjust_counts(doc, &ancestors, i64::try_from(count)?)?;
    Ok(count)
}

fn import_pages(
    doc: &mut Document,
    parent: ObjectId,
    mut source: Document,
) -> Result<Vec<ObjectId>> {
    if encrypted(&source) || source.page_iter().next().is_none() {
        return Err("암호가 없고 페이지가 있는 PDF를 선택하세요.".into());
    }
    let start = doc.max_id.checked_add(1).ok_or(INVALID)?;
    if u32::try_from(source.objects.len())? >= u32::MAX - start {
        return Err(INVALID.into());
    }
    source.renumber_objects_with(start);
    let pages: Vec<_> = source.page_iter().collect();
    for &page in &pages {
        let media = inherited(&source, page, b"MediaBox")?
            .ok_or(INVALID)?
            .clone();
        let crop = inherited(&source, page, b"CropBox")?
            .cloned()
            .unwrap_or_else(|| media.clone());
        let resources = inherited(&source, page, b"Resources")?
            .cloned()
            .unwrap_or_else(|| Dictionary::new().into());
        let rotation = page_rotation(&source, page)?;
        let page = source.get_dictionary_mut(page)?;
        page.set("MediaBox", media);
        page.set("CropBox", crop);
        page.set("Resources", resources);
        page.set("Rotate", rotation);
        page.set("Parent", parent);
    }
    // Start the dependency walk at the imported pages, so unrelated source
    // objects such as the catalog and page tree do not enter the destination.
    source.trailer =
        dictionary! { "Pages" => pages.iter().copied().map(Object::Reference).collect::<Vec<_>>() };
    source.prune_objects();
    doc.max_id = source.max_id;
    if source.version > doc.version {
        doc.version = source.version;
    }
    doc.objects.append(&mut source.objects);
    Ok(pages)
}

fn add_note(doc: &mut Document, page: ObjectId, note: &str) -> Result<()> {
    let [x1, y1, x2, y2] = page_box(doc, page, true)?;
    let left = x1.min(x2);
    let top = y1.max(y2);
    let width = (x2 - x1).abs().min(26.0);
    let height = (y2 - y1).abs().min(26.0);
    let mut annotations = match doc.get_dictionary(page)?.get_deref(b"Annots", doc) {
        Ok(Object::Null) | Err(lopdf::Error::DictKey(_)) => Vec::new(),
        Ok(value) => value.as_array()?.clone(),
        Err(error) => return Err(error.into()),
    };
    let annotation = doc.add_object(dictionary! {
        "Type" => "Annot",
        "Subtype" => "Text",
        "Rect" => vec![left.into(), (top - height).into(), (left + width).into(), top.into()],
        "Contents" => text_string(note),
        "P" => page,
        "F" => 4,
    });
    annotations.push(annotation.into());
    doc.get_dictionary_mut(page)?.set("Annots", annotations);
    Ok(())
}
