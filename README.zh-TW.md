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

- **[`rules/AGENTS.md`](./rules/AGENTS.md)**：常駐規則：情境路由矩陣、呼叫規範、子代理四行回報合約，以及 branch worktree 清理規則。
- **[`hooks.json`](./hooks.json) 與 [`scripts/enforce-orchestrator.py`](./scripts/enforce-orchestrator.py)**（Python 3.8+，僅用標準函式庫）：

  | Hook | 作用 |
  | :--- | :--- |
  | `SessionStart` | 讀取 `~/.gemini/antigravity-cli/settings.json`，主模型若非 Argon 就**警告**並注入政策提示。只有設定 `AGY_ARGON_PIN_SETTINGS=1` 才會改寫檔案。 |
  | `PreInvocation` | 每回合檢查實際 session 模型；若非 Argon/Pro 層級，每次 invocation 警告一次（可抓到 session 開始後在 UI 切換模型的情況）。 |
  | `PreToolUse` `invoke_subagent` | 依 `TypeName`、`Workspace`、`Role`、`Prompt` 把每個子代理分到 `flash` / `flash_lite`。強訊號（測試、除錯、追蹤、修改動詞）一律優先於查找關鍵字。`flash-lite-scout` 收到修改任務會自動升級為 `flash-worker`。 |
  | `PreToolUse` `send_message` | 拒絕對 `flash_lite` 收件者送出程式修改或多步驟（超過 3 條編號規則）的後續指令。 |
  | `PreToolUse` `run_workflow` | 拒絕 `agent(...)` 呼叫缺少 `model="flash"` / `model="flash_lite"` 的腳本。 |
  | `PreToolUse` `define_subagent` | 強制 `enable_subagent_tools=false`（禁止巢狀再生子代理）。 |
  | `PreToolUse` 其他工具（選擇性啟用，`AGY_ARGON_AUTO_ALLOW=1`） | 工作區範圍守衛：`run_command` 與檔案工具若 cwd、路徑與命令內容都在 `workspacePaths` / `artifactDirectoryPath` 內就回 `allow`；路徑在外、提權、遞迴刪除根目錄、更動系統、force push、下載後直接導入 shell 則回 `ask`；沒有路徑參數的工具（瀏覽器、MCP、網路）不表態。 |
  | `PostToolUse` | 以 agy 寫在 `<artifactDirectoryPath>/.system_generated/subagents/` 的中繼資料（型別、角色、存活狀態、worktree）配對子代理；生成失敗時回滾。 |
  | `Stop` | 回合完全閒置時若仍有存活的 `Workspace:"branch"` worker，針對同一組 worker 只提醒一次，要求先合併/捨棄並 kill。絕不阻擋使用者取消、錯誤或預算上限。 |

- **[`agents/`](./agents/)**：
  - **[`flash-lite-scout`](./agents/flash-lite-scout/agent.md)**：綁定 `model: flash_lite` 的唯讀偵察子代理；只有本機查找工具，無網路、無寫入。
  - **[`flash-worker`](./agents/flash-worker/agent.md)**：綁定 `model: flash` 的研究與執行子代理；只修改提示詞允許清單內的檔案，失敗兩次即停止回報。
  - 兩者都只回傳固定四行報告（`STATUS / FILES_CHANGED / VERIFICATION / NOTES`），讓主代理讀回的 token 降到最低。
- **[`skills/orchestrate/SKILL.md`](./skills/orchestrate/SKILL.md)**：`/orchestrate` 四階段流程（`flash_lite` 偵察 → Argon 規劃 → `flash` 執行 → Argon 驗證與 branch 清理）。

### 環境變數

| 變數 | 效果 |
| :--- | :--- |
| `AGY_ARGON_PIN_SETTINGS=1` | 選擇性啟用：session 開始時把 `settings.json` 改回 Argon 模型（1.1.x 的行為）。 |
| `AGY_ARGON_PRIMARY_MODEL` | 改寫時的目標模型：`Gemini 4 Argon (High)`（預設）或 `Gemini 4 Argon (Medium)`。 |
| `AGY_ALLOW_NON_ARGON_PRIMARY=1` | 關閉所有主模型警告。 |
| `AGY_ARGON_AUTO_ALLOW=1` | 啟用工作區範圍守衛（預設關閉）。 |
| `AGY_ARGON_DEBUG=1` | 每次 hook 呼叫在 `<artifactDirectoryPath>/argon_orchestrator_debug.log` 追加一行 JSON（模式、工具、payload 欄位、決策）。 |

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

---

## 開發與測試

```bash
python3 -m unittest discover -s tests -v
agy plugin validate . --strict
```

**Headless 限制。** 在 `agy -p`（print）模式下，Antigravity 會把所有需要權限提示的工具直接軟性拒絕，不管 hook 回什麼 decision 或 `permissionOverrides`；該模式只認 `settings.json` 的 allow 規則或 `--dangerously-skip-permissions`。因此工作區範圍守衛只在互動式 session 中能減少提示（已在 agy 1.4.0 互動模式驗證：工作區內的編輯與測試命令不再跳出權限詢問）。

已於 2026-10-16 在 `agy` 1.4.0 實測：`SessionStart` 與 `PreInvocation` 提示確實進入模型上下文；實際模型 id 形如 `gemini-4-argon-high` / `gemini-3.8-flash-high`；`Stop` 會收到 `fullyIdle`、`executionNum` 與 `terminationReason`（正常結束回合為 `NO_TOOL_CALL`）；子代理中繼資料在 `manage_subagents(Action="kill")` 後由 `SUBAGENT_STATE_ALIVE` 變為 `SUBAGENT_STATE_KILLED`。

版本紀錄見 [CHANGELOG.md](./CHANGELOG.md)。

---

## 授權

[MIT](./LICENSE)
