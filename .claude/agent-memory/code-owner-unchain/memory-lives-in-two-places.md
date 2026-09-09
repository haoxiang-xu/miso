---
name: memory-lives-in-two-places
description: 本 owner 的记忆分裂在 unchain 仓与 PuPu 仓两个 agent-memory 目录，harness 只加载 unchain 那份，旧条目在 PuPu 那份里
metadata:
  type: reference
---

`code-owner-unchain` 的记忆目前有两份，harness 只会把 unchain 仓那份的 `MEMORY.md` 注入上下文：

- **harness 加载的**（写这里）：`/Users/red/Desktop/GITRepo/unchain/.claude/agent-memory/code-owner-unchain/`
- **更早的、有真实内容但不会被自动加载**：`/Users/red/Desktop/GITRepo/PuPu/.claude/agent-memory/code-owner-unchain/`
  - `unchain-import-bootstrap-trap.md` — 仍然有效：要实跑 `store_owner=unchain`，必须先 import `unchain_adapter` 触发产品自带的 sys.path bootstrap
  - `unchain-evidence-must-cite-lock-revision.md` / `locked-revision-test-isolation-trap.md` / `lazy-import-defers-locked-pair-breakage.md` — 三条都已标"已废止"，是旧 Git-SHA lock 政策的事故考古；现行规则是 runtime manifest admission + 单一 wheel artifact continuity
- 还有一份更老的库侧知识散在 `code-owner-runtime/` 的记忆目录里（旧「擎」同时管两侧），charter 要求逐步只读考古并沉淀过来，**尚未开始**

**Why**: charter 声称本 owner 的记忆目录是空的新目录，实测不是 —— PuPu 那份 2026-08-14 就有 4 条 + 索引。2026-08-15 P-0000-0007 立案时发现这个分裂；若只看 harness 注入的索引会误判"没有任何历史"，重复踩已经记录过的坑。

**How to apply**: 需要历史时主动去读 PuPu 那份目录，别只信注入的索引。新条目写 unchain 这份（否则未来的自己看不见）。哪天两份合并了，删掉这条。相关：[[case-boundary-lint-and-hashes]]
</content>
</invoke>
