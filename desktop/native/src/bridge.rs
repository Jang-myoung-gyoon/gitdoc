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
    if request.op == "page" {
        let number = params["number"].as_u64().unwrap_or(0) as usize;
        images.extend(
            paths
                .iter()
                .map(|path| renderer.image(path.as_str(), number)),
        );
        let regions = match images.as_slice() {
            [before, after] if params["comparable"] == true => {
                pages::regions(before.as_ref(), after.as_ref())
            }
            _ => vec![],
        };
        let notes = if params["pdf_notes"] == true {
            call(
                "page_notes",
                &json!({
                    "doc": params["doc"], "version": params["ids"][0], "number": number,
                }),
            )
            .unwrap_or_else(|_| json!([]))
        } else {
            json!([])
        };
        return Ok(json!({"regions": regions, "notes": notes}));
    }

    let (Some(left), Some(right)) = (
        paths.first().and_then(Value::as_str),
        paths.get(1).and_then(Value::as_str),
    ) else {
        return Ok(json!([]));
    };
    let left_count = params["counts"][0].as_u64().unwrap_or(0) as usize;
    let right_count = params["counts"][1].as_u64().unwrap_or(0) as usize;
    let mut found = Vec::new();
    if left != right && left_count > 0 && right_count > 0 {
        for number in 1..=left_count.max(right_count) {
            let before = renderer.image(Some(left), number);
            let after = renderer.image(Some(right), number);
            if (number > left_count || before.is_some())
                && (number > right_count || after.is_some())
            {
                found.extend(
                    (0..pages::regions(before.as_ref(), after.as_ref()).len())
                        .map(|index| (number, index)),
                );
            }
        }
    }
    Ok(json!(found))
}
