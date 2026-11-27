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
                    });
                    ui.request_repaint();
                    return;
                }
            };
            let mut stdin = child.stdin.take().expect("piped stdin");
            let mut stdout = BufReader::new(child.stdout.take().expect("piped stdout"));
            let mut line = String::new();
            for request in input {
                let result = (|| {
                    writeln!(
                        stdin,
                        "{}",
                        json!({"op":request.op,"params":request.params})
                    )
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
                })();
                if output
                    .send(Reply {
                        op: request.op,
                        scope: request.scope,
                        result,
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
