use crate::pages::{self, PageImage, Pages};
use eframe::egui;
use serde_json::{Value, json};
use std::{
    ffi::OsString,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Command, Stdio},
    sync::mpsc::{self, Receiver, Sender},
};

pub struct Message {
    pub op: &'static str,
    pub scope: u64,
    pub params: Value,
}

pub struct Reply {
    pub op: &'static str,
    pub scope: u64,
    pub result: Result<Value, String>,
    pub images: Vec<Option<PageImage>>,
    pub page: Option<usize>,
}

pub struct Bridge {
    pub send: Sender<Message>,
    pub read: Receiver<Reply>,
}

impl Bridge {
    pub fn start(root: PathBuf, executable: OsString, ui: egui::Context) -> Self {
        let (requests, input) = mpsc::channel::<Message>();
        let (output, replies) = mpsc::channel();
        std::thread::spawn(move || {
            let mut command = Command::new(executable);
            command
                .current_dir(root)
                .args(["-m", "desktop.bridge"])
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit());
            if std::env::var_os("DOCVERS_PDF_EDITOR").is_none()
                && let Ok(executable) = std::env::current_exe()
            {
                let mut editor = executable.with_file_name("gitdoc-pdf");
                editor.set_extension(std::env::consts::EXE_EXTENSION);
                if editor.is_file() {
                    command.env("DOCVERS_PDF_EDITOR", editor);
                }
            }
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                command.creation_flags(0x08000000);
            }
            let mut child = match command.spawn() {
                Ok(child) => child,
                Err(error) => {
                    let _ = output.send(Reply {
                        op: "startup",
                        scope: 0,
                        result: Err(error.to_string()),
                        images: vec![],
                        page: None,
                    });
                    ui.request_repaint();
                    return;
                }
            };
            let mut stdin = child.stdin.take().expect("piped stdin");
            let mut stdout = BufReader::new(child.stdout.take().expect("piped stdout"));
            let mut line = String::new();
            let mut renderer = None;
            for request in input {
                let mut call = |op: &str, params: &Value| {
                    writeln!(stdin, "{}", json!({"op":op,"params":params}))
                        .map_err(|error| error.to_string())?;
                    stdin.flush().map_err(|error| error.to_string())?;
                    line.clear();
                    if stdout
                        .read_line(&mut line)
                        .map_err(|error| error.to_string())?
                        == 0
                    {
                        return Err(
                            "문서 처리 엔진이 종료되었습니다. 설치 환경을 확인하세요.".into()
                        );
                    }
                    let mut value: Value =
                        serde_json::from_str(&line).map_err(|error| error.to_string())?;
                    if let Some(error) = value["error"].as_str() {
                        return Err(error.into());
                    }
                    value
                        .as_object_mut()
                        .and_then(|object| object.remove("result"))
                        .ok_or_else(|| "문서 처리 엔진 응답을 읽을 수 없습니다.".into())
                };
                let mut images = Vec::new();
                let result = match request.op {
                    "page" | "changes" => render(&request, &mut renderer, &mut images, call),
                    _ => call(request.op, &request.params),
                };
                if output
                    .send(Reply {
                        op: request.op,
                        scope: request.scope,
                        result,
                        images,
                        page: request.params["number"].as_u64().map(|n| n as usize),
                    })
                    .is_err()
                {
                    break;
                }
                ui.request_repaint();
            }
            drop(stdin);
            let _ = child.kill();
            let _ = child.wait();
        });
        Self {
            send: requests,
            read: replies,
        }
    }
}

fn render(
    request: &Message,
    renderer: &mut Option<Pages>,
    images: &mut Vec<Option<PageImage>>,
    mut call: impl FnMut(&str, &Value) -> Result<Value, String>,
) -> Result<Value, String> {
    let params = &request.params;
    let renderer = match renderer {
        Some(renderer) => renderer,
        None => renderer.insert(Pages::new(
            params["pdfium_directory"]
                .as_str()
                .ok_or("PDFium 경로를 읽을 수 없습니다.")?,
        )?),
    };
    let paths = params["paths"]
        .as_array()
        .ok_or("문서 미리보기 경로를 읽을 수 없습니다.")?;
    let physical_pages = params["pages"]
        .as_array()
        .ok_or("비교 페이지를 찾을 수 없습니다.")?;
    if request.op == "changes"
        && paths.first() == paths.get(1)
        && physical_pages.first() == physical_pages.get(1)
    {
        return Ok(json!({"count":0, "footer_only":false}));
    }
    let mut rendered = Vec::new();
    for (path, page) in paths.iter().zip(physical_pages) {
        rendered.push(path.as_str().zip(page.as_u64()).and_then(|(path, number)| {
            renderer.image(
                path,
                number as usize,
                params["substitute_fonts"] == true,
                || {
                    let result =
                        call("font_raster", &json!({"path":path, "number":number})).ok()?;
                    let rgba = image::open(result.as_str()?).ok()?.into_rgba8();
                    let size = [rgba.width() as usize, rgba.height() as usize];
                    Some(egui::ColorImage::from_rgba_unmultiplied(
                        size,
                        rgba.as_raw(),
                    ))
                },
            )
        }));
    }
    let failed = rendered
        .iter()
        .zip(physical_pages)
        .any(|(image, p)| image.is_none() && !p.is_null());
    let regions = match rendered.as_slice() {
        [before, after] if !failed && (request.op == "changes" || params["comparable"] == true) => {
            pages::regions(before.as_ref(), after.as_ref())
        }
        _ => vec![],
    };
    let footer_only = serde_json::from_value::<pages::Region>(params["footer"].clone())
        .is_ok_and(|footer| pages::only_footer(&regions, footer));
    if request.op == "changes" {
        return Ok(if failed {
            Value::Null
        } else {
            json!({"count": if footer_only { 0 } else { regions.len() }, "footer_only": footer_only})
        });
    }
    images.extend(rendered);
    let notes = if params["pdf_notes"] == true {
        call(
            "page_notes",
            &json!({
                "doc": params["doc"], "version": params["ids"][0], "number": physical_pages[0],
            }),
        )
        .unwrap_or_else(|_| json!([]))
    } else {
        json!([])
    };
    Ok(json!({"regions": regions, "notes": notes, "footer_only":footer_only}))
}
