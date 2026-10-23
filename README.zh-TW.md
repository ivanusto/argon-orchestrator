# Argon Orchestrator（`argon-orchestrator`）

[English README](./README.md)

僅支援 Antigravity（`agy`）的外掛。以尖端模型擔任主控代理，並透過具信心分數的規則引擎，將每個子代理路由至 `flash` 或 `flash_lite`；同時管制高風險工具呼叫，並強制子代理使用有界的四標頭回報格式。

支援的主控模型（以 agy 1.4.6 提供者為準）：**Gemini 4 Argon（Medium / High）**、**Claude Opus 5.5**、**Claude Sonnet 5.5**。Argon Low、Gemini 3.8 Flash 與 GPT-OSS 120B 會被警告為非尖端模型。

---

## 架構與路由政策

| 層級 | 模型 | 職責 |
| :--- | :--- | :--- |
| **主控代理** | Gemini 4 Argon（Medium / High）、Claude Opus 5.5、Claude Sonnet 5.5 | 架構設計、規劃、跨模組重構、安全審查與最終彙整。 |
| **第一層（`flash-worker`）** | `flash` | 跨檔案研究、網頁與文件查詢、撰寫與執行測試、範圍明確的修改與除錯。 |
| **第二層（`flash-lite-scout`）** | `flash_lite` | 唯讀查詢：檔案、符號、grep/glob、git 與日誌檢查、單一檔案檢視。 |

---

## 元件

- **[`rules/AGENTS.md`](./rules/AGENTS.md)**：常駐政策，包含路由矩陣、呼叫規則、四標頭回報合約與分支清理。
- **[`hooks.json`](./hooks.json) 與 [`scripts/enforce-orchestrator.py`](./scripts/enforce-orchestrator.py)**（Python 3.8 以上，僅用標準函式庫）。邏輯位於 `scripts/argon_hook/`（`common`、`models`、`routing`、`judge`、`report`、`state`、`guards`、`tools`、`handlers`）。
- **[`agents/`](./agents/)**：`flash-lite-scout`（`flash_lite`，唯讀）與 `flash-worker`（`flash`，僅能修改提示中檔案白名單內的檔案）。
- **[`skills/orchestrate/SKILL.md`](./skills/orchestrate/SKILL.md)**：`/orchestrate` 四階段流程（偵察、規劃、執行、驗證）。

### Hook 一覽

| Hook | 作用 |
| :--- | :--- |
| `SessionStart` | 檢查 `~/.gemini/antigravity-cli/settings.json` 的主控模型，不在上述清單時發出警告；若 `mitosis-orchestrator` 同時啟用也會警告。僅在 `AGY_ARGON_PIN_SETTINGS=1` 時改寫設定。 |
| `PreInvocation` | 每回合重新檢查實際模型（可偵測介面切換模型），並重設每回合追蹤。 |
| `PreToolUse` `invoke_subagent` | 將每個子代理路由至 `flash` 或 `flash_lite`；缺少四標頭的提示會自動附加 REPORT FORMAT 區塊；套用 scout 升級；斷路器跳脫時拒絕。 |
| `PreToolUse` `send_message` | 拒絕對 `flash_lite` 收件者下達修改程式碼或多步驟的後續指令；驗證子代理回報給上層的訊息，無效時拒絕一次使其重送。 |
| `PreToolUse` `run_workflow` / `define_subagent` | 拒絕未指定 `model="flash"` 或 `"flash_lite"` 的 `agent(...)`；強制 `enable_subagent_tools=false`。 |
| `PreToolUse` scout 唯讀層 | `flash-lite-scout` 子代理僅能使用唯讀工具與指令。 |
| `PreToolUse` 衛生與快速路徑 | 阻擋主控直接讀取 `.log` 與 `transcript.jsonl`；限制每回合直接編輯檔案數（預設 3）與「編輯後立即測試」迴圈。 |
| `PostToolUse` | 配對子代理與其中繼資料，追蹤失敗連續次數供斷路器使用。 |
| `Stop` | 若仍有存活的 `Workspace:"branch"` 工作者，詢問一次是否合併或捨棄並終止。 |

狀態以每個工作階段的 `flock` 保護，逾時即放行（fail open），並以原子方式存檔。

### 路由

引擎原樣移植自 mitosis-orchestrator 2.3.0。每項決策附帶信心分數。強訊號（測試、除錯、追蹤、修改動詞）優先於查詢關鍵字，內含中文規則；`scout-unproven` 規則要求有明確的唯讀證據，任務才能交給 scout；worker-lookup 降級則把單純查詢由 `flash` 改派 `flash_lite`。低信心案例可選用判斷模型（judge）仲裁。

`tests/routing_cases.jsonl`（855 筆）與 `tests/routing_holdout*.jsonl`（v1 至 v8，各 40 筆）支撐 `tests/test_routing_eval.py`：準確率 >= 0.95、成本誤判 <= 0.05、worker 任務送至 scout 的數量為零。`scripts/routing-parity.py <mitosis_repo>` 可證明與 mitosis 的差異為 0。

### 升級與回報強制

