# 安全同步老師專案

本系統只讀取 `Kouzui-HUANG/biz-ai-video-course` 的 `main`，只推送到
`intilm888-afk/biz-ai-video-course` 的 `sync/upstream-main` 分支，建立 **Draft PR**。
不自動合併、不直接推送 main、不對老師專案寫入。

## 啟用與第一次測試

1. 先審核並手動合併這份「安裝同步系統」PR。這個 PR 不包含大量產生的 Codex Skills；
   第一次執行會另外建立同步 PR，方便分開檢查。
2. 你的 GitHub 專案 → **Actions**。Fork 若顯示停用工作流程，選擇啟用。
3. **Settings → Actions → General → Actions permissions**：允許 GitHub 自家的
   `actions/checkout`。工作流程固定 checkout 的 commit SHA。
4. 同頁 **Workflow permissions**：勾選 **Allow GitHub Actions to create and approve
   pull requests**。本系統只建立 PR，不呼叫 approve API。
   預設權限可保留唯讀，YAML 僅對同步 job 要求 `contents: write` 與 `pull-requests: write`。
   如果組織政策禁止這些權限，請由管理員依政策調整，程式不嘗試繞過限制。
5. **Actions → Safe upstream sync → Run workflow → Branch: main → Run workflow**。
   手動選其他分支會被安全略過。只在你自己的專案執行。
6. 查看執行紀錄及 **Summary** 的完整報告，再到 **Pull requests** 檢查 Draft PR。

排程為 `0 1 * * *`（UTC 01:00＝台灣每天上午 09:00）。GitHub 的排程可能延遲，
不是精準計時服務；排程只讀取預設分支，請維持預設分支為 main。
公開專案長期沒有活動時 GitHub 也可能停用排程，屆時到 Actions 重新啟用。

不需要 PAT、不需要新增 Secrets。使用每次執行的短效 `GITHUB_TOKEN`。
如果寫分支遭拒，檢查 Actions、組織及分支規則，勿為此開啟 force push 或解除 main 保護。
如果分支可寫但 GitHub 不允許建立 PR，Summary 會提供 compare URL，可手動建立 PR。
手動建立的 PR 不會被程式擅自接管；合併後下一輪可以正常建立 bot 的新 PR。

## 同步與保護方式

- 讀取 Git tree/blob，不 checkout、不執行老師的 workflow、安裝程式或生成腳本。
- 首次以 Git 共同祖先作為原始檔案比對基準；沒有共同祖先就失敗停止。
  透過 `.safe-sync/state.json` 記錄每個成功接收的來源 blob/mode 及 Codex Skill 指紋。
  紀錄只存在同步 PR，隨你手動合併進 main 才正式採用。
- 來源檔案只有本地與基準相同（或已與老師新版相同）才能更新。
  本地的新增、修改、刪除與老師不同時列為衝突，不做自動文字合併。
  沒有衝突的其他檔案仍可提出更新。
- Claude Skill 的本地修改衝突，以整個資料夾為單位停止；Codex Skill 也以整個
  資料夾為單位，比對所有檔案內容、可執行位元及新增／刪除檔案。
  同名的既有自訂 Codex Skill，即使內容相同，也不會在首次同步時自動認領。
- `.agents/skills/` 不存在時安全建立。每個 Skill 保留資源與腳本，Markdown 中的
  `.claude/skills/` 路徑改為 `.agents/skills/`。Python 腳本不改寫、不執行；
  `.claude/lib/` 是現有生成腳本共用模組，保留原路徑與三方比對保護。
- Codex `SKILL.md` 產生標準 YAML `name`、`description`，保留正文；捨棄 Claude 專用
  frontmatter 欄位。名稱以資料夾 slug 為準。
  單行 description 若因未加引號且含冒號而無法解析，只在 **Codex 副本** 加上正確引用；
  不明確、重複 key、缺少欄位或其他 YAML 錯誤停止該 Skill 轉換。
  轉換不保證 Claude 專屬工具的語意能在 Codex 原樣運作，請審查可執行 Skills 的工具呼叫。
- 老師刪除的任何原始檔案、整個 Skill 都先保留、列報，絕不自動刪除。
  老師刪除 Codex Skill 的資源檔案時，整個 Codex Skill 暫停更新並列出需審核的資源。
- **硬性保護**：`.github/`、`.agents/`、`.safe-sync/`、`scripts/`、`tests/`、
  `docs/`、`AGENTS.md`、`CLAUDE.md`、`.gitignore`、`.gitattributes`。
  `.agents/skills/` 只有受指紋保護的轉換程式能寫入，不能由上游直接匯入。
  隱藏設定預設也不匯入，只有 `.claude/skills/` 和 `.claude/lib/` 的非隱藏資源例外。
