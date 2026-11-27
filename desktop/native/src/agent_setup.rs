use eframe::egui::*;
use serde_json::{Value, json};

pub type Request = (&'static str, Value);

#[derive(Default)]
pub struct AgentSetup {
    pub open: bool,
    claude: bool,
    plan: Value,
    confirmed: bool,
    pending: bool,
    notice: Option<String>,
}

fn prompt(target: &str) -> String {
    let (label, command, skill) = if target == "claude" {
        (
            "Claude Code",
            "claude mcp add gitdoc-local --scope user --transport stdio --env DOCVERS_DB=<chosen-db> -- <python> -E -s -B <app>/mcp_server.py --read-only",
            "~/.claude/skills/document-versions-desktop 또는 CLAUDE_CONFIG_DIR 아래 skills",
        )
    } else {
        (
            "Codex",
            "codex mcp add --env DOCVERS_DB=<chosen-db> gitdoc-local -- <python> -E -s -B <app>/mcp_server.py --read-only",
            "~/.agents/skills/document-versions-desktop",
        )
    };
    format!(
        "{label}에 로컬 GitDoc MCP와 document-versions-desktop 스킬을 연결해 주세요.\n\n        GitDoc 데스크톱의 설정 → 에이전트 연결에서 설치 경로와 현재 문서함을 확인하고, 내가 선택한 문서함 하나에 대해 읽기 전용 연결을 설치해 주세요.         설치된 GitDoc의 Python과 mcp_server.py를 사용하고 문서함 DB 및 CLI 경로를 확인해 주세요.         기존 등록과 스킬을 변경하기 전에 충돌을 확인하고 사용자 동의를 받으세요.\n\n        공식 CLI 명령 형식\n{command}\n\n        GitDoc에 포함된 SKILL.md를 {skill}에 배치하세요. 경로 자리표시는 현재 연결에서 확인한 로컬 값으로 바꾸고 명령을 인자 배열로 실행하세요.         임의 프롬프트를 셸에서 실행하지 마세요. MCP initialize/tools/list에서 search_documents와 list_versions가 보이고 쓰기 도구가 없는지, 설치한 스킬을 로컬 에이전트가 찾는지 확인하세요."
    )
}

impl AgentSetup {
    fn target(&self) -> &'static str {
        if self.claude { "claude" } else { "codex" }
    }

    pub fn receive(&mut self, op: &str, result: Result<Value, String>) {
        self.pending = false;
        self.confirmed = false;
        match result {
            Ok(plan) if op == "agent_setup_preview" => self.plan = plan,
            Ok(result) => {
                self.plan = Value::Null;
                self.notice = result["message"].as_str().map(str::to_owned);
            }
            Err(message) => self.notice = Some(message),
        }
    }

    pub fn show(&mut self, ctx: &Context) -> Option<Request> {
        if !self.open {
            return None;
        }
        let mut open = self.open;
        let mut request = None;
        Window::new("에이전트 연결")
            .open(&mut open)
            .collapsible(false)
            .default_width(500.0)
            .show(ctx, |ui| {
                ui.label("현재 문서함 하나의 읽기 전용 MCP와 GitDoc 스킬을 로컬 에이전트에 설치합니다.");
                ui.add_enabled_ui(!self.pending, |ui| {
                    let previous = self.claude;
                    ui.horizontal_wrapped(|ui| {
                        ui.selectable_value(&mut self.claude, false, "Codex");
                        ui.selectable_value(&mut self.claude, true, "Claude Code");
                    });
                    if previous != self.claude {
                        self.plan = Value::Null;
                        self.notice = None;
                        self.confirmed = false;
                    }
                    ui.horizontal_wrapped(|ui| {
                        if ui.button("설치 프롬프트 복사").clicked() {
                            ctx.copy_text(prompt(self.target()));
                            self.notice = Some("계정과 문서 경로를 제외한 설치 프롬프트를 복사했습니다.".into());
                        }
                        if ui.button("로컬 설치 사전 검사").clicked() {
                            self.plan = Value::Null;
                            self.notice = None;
                            self.confirmed = false;
                            request = Some(("agent_setup_preview", json!({"target": self.target()})));
                        }
                    });
                    if let Some(id) = self.plan["id"].as_str() {
                        ui.separator();
                        ScrollArea::vertical().max_height(260.0).show(ui, |ui| {
                            for (label, field) in [("에이전트 버전", "version"), ("설정 파일", "config"),
                                                  ("스킬 위치", "skill"), ("읽을 문서함", "database")] {
                                ui.label(format!("{label} · {}", self.plan[field].as_str().unwrap_or("")));
                            }
                            ui.label("설치 명령 인자");
                            ui.monospace(self.plan["install"].to_string());
                            if self.plan["registered"] == true {
                                ui.label("연결 해제 명령 인자");
                                ui.monospace(self.plan["remove"].to_string());
                            }
                        });
                        ui.checkbox(&mut self.confirmed, "표시된 명령과 경로 변경을 허용합니다.");
                        ui.horizontal_wrapped(|ui| {
                            for (label, operation, enabled) in [
                                ("MCP와 스킬 설치", "install", true),
                                ("연결 해제", "remove", self.plan["registered"] == true),
                            ] {
                                if ui.add_enabled(enabled && self.confirmed, Button::new(label)).clicked() {
                                    request = Some(("agent_setup_apply", json!({"target": self.target(),
                                          "plan_id": id, "confirmed": true, "operation": operation})));
                                }
                            }
                        });
                    }
                });
                if self.pending {
                    ui.spinner();
                }
                if let Some(notice) = &self.notice {
                    ui.label(notice);
                }
            });
        self.open = open;
        if request.is_some() {
            self.pending = true;
        }
        request
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copied_prompts_contain_placeholders_and_a_read_only_server() {
        for target in ["codex", "claude"] {
            let text = prompt(target);
            assert!(text.contains("<chosen-db>") && text.contains("--read-only"));
            assert!(!text.contains("/Users/") && !text.contains("C:\\Users\\"));
        }
        assert!(prompt("claude").contains("--scope user"));
        assert!(prompt("codex").contains("~/.agents/skills"));
    }
}
