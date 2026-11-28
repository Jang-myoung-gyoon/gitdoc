use eframe::egui;
use std::{
    ffi::OsString,
    path::Path,
    sync::mpsc::{self, Receiver, Sender},
    time::{Duration, Instant},
};

#[derive(Debug, PartialEq)]
enum State {
    Idle,
    Available {
        version: String,
        notes: String,
        digest: String,
        can_install: bool,
    },
    Current,
    Installed,
    Error(String),
    Installing,
}

enum Request {
    Info,
    Manual,
    Install { version: String, digest: String },
}

pub struct Updates {
    worker: Option<Sender<Request>>,
    replies: Receiver<State>,
    state: State,
    next_check: Instant,
    busy: bool,
    interactive: bool,
    windows: bool,
}

impl Updates {
    pub fn new(root: &Path, python: &OsString, ctx: &egui::Context) -> Self {
        let (replies, receiver) = mpsc::channel();
        let (request, work) = mpsc::channel();
        let connected = if cfg!(target_os = "macos")
            && std::env::var("DOCVERS_SPARKLE_PIPE").as_deref() == Ok("1")
        {
            mac_worker(ctx.clone(), work, replies);
            true
        } else if cfg!(windows) && root.join("PACKAGED").is_file() {
            windows_worker(root, python, ctx.clone(), work, replies);
            true
        } else {
            false
        };
        let restart = if cfg!(windows) {
            match std::env::var("GITDOC_UPDATE_RESTART_STATUS").as_deref() {
                Ok("installed") => Some(State::Installed),
                Ok("failed") => Some(State::Error(
                    "업데이트가 중단되어 기존 설치본으로 다시 시작했습니다.".into(),
                )),
                _ => None,
            }
        } else {
            None
        };
        let show_restart = restart.is_some();
        Self {
            worker: connected.then_some(request),
            replies: receiver,
            state: restart.unwrap_or(State::Idle),
            next_check: Instant::now() + Duration::from_secs(4),
            busy: false,
            interactive: show_restart,
            windows: cfg!(windows),
        }
    }

    pub fn tick(&mut self, ctx: &egui::Context, idle: bool) {
        while let Ok(reply) = self.replies.try_recv() {
            if reply == State::Installing {
                // The supervisor waits for this process to exit before touching installed files.
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                self.state = reply;
                return;
            }
            if self.interactive
                && (self.windows || matches!(reply, State::Error(_)))
                && matches!(
                    reply,
                    State::Current | State::Available { .. } | State::Error(_)
                )
            {
                self.state = reply;
                self.busy = false;
            } else {
                self.state = reply;
                self.busy = false;
                self.interactive = false;
            }
        }
        if idle
            && !self.busy
            && !self.interactive
            && self.worker.is_some()
            && Instant::now() >= self.next_check
        {
            self.next_check = Instant::now() + Duration::from_secs(24 * 3600);
            self.start(Request::Info, false);
        }
        if !self.busy && self.worker.is_some() {
            ctx.request_repaint_after(
                self.next_check
                    .saturating_duration_since(Instant::now())
                    .max(Duration::from_millis(250)),
            );
        }
    }

    fn start(&mut self, request: Request, interactive: bool) {
        if self
            .worker
            .as_ref()
            .is_some_and(|worker| worker.send(request).is_ok())
        {
            self.busy = true;
            self.interactive = interactive;
        } else {
            self.worker = None;
            self.busy = false;
            self.interactive = false;
            self.state = State::Error("업데이트 도우미에 연결할 수 없습니다.".into());
        }
    }

    pub fn control(&mut self, ui: &mut egui::Ui, idle: bool) {
        let label = if matches!(self.state, State::Available { .. }) {
            "새 업데이트"
        } else {
            "업데이트 확인…"
        };
        if ui.add_enabled(idle && !self.busy && self.worker.is_some(), egui::Button::new(label))
            .on_disabled_hover_text(if self.worker.is_none() {
                "업데이트를 지원하는 설치본을 실행하세요. macOS 설치 이미지는 응용 프로그램으로 복사한 뒤 실행하세요."
            } else {
                "문서 편집과 진행 중인 작업을 마친 뒤 확인할 수 있습니다."
            }).clicked() {
            self.start(Request::Manual, true);
            ui.close();
        }
        if let State::Error(message) = &self.state {
            ui.label(message);
        }
    }