- 額外保護規則在 `.safe-sync/config.json`：設定檔、環境檔、金鑰、套件清單、
  lockfiles、`personal/`、`custom/`、`uploads/`、`outputs/` 等。
  自訂目錄請在首次同步前加入 `protected` glob 規則；硬性保護不受此設定刪除影響。
  普通未列入保護的本地修改仍有基準比對防護。
- 拒絕匯入 symlink/submodule，拒絕透過本地 symlink 寫入，也不覆蓋未追蹤的同名檔案。

## PR 重用與沒有更新時的行為

同一時間只執行一輪。重用固定同步分支及 bot 建立、帶系統標記的未合併 PR，
先正常 merge main，再疊加更新，保留 PR 裡的人工作業。
執行的程式先從可信 main 複製到 runner 暫存區，不執行 PR 分支內修改過的程式。
推送只使用一般 push，若遠端在過程中改變會被 Git 拒絕，留待下次重試。

main 與 PR 分支發生 merge 衝突時，列出檔名、abort 本次 merge 並停止，不推送。
請自行在同步 PR 解決衝突後再執行。若分支屬於他人、PR 被關閉但沒有合併，
或有未受管理的同名分支，會停止供你檢查；不重複建立 PR。
確認不再需要已關閉 PR 的分支後，可自行刪除遠端同步分支，再重新執行。

首次即使老師沒有新 commit，也會提出安全初始化 Codex Skills 和同步紀錄的 PR。
之後沒有實質差異不建立 commit／空白 PR。成功合併後可保留同步分支，下次會正常快轉；
也可使用 GitHub 的刪除已合併分支功能。

## 審核、衝突及驗證

每次同步檢查 `.claude/skills/` 和 `.agents/skills/` 的每個直接子資料夾是否有
`SKILL.md`、YAML 是否可解析（含重複 key 檢查）、name/description 是否有效。
每個 Skill 預期是直接子資料夾，隱藏資料夾略過。

報告在 Actions Summary、PR 說明及 `.safe-sync/report.md`，列出原始檔案新增／修改／
保留的刪除、受保護項目、Codex Skills 新增／修改／上游刪除、轉換說明及衝突、驗證錯誤。
相同上游且沒有新的可套用內容時保留既有 PR 報告，避免每天因報告重算建立多餘 commit；
最新本地驗證結果仍列在 Actions log。報告有衝突或驗證錯誤仍可提交未衝突部分供審查，
**不表示所有 Skills 全部通過**。

目前上游 commit `58a82a940ff3bb0e56bcd217a01c82b01f7356b0` 有 46 個 Skills；
其中 8 份原始 SKILL.md 的單行 description 含未引用的 `: `，1 份名稱使用顯示名稱。
原始檔案維持老師版本並明確列出 9 項驗證問題；Codex 副本會安全正規化這些欄位。

審核步驟：

1. 檢查 **Files changed**，確認沒有覆蓋個人設定，沒有不應出現的檔案或機密。
2. 閱讀 `conflicts`、`validation_errors`、`conversion_notes` 及保留刪除清單。
3. 原始檔案衝突：在 PR 上手動整合需要的內容，程式不會替你接受老師覆蓋。
   不要盲目修改指紋或 baseline 來繞過保護；有需要採用的上游內容也可直接在 PR 手動編輯。
   自訂 Codex Skill 可保持原樣，或手動另取名稱後再執行。
4. 檢查產生的 Codex SKILL.md 和資源路徑；可執行 Skills 需另外在你允許的環境測試，
   同步工作流程不會執行這些 API／產生成本。
5. 確認後按 **Ready for review**，再手動 **Merge pull request**。不要啟用自動合併。
   驗證錯誤應先理解或修正，再決定是否合併其他安全部分。

GitHub 以 `GITHUB_TOKEN` 建立的 PR 通常不會觸發其他 push/PR 工作流程；
本系統的驗證在同步 job 本身執行，不依賴那些檢查被自動觸發。
如你的分支規則要求額外檢查，請使用專案核准的手動驗證途徑，不需新增 PAT。

## 本地驗證

```sh
python3 -m pip install -r scripts/safe_sync/requirements.txt
python3 -m unittest discover -s tests
python3 scripts/safe_sync/sync.py --validate-only
```

`--validate-only` 遇到原始 Skills 現存的 YAML／名稱問題會回傳非零，這是預期的可見訊號。
真正的同步只應在乾淨的專用分支／拋棄式 checkout 執行，勿在含未提交工作的目錄試跑。
安全測試全部使用暫存 Git repositories，不會修改你的 main。
