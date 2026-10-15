# Argon Orchestrator (`argon-orchestrator`)

[English Documentation (README.md)](./README.md)

專為 Google Antigravity (`agy`) 設計的 Orchestrator 插件：將主代理（Primary Agent）綁定為 **Gemini 4 Argon (`High` 或 `Medium`)**（自動保留您在 UI/CLI 切換的思考等級），並根據任務情境（Context）自動將所有子代理（Subagents）固定為 **`flash`** 或 **`flash_lite`** 模型。

---

## 架構與情境路由策略 (Context Routing Matrix)

| 層級 | 綁定模型 | 角色與負責範圍 |
| :--- | :--- | :--- |
| **主代理 (Primary Orchestrator)** | `Gemini 4 Argon (High / Medium)` | 系統架構設計、多步驟任務拆解、跨模組重構、複雜邏輯與資安把關、最終結果彙整與驗證（自動依您選用的 `High` 或 `Medium` 等級運行）。 |
| **一級子代理 (`flash-worker`)** | `flash` | 跨檔案程式碼探索、外部文件與網頁查證、撰寫與執行單元/整合測試、局部範圍程式碼修改與除錯。 |
| **二級子代理 (`flash-lite-scout`)** | `flash_lite` | 快速檔案/符號查找、Grep/Glob 搜尋、Git/Log 狀態檢查、單一檔案檢視、排版檢查與輕量摘要。 |

---

## 插件內含組件

- **[`rules/AGENTS.md`](./rules/AGENTS.md)**：常駐規則（Always-on Rules），覆寫預設 `Model: "inherit"` 行為，強制要求主代理在呼叫 `invoke_subagent` 與 `run_workflow` 時必須依據任務複雜度指定 `flash` 或 `flash_lite`（禁止使用 `inherit` 或 `pro`）。
- **[`hooks.json`](./hooks.json) 與 [`scripts/enforce-orchestrator.py`](./scripts/enforce-orchestrator.py)**：
  - **`SessionStart` Hook**：每次啟動對話時自動檢查 `~/.gemini/antigravity-cli/settings.json` 中的主模型是否為 `"Gemini 4 Argon (High)"` 或 `"Gemini 4 Argon (Medium)"`（若您在選單切換 `Medium` 或 `High` 會自動保留您的選擇不覆寫；若誤設為非 Argon 模型則自動復原為 `High`，亦可透過 `AGY_ARGON_PRIMARY_MODEL` 或 `AGY_ALLOW_NON_ARGON_PRIMARY=1` 自訂），並注入 Orchestrator 啟動提示。
  - **`PreToolUse` Hook (`invoke_subagent`)**：硬性攔截所有 `invoke_subagent` 呼叫；若發現未指定 `Model` 或誤傳 `"inherit"` / `"pro"`，會自動根據 `TypeName`、`Workspace`、`Role` 與 `Prompt` 內容智慧重寫為 `"flash"` 或 `"flash_lite"`。
- **[`agents/`](./agents/)**：
  - **[`flash-lite-scout`](./agents/flash-lite-scout/agent.md)**：預設綁定 `model: flash_lite` 的唯讀快速偵察子代理。
  - **[`flash-worker`](./agents/flash-worker/agent.md)**：預設綁定 `model: flash` 的多檔案研究、測試與開發子代理。
- **[`skills/orchestrate/SKILL.md`](./skills/orchestrate/SKILL.md)**：提供 `/orchestrate` 技能，執行四階段協作流程（`flash_lite` 平行偵察 $\rightarrow$ Argon 架構規劃 $\rightarrow$ `flash` 平行實作與測試 $\rightarrow$ Argon 最終驗證）。

---

## 安裝方式

### 方式一：直接 Clone 至 Antigravity 全域插件目錄

```bash
git clone https://github.com/ivanusto/argon-orchestrator.git ~/.gemini/config/plugins/argon-orchestrator
```

### 方式二：透過 `agy plugin` CLI 安裝

```bash
git clone https://github.com/ivanusto/argon-orchestrator.git ~/argon-orchestrator
agy plugin validate ~/argon-orchestrator
agy plugin install ~/argon-orchestrator
```

安裝後請重新啟動 `agy`。可隨時透過 `/plugin` 介面或 `agy plugin enable argon-orchestrator` / `agy plugin disable argon-orchestrator` 啟用或停用。