    pub fn dialog(&mut self, ctx: &egui::Context) -> bool {
        if !self.interactive {
            return false;
        }
        let mut close = false;
        let mut install = None;
        let modal = egui::Modal::new(egui::Id::new("gitdoc-updates")).show(ctx, |ui| {
            ui.set_width(390.0);
            ui.heading("GitDoc 업데이트");
            if self.busy {
                ui.spinner();
                ui.label(if self.windows {
                    "업데이트를 확인하거나 검증하고 있습니다."
                } else {
                    "macOS 업데이트 창에서 확인하세요."
                });
                return;
            }
            match &self.state {
                State::Available {
                    version,
                    notes,
                    digest,
                    can_install,
                } => {
                    ui.label(format!("GitDoc {version}"));
                    egui::ScrollArea::vertical()
                        .max_height(200.0)
                        .show(ui, |ui| {
                            ui.label(if notes.is_empty() {
                                "변경 사항은 릴리스 페이지에서 확인할 수 있습니다."
                            } else {
                                notes
                            });
                        });
                    if !can_install {
                        ui.label("이 설치본에는 Windows 업데이트 서명자가 설정되어 있지 않습니다.");
                    }
                    if ui
                        .add_enabled(*can_install, egui::Button::new("다운로드 및 설치"))
                        .clicked()
                    {
                        install = Some(Request::Install {
                            version: version.clone(),
                            digest: digest.clone(),
                        });
                    }
                }
                State::Installed => {
                    ui.label("업데이트를 완료했습니다.");
                }
                State::Current => {
                    ui.label("최신 버전을 사용하고 있습니다.");
                }
                State::Error(message) => {
                    ui.label(message);
                }
                _ => {}
            }
            close = ui.button("닫기").clicked();
        });
        if let Some(request) = install {
            self.start(request, true);
        } else if !self.busy && (close || modal.should_close()) {
            self.interactive = false;
        }
        true
    }
}

fn mac_worker(ctx: egui::Context, work: Receiver<Request>, replies: Sender<State>) {
    use std::io::{BufRead, Write};
    std::thread::spawn(move || {
        let stdin = std::io::stdin();
        let mut response = stdin.lock().lines();
        for request in work {
            let command = match request {
                Request::Info => "info",
                Request::Manual => "check",
                Request::Install { .. } => continue,
            };
            let sent = {
                let mut output = std::io::stdout().lock();
                writeln!(output, "{command}")
                    .and_then(|_| output.flush())
                    .is_ok()
            };
            let state = if sent {
                match response.next().and_then(Result::ok).as_deref() {
                    Some("idle") => State::Current,
                    Some("other-windows") => {
                        State::Error("다른 GitDoc 창을 닫은 뒤 업데이트를 확인하세요.".into())
                    }
                    Some(line) if line.starts_with("available ") => State::Available {
                        version: line["available ".len()..].to_owned(),
                        notes: String::new(),
                        digest: String::new(),
                        can_install: false,
                    },
                    _ => State::Error(
                        "업데이트 정보를 확인할 수 없습니다. 잠시 후 다시 시도하세요.".into(),
                    ),
                }
            } else {
                State::Error("업데이트 도우미에 연결할 수 없습니다.".into())
            };
            if replies.send(state).is_err() {
                break;
            }
            ctx.request_repaint();
        }
    });
}

fn windows_worker(
    root: &Path,
    python: &OsString,
    ctx: egui::Context,
    work: Receiver<Request>,
    replies: Sender<State>,
) {
    use std::process::Command;
    let root = root.to_owned();
    let python = python.clone();
    std::thread::spawn(move || {
        for request in work {
            let mut command = Command::new(&python);
            command
                .current_dir(&root)
                .args(["-E", "-s", "-B", "-m", "desktop.update_windows"]);
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                command.creation_flags(0x08000000);
            }
            match request {
                Request::Info | Request::Manual => {
                    command.arg("check");
                }
                Request::Install { version, digest } => {
                    command.args([
                        "install",
                        &version,
                        &digest,
                        &std::process::id().to_string(),
                    ]);
                }
            }
            let state = command
                .output()
                .ok()
                .filter(|result| result.status.success())
                .and_then(|result| serde_json::from_slice(&result.stdout).ok())
                .map(decode_windows)
                .unwrap_or_else(|| State::Error("Windows 업데이트를 확인할 수 없습니다.".into()));
            if replies.send(state).is_err() {
                break;
            }
            ctx.request_repaint();
        }
    });
}

