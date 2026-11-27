use eframe::egui::*;
use serde_json::{Value, json};
use std::time::{Duration, Instant};
use url::Url;

const ISSUES: &str = "https://github.com/Jang-myoung-gyoon/gitdoc/issues";
const LOGIN: &str = "https://github.com/login/device";
const CONFIRM: &str = "입력한 내용을 공개 GitHub 이슈로 전송하는 데 동의합니다.";

pub type Request = (&'static str, Value);

#[derive(Default)]
pub struct BugReport {
    pub open: bool,
    title: String,
    steps: String,
    expected: String,
    actual: String,
    context: bool,
    confirmed: bool,
    configured: Option<bool>,
    login: Option<String>,
    prompt: Option<String>,
    next_poll: Option<Instant>,
    pending: Option<&'static str>,
    message: Option<String>,
    uncertain: bool,
    issue: Option<u64>,
}

pub fn report_button(ui: &mut Ui) -> Response {
    let response = ui
        .button("    버그 신고")
        .on_hover_text("버그를 신고하고 GitHub 이슈를 등록합니다.");
    let stroke = ui.style().interact(&response).fg_stroke;
    let center = response.rect.left_center() + vec2(9.0, 0.0);
    let painter = ui.painter();
    painter.circle_stroke(center, 3.5, stroke);
    for direction in [-1.0, 1.0] {
        for (height, tip) in [(-1.5, -3.5), (0.0, 0.0), (1.5, 3.5)] {
            painter.line_segment(
                [
                    center + vec2(3.0 * direction, height),
                    center + vec2(5.2 * direction, tip),
                ],
                stroke,
            );
        }
        painter.line_segment(
            [
                center + vec2(direction, -3.3),
                center + vec2(2.8 * direction, -5.1),
            ],
            stroke,
        );
    }
    response
}

impl BugReport {
    fn body(&self) -> String {
        let mut body = format!(
            "## 재현 방법\n{}\n\n## 기대 결과\n{}\n\n## 실제 결과\n{}",
            self.steps.trim(),
            self.expected.trim(),
            self.actual.trim(),
        );
        if self.context {
            body.push_str(&format!(
                "\n\n## 실행 환경\nGitDoc {} · {}",
                env!("CARGO_PKG_VERSION"),
                std::env::consts::OS,
            ));
        }
        body
    }

    fn complete(&self) -> bool {
        [&self.title, &self.steps, &self.expected, &self.actual]
            .into_iter()
            .all(|field| !field.trim().is_empty())
    }

    fn action(&mut self, name: &'static str, data: Value) -> Request {
        self.pending = Some(name);
        self.message = None;
        (name, data)
    }

    pub fn receive(&mut self, name: &str, result: Result<Value, String>) {
        if self.pending != Some(name) {
            return;
        }
        self.pending = None;
        let value = match result {
            Ok(value) => value,
            Err(message) => {
                if name == "bug_report_submit" {
                    self.uncertain = true;
                }
                self.message = Some(message);
                self.prompt = None;
                self.next_poll = None;
                return;
            }
        };
        match name {
            "bug_report_status" => {
                self.configured = value["configured"].as_bool();
                self.login = value["login"].as_str().map(str::to_owned);
            }
            "bug_report_start" | "bug_report_poll" => {
                if let Some(code) = value["code"].as_str() {
                    self.prompt = Some(code.to_owned());
                }
                if let Some(interval) = value["interval"].as_u64() {
                    self.next_poll = Some(Instant::now() + Duration::from_secs(interval.min(3600)));
                }
                if let Some(login) = value["login"].as_str() {
                    self.login = Some(login.to_owned());
                    self.prompt = None;
                    self.next_poll = None;
                }
            }
            "bug_report_cancel" => {
                self.prompt = None;
                self.next_poll = None;
                self.login = None;
                self.confirmed = false;
            }
            "bug_report_submit" => {
                self.issue = value["number"].as_u64();
                if let Some(message) = value["rejected"].as_str() {
                    self.login = value["login"].as_str().map(str::to_owned);
                    self.message = Some(message.into());
                } else if let Some(message) = value["uncertain"].as_str() {
                    self.uncertain = true;
                    self.message = Some(message.into());
                }
                self.confirmed = false;
            }
            _ => {}
        }
    }

    pub fn connection_failed(&mut self) {
        self.pending = None;
        self.message = Some("버그 신고 처리기에 연결할 수 없습니다.".into());
    }

    fn browser(&mut self, ctx: &Context) {
        let mut url = Url::parse(&format!("{ISSUES}/new")).expect("fixed GitHub URL");
        url.query_pairs_mut()
            .append_pair("title", self.title.trim());
        let mut with_body = url.clone();
        let body = self.body();
        with_body.query_pairs_mut().append_pair("body", &body);
        if with_body.as_str().len() > 7500 {
            ctx.copy_text(body);
            ctx.open_url(OpenUrl::new_tab(url.to_string()));
            self.message = Some(
                "본문을 클립보드에 복사했습니다. GitHub 작성 화면에 붙여넣고 직접 등록하세요."
                    .into(),
            );
        } else {
            ctx.open_url(OpenUrl::new_tab(with_body.to_string()));
            self.message = Some(
                "GitHub 작성 화면을 열었습니다. 브라우저에서 내용을 확인하고 직접 등록하세요."
                    .into(),
            );
        }
        self.confirmed = false;
    }

    pub fn show(&mut self, ctx: &Context) -> Option<Request> {
        if !self.open {
            return None;
        }
        let mut open = self.open;
        let mut request = None;
        Window::new("버그 신고")
            .id(Id::new("public-github-bug-report"))
            .open(&mut open)
            .collapsible(false)
            .default_width(490.0)
            .show(ctx, |ui| {
                ScrollArea::vertical()
                    .max_height((ctx.content_rect().height() - 120.0).max(250.0))
                    .show(ui, |ui| request = self.form(ui));
            });
        self.open = open;
        if request.is_some() || self.pending.is_some() || !self.open {
            return request;
        }
        if let Some(when) = self.next_poll {
            let delay = when.saturating_duration_since(Instant::now());
            if delay.is_zero() {
                self.next_poll = None;
                return Some(self.action("bug_report_poll", json!({})));
            }
            ctx.request_repaint_after(delay);
        }
        request
    }

    fn form(&mut self, ui: &mut Ui) -> Option<Request> {
        if let Some(number) = self.issue {
            ui.label(format!("공개 GitHub 이슈 #{number}로 등록했습니다."));
            ui.hyperlink_to("등록된 이슈 열기", format!("{ISSUES}/{number}"));
            if ui.button("새 신고 작성").clicked() {
                *self = Self {
                    open: true,
                    configured: self.configured,
                    login: self.login.take(),
                    ..Self::default()
                };
            }
            return None;
        }
        ui.label(
            "신고 내용과 GitHub 계정 이름은 공개됩니다. 문서 내용이나 개인정보는 입력하지 마세요.",
        );
        let waiting = self.pending.is_some();
        let locked = waiting || self.uncertain;
        let changed = ui
            .add_enabled_ui(!locked, |ui| {
                let mut changed = field(ui, "제목", &mut self.title, false);
                changed |= field(ui, "재현 방법", &mut self.steps, true);
                changed |= field(ui, "기대 결과", &mut self.expected, true);
                changed |= field(ui, "실제 결과", &mut self.actual, true);
                changed
                    | ui.checkbox(&mut self.context, "GitDoc 버전과 운영체제 포함")
                        .changed()
            })
            .inner;
        if changed {
            self.confirmed = false;
        }
        if self.complete() {
            let body = self.body();
            ui.collapsing("공개될 내용 미리보기", |ui| {
                ui.strong(self.title.trim());
                ui.label(body);
            });
        }
        if let Some(message) = &self.message {
            ui.label(message);
        }
        if self.uncertain {
            ui.hyperlink_to("공개 GitHub 이슈 목록 열기", ISSUES);
            return None;
        }
        if waiting {
            ui.spinner();
        }
        if let Some(code) = &self.prompt {
            ui.label(format!("GitHub 로그인 코드  {code}"));
            ui.horizontal_wrapped(|ui| {
                if ui.button("코드 복사").clicked() {
                    ui.ctx().copy_text(code.clone());
                }
                ui.hyperlink_to("GitHub 로그인 페이지 열기", LOGIN);
            });
            if ui.button("로그인 취소").clicked() {
                self.prompt = None;
                self.next_poll = None;
                return Some(self.action("bug_report_cancel", json!({})));
            }
        } else if let Some(login) = &self.login {
            ui.label(format!("GitHub  @{login}"));
            if ui.add_enabled(!waiting, Button::new("로그아웃")).clicked() {
                return Some(self.action("bug_report_cancel", json!({})));
            }
        } else if self.configured == Some(true) {
            if ui
                .add_enabled(!waiting, Button::new("GitHub 로그인"))
                .clicked()
            {
                return Some(self.action("bug_report_start", json!({})));
            }
        } else if self.configured == Some(false) {
            ui.weak("이 빌드에서는 GitHub 작성 화면에서 직접 등록할 수 있습니다.");
        }
        let complete = self.complete();
        ui.add_enabled(
            complete && !waiting,
            Checkbox::new(&mut self.confirmed, CONFIRM),
        );
        let ready = complete && self.confirmed && !waiting && self.prompt.is_none();
        if ui
            .add_enabled(
                ready && self.login.is_some(),
                Button::new("공개 GitHub 이슈로 제출"),
            )
            .clicked()
        {
            return Some(self.action(
                "bug_report_submit",
                json!({"title": self.title.trim(), "body": self.body(), "confirmed": true}),
            ));
        }
        if ui
            .add_enabled(ready, Button::new("GitHub 작성 화면 열기 (직접 등록)"))
            .clicked()
        {
            self.browser(ui.ctx());
        }
        None
    }

    pub fn prepare(&mut self) -> Option<Request> {
        self.open = true;
        if self.configured.is_none() && self.pending.is_none() {
            Some(self.action("bug_report_status", json!({})))
        } else {
            None
        }
    }
}

fn field(ui: &mut Ui, label: &str, value: &mut String, multiline: bool) -> bool {
    let label = ui.label(label);
    let input = if multiline {
        TextEdit::multiline(value).desired_rows(2).char_limit(3000)
    } else {
        TextEdit::singleline(value).char_limit(200)
    };
    ui.add(input.desired_width(f32::INFINITY))
        .labelled_by(label.id)
        .changed()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn report_posts_only_after_confirmation_and_shows_issue_number() {
        let ctx = Context::default();
        let mut report = BugReport {
            title: "제목 샘플".into(),
            steps: "재현 단계".into(),
            expected: "기대한 값".into(),
            actual: "실제 값".into(),
            ..BugReport::default()
        };
        let (op, _) = report.prepare().unwrap();
        assert_eq!(op, "bug_report_status");
        report.receive(op, Ok(json!({"configured": true, "login": "sample-user"})));
        let frame = |report: &mut BugReport, events| {
            let mut request = None;
            let output = ctx.run(
                RawInput {
                    screen_rect: Some(Rect::from_min_size(Pos2::ZERO, vec2(510.0, 730.0))),
                    events,
                    ..Default::default()
                },
                |ctx| {
                    CentralPanel::default().show(ctx, |ui| {
                        report_button(ui);
                        request = report.form(ui);
                    });
                },
            );
            (request, output)
        };
        let find = |output: &FullOutput, label| {
            output
                .shapes
                .iter()
                .find_map(|shape| match &shape.shape {
                    epaint::Shape::Text(text) if text.galley.job.text == label => {
                        Some(text.pos + text.galley.size() / 2.0)
                    }
                    _ => None,
                })
                .unwrap_or_else(|| panic!("missing visible control: {label}"))
        };
        let click = |report: &mut BugReport, point| {
            let button = |pressed| Event::PointerButton {
                pos: point,
                button: PointerButton::Primary,
                pressed,
                modifiers: Modifiers::NONE,
            };
            frame(report, vec![Event::PointerMoved(point), button(true)]);
            frame(report, vec![button(false)]).0
        };
        let (_, output) = frame(&mut report, vec![]);
        find(&output, "    버그 신고");
        let submit = find(&output, "공개 GitHub 이슈로 제출");
        assert!(click(&mut report, submit).is_none());
        let confirm = find(&output, CONFIRM);
        click(&mut report, confirm);
        let (op, payload) = click(&mut report, submit).unwrap();
        assert_eq!(op, "bug_report_submit");
        assert_eq!(
            payload,
            json!({"title": "제목 샘플",
                                  "body": "## 재현 방법\n재현 단계\n\n## 기대 결과\n기대한 값\n\n## 실제 결과\n실제 값",
                                  "confirmed": true})
        );
        report.receive(op, Ok(json!({"number": 42})));
        let (_, output) = frame(&mut report, vec![]);
        find(&output, "공개 GitHub 이슈 #42로 등록했습니다.");
    }
}