- **升級**：scout 回報 `FAILED` 或 `BLOCKED` 時，下一次 scout 派工會升級為 `flash-worker`，僅一次，且須在 3 次派工或 30 分鐘內。以 `AGY_ARGON_ESCALATE=0` 停用。
- **回報格式**：缺少四標頭的子代理提示會自動附加 REPORT FORMAT 區塊（`AGY_ARGON_APPEND_REPORT_FORMAT=0` 可停用）。
- **回報強制**：子代理傳給上層的 `send_message` 會依合約驗證；無效回報會被拒絕一次，使其重送（`AGY_ARGON_ENFORCE_REPORT=0` 可停用）。

合約為 `STATUS`、`FILES_CHANGED`、`VERIFICATION`、`NOTES`；`NOTES` 下最多 10 條項目；總長度不超過 1,600 字元；`BLOCKED` 或 `FAILED` 時須附 `file://` 日誌 URI。

---

## 權限行為

於 agy 1.4.6 實測確認（2026-10-23）：

- hook 回傳 `{}` 視為**拒絕**。
- `{"decision":"allow"}` 表示「無異議」；agy 仍會對指令與檔案寫入顯示自己的權限提示。
- 因此外掛在沒有意見時會明確回傳 allow，絕不繞過 agy 的權限提示。
- `AGY_ARGON_AUTO_ALLOW=1` 是唯一會授予 `permissionOverrides` 的途徑，且僅限工作區內。工作區外路徑、提權、刪除根目錄、強制推送與管線導入 shell 的下載一律回答 `ask`。

在無頭模式 `agy -p` 中，凡需要提示者，agy 一律軟性拒絕，與 hook 無關。

---

## 環境變數


| 變數 | 作用 |
| :--- | :--- |
| `AGY_ARGON_PIN_SETTINGS=1` | 工作階段開始時將 `settings.json` 改寫為主控模型。 |
| `AGY_ARGON_PRIMARY_MODEL` | 釘選時使用的模型（預設 `Gemini 4 Argon (High)`）。 |
| `AGY_ALLOW_NON_ARGON_PRIMARY=1` | 關閉主控模型警告。 |
| `AGY_ARGON_AUTO_ALLOW=1` | 對工作區內工具授予 `permissionOverrides`。 |
| `AGY_ARGON_MAX_DIRECT_EDITS=<int>` | 主控每回合可直接編輯的不同檔案數（預設 3）。 |
| `AGY_ARGON_ALLOW_DIRECT_LOGS=1` | 允許直接讀取 `.log` 與逐字稿檔。 |
| `AGY_ARGON_ALLOW_DIRECT_FASTPATH=1` | 停用直接編輯上限與「編輯後測試」阻擋。 |
| `AGY_ARGON_CIRCUIT_BREAKER=0` | 停用連續兩次失敗的斷路器。 |
| `AGY_ARGON_DEBUG=1` | 每次 hook 呼叫附加一行 JSON 至 `argon_orchestrator_debug.log`。 |
| `AGY_ARGON_APPEND_REPORT_FORMAT=0` | 不附加 REPORT FORMAT 區塊。 |
| `AGY_ARGON_ENFORCE_REPORT=0` | 不驗證子代理回報。 |
| `AGY_ARGON_ESCALATE=0` | 停用 scout 至 worker 的升級。 |
| `AGY_ARGON_WORKER_DOWNGRADE=0` | 停用 worker-lookup 降級。 |
| `AGY_ARGON_JUDGE_URL` | 啟用選用的 judge（後備為 `MITOSIS_JUDGE_URL`）。 |
| `AGY_ARGON_JUDGE_KEY`、`_MODEL`、`_TIMEOUT`、`_MIN_CONFIDENCE`、`_THRESHOLD` | judge 的金鑰、模型、逾時與信心設定。 |
| `AGY_ARGON_JUDGE_ALLOW_HTTP_KEY=1` | 允許以明文 http 傳送 judge 金鑰（預設關閉）。 |
| `AGY_ARGON_LOCK_TIMEOUT=<秒>` | 狀態鎖逾時，逾時即放行（預設 2）。 |
| `AGY_ANTIGRAVITY_SETTINGS` | 覆寫設定檔路徑（供測試使用）。 |
| `AGY_ARGON_GEMINI_CONFIG_DIR` | 覆寫 `~/.gemini/config`，供 mitosis 共存偵測使用（測試用）。 |

---

## 安裝

```bash
git clone https://github.com/ivanusto/argon-orchestrator.git ~/.gemini/config/plugins/argon-orchestrator
# 或由任何本機目錄安裝：
agy plugin install <dir>
agy plugin validate . --strict
```

安裝後請重新啟動 `agy`。可用 `agy plugin enable argon-orchestrator` 或 `agy plugin disable argon-orchestrator` 切換。

---

## 與 mitosis-orchestrator 的關係

[mitosis-orchestrator](https://github.com/ivanusto/mitosis-orchestrator) 是支援三個平台的後繼專案；Argon Orchestrator 僅支援 agy，較為輕量。兩者使用相同的 skill 與 agent 名稱，因此**只能啟用其中一個**：兩者同時啟用時，`SessionStart` 會發出警告。若要改用 Argon，請執行 `agy plugin disable mitosis-orchestrator`。

---

## 開發

```bash
python3 -m unittest discover -s tests -v
python3 scripts/routing-parity.py <mitosis_repo>
ruff check .
agy plugin validate . --strict
```

另見 [CONTRIBUTING.md](./CONTRIBUTING.md)、[SECURITY.md](./SECURITY.md) 與 [CHANGELOG.md](./CHANGELOG.md)。

## 授權

[MIT](./LICENSE)