fn decode_windows(result: serde_json::Value) -> State {
    let string = |key| result[key].as_str().unwrap_or("").to_owned();
    match result["status"].as_str() {
        Some("current") => State::Current,
        Some("available") => State::Available {
            version: string("version"),
            notes: string("notes"),
            digest: string("digest"),
            can_install: result["can_install"] == true,
        },
        Some("installing") => State::Installing,
        _ => State::Error(string("error")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn desktop_controls_require_an_explicit_install_click_and_the_signer_configuration() {
        let ctx = egui::Context::default();
        let (request, work) = mpsc::channel();
        let (_, replies) = mpsc::channel();
        let mut updates = Updates {
            worker: Some(request),
            replies,
            state: State::Idle,
            next_check: Instant::now(),
            busy: false,
            interactive: false,
            windows: true,
        };
        let frame = |updates: &mut Updates, events| {
            ctx.run(
                egui::RawInput {
                    screen_rect: Some(egui::Rect::from_min_size(
                        egui::Pos2::ZERO,
                        egui::vec2(640.0, 440.0),
                    )),
                    events,
                    ..Default::default()
                },
                |ctx| {
                    if updates.dialog(ctx) {
                        return;
                    }
                    egui::CentralPanel::default().show(ctx, |ui| updates.control(ui, true));
                },
            )
        };
        let click = |updates: &mut Updates, label| {
            let _ = frame(updates, vec![]);
            let output = frame(updates, vec![]);
            let pos = output
                .shapes
                .iter()
                .find_map(|shape| match &shape.shape {
                    egui::Shape::Text(text) if text.galley.job.text == label => {
                        Some(text.pos + text.galley.size() / 2.0)
                    }
                    _ => None,
                })
                .unwrap_or_else(|| panic!("Missing control {label}"));
            for pressed in [true, false] {
                frame(
                    updates,
                    vec![
                        egui::Event::PointerMoved(pos),
                        egui::Event::PointerButton {
                            pos,
                            button: egui::PointerButton::Primary,
                            pressed,
                            modifiers: egui::Modifiers::NONE,
                        },
                    ],
                );
            }
        };
        frame(&mut updates, vec![]);
        click(&mut updates, "업데이트 확인…");
        assert!(matches!(work.try_recv(), Ok(Request::Manual)));
        let result = |can_install| State::Available {
            version: "0.1.4".into(),
            notes: "Release notes".into(),
            digest: "approved-hash".into(),
            can_install,
        };
        updates.state = result(false);
        updates.busy = false;
        click(&mut updates, "다운로드 및 설치");
        assert!(work.try_recv().is_err());
        updates.state = result(true);
        assert!(work.try_recv().is_err());
        click(&mut updates, "다운로드 및 설치");
        assert!(
            matches!(work.try_recv(), Ok(Request::Install { version, digest })
                         if version == "0.1.4" && digest == "approved-hash")
        );
    }

    #[test]
    fn background_results_and_manual_approval_are_separate() {
        let ctx = egui::Context::default();
        let (request, work) = mpsc::channel();
        let (reply, replies) = mpsc::channel();
        let mut updates = Updates {
            worker: Some(request),
            replies,
            state: State::Idle,
            next_check: Instant::now(),
            busy: false,
            interactive: false,
            windows: true,
        };
        updates.tick(&ctx, false);
        assert!(work.try_recv().is_err());
        updates.tick(&ctx, true);
        assert!(matches!(work.try_recv(), Ok(Request::Info)));
        reply
            .send(State::Available {
                version: "0.1.4".into(),
                notes: "notes".into(),
                digest: "hash".into(),
                can_install: true,
            })
            .unwrap();
        updates.tick(&ctx, true);
        assert!(!updates.interactive);
        assert!(work.try_recv().is_err());
        updates.start(Request::Manual, true);
        assert!(matches!(work.try_recv(), Ok(Request::Manual)));
        assert!(updates.interactive);
        assert_eq!(
            decode_windows(serde_json::json!({"status":"available"})),
            State::Available {
                version: String::new(),
                notes: String::new(),
                digest: String::new(),
                can_install: false,
            }
        );
    }
}
