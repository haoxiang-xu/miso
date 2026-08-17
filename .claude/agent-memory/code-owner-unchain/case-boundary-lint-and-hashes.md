---
name: case-boundary-lint-and-hashes
description: PuPu 的 Quorum boundary linter 位置、它能替你算 boundary object hash，以及 PS content hash 的一个非循环取值办法
metadata:
  type: reference
---

写 `proposal.md` / `PS-###` 时不要手写或猜 hash，也不要靠肉眼核对 BC/SEQ 字段是否齐全。

**linter**: `/Users/red/Desktop/GITRepo/PuPu/.claude/skills/case/boundary_lint.py <case-dir> [--phase ruling|acceptance]`。
它是 ruling 门禁的事实标准，会一次性报出缺字段、AC ref 不存在、SEQ 单元格格式错、ID 重复、case.md 与 proposal 的 BC/SEQ 集合不一致等问题。首稿写完就跑一次，剩下的 ERROR 应该只有"等交棒 / 等候选冻结 / 等 Speaker 同步 case.md"这三类。

**boundary object hash 可以直接算**，不要编：

```python
sys.path.insert(0, "/Users/red/Desktop/GITRepo/PuPu/.claude/skills/case")
from tools.quorum_lint import lint as L
sections, _ = L._sections(proposal_text)
bc  = {k: v for k, v in sections.items() if k.startswith("BC-")}
seq = {k: v for k, v in sections.items() if k.startswith("SEQ-")}
L._boundary_object_hash(bc, seq)   # 与 linter 的校验值逐字一致
```

**PS content hash 的算法法典没有钉死**，linter 只做 `^sha256:[0-9a-f]{64}$` 格式检查。直接对整个文件取 hash 是循环的（hash 要写进文件）。我用的非循环取法：**对 `### PS-<n>` 标题行之前的全部字节取 SHA-256**，并在 PS 块和 record 事件里写明这个推导方式。好处是后续只改 PS 块内的 hash 行不会让它失效，任何人都能复算。

**Why**: P-0000-0007-2026-0815 首稿时发现 templates.md 给的是示例占位 hash（`sha256:aaaa…`），照抄会被 linter 判无效；而 `boundary_revision_set` 要求两个真实 64-hex SHA-256 的 exact pair，候选未构建时根本拿不到，只能写 `PENDING_CANDIDATE_FREEZE` 并把推导规则写进"送裁前仍缺"。

**How to apply**: 任何要写 BC 的 proposal，顺序是 —— 先写全文（hash 位置留占位）→ 跑上面的脚本拿两个 hash → 填回去 → 跑 linter → 把剩余 ERROR 逐条对应到"待交棒/待冻结/待 Speaker"，不能对应上的才是真缺陷。相关：[[quorum-lead-owner-blank-discipline]]

## HS scope 是时间冻结的：集成返回件时不要给它新增 AC 编号

交棒返回件常常会建议"新加一条 AC-0NN"。**照做会当场打断该 owner 自己的确认。** HS 的 scope 冻结在 HANDOFF 事件那一刻；linter 会算 `requirement.criteria` = 由该 HS 确认的每个 BC/SEQ 的 positive ∪ negative acceptance，要求它是 scope 里 AC 引用的子集。返回件里新诞生的编号不在冻结 scope 内 → `confirmation handoff HS-00N scope does not cover responsibility criteria` → 那个 owner 的确认失效，只能再开一棒。

**规避办法**：把新验收的正文**逐字**折进一条已在 scope 内的 AC 作为子例，不给独立编号。往往返回件自己就是这么组织的（"作为 AC-0NN 的引用项，不重复计数"），只是顺手也建议了个新号。新编号留给 LEAD 自己确认的对象——LEAD 确认不受任何 HS scope 约束。

**Why**: 2026-08-15 P-0000-0007 集成 HS-001 时实际踩到。runtime 建议新设 AC-016 给 BC-004 producer 自证，照搬后 linter 立刻报 scope 不覆盖；折成 AC-011 子例 6 后消失，且 producer 侧仍拿到一条有正文的自证 AC，原本要解决的 defect 照样解决，还省了一棒。

## 「严格 consumer / 禁共用 helper」是位置相对的，写进 AC 时必须限定范围

跨边界契约测试的通则「不得 import 被测实现、必须就地重声明」**只在被测对象不是那个实现时成立**。同一条 AC 如果有多个取证位置，其中一个位置的被测对象**就是**那份实现（例如给解析器本身写防护），那里必须**调用真实导出**，否则测的是测试自己写的副本、与生产代码彻底脱钩、防护归零。

写 AC 时把这条规则**逐位置限定**（「本规则仅适用于位置 (E)」+「位置 (F) 适用相反规则」），不要写成全 AC 通则。否则拥有那份实现的 owner 会正确地把它列为拒绝条件。

**Why**: P-0000-0007 里我在位置 (E)（Electron 载体）写了禁 import，shared-arteries owner 集成时指出它若套到位置 (F)（反解器自己的测试）会让整份交付作废——这是他 AGREE 的唯一条件，且他是对的。判据不是「谁在测」而是「被测对象是不是那份实现」。

## 别把「取证方式」写进 SEQ 的矩阵单元格

`first use` / `retry` / `rollback` 这些单元格的 detail 必须是**干净的 AC 引用列表**。我在 `AC-014` 后面跟了一句括号说明取证方法，linter 报 `field 'rollback' must contain at least one exact same-case AC ref`。解决办法是给 SEQ 另加一个自定义字段（我用的是 `cell 取证方式`）承载说明，单元格本身只留 `REQUIRED | AC-###`。同一条纪律适用于 `consumer owner` 这类 owner 字段。

## 写「某某测试里加一条断言」之前，先确认那个测试拿得到它要比的东西

我在 PS-002 写了「AC-011 的 pytest 增加一条 session 级断言，比对进程内 manifest digest 与 evidence 文件」，devtools 集成时指出该断言**物理上不可写** —— CI 与本地门的 pytest 步都只注入 `PYTHONPATH`，不注入 evidence 路径（同一 workflow 的其他步骤都注入了，唯独这步没有）。**跨 owner 写验收义务时，"谁来跑、跑的时候环境里有什么"和"断言内容"同等重要**，前者往往在别人的边界里。核实成本是 grep 一次 workflow 的 env 块。

## BC 只有两个 owner confirmation 字段 —— 多跳契约必然溢出

`BOUNDARY_V1` 的 schema 里 BC 只有 `producer owner confirmation` 与 `consumer owner confirmation` 两个槽。**一条跨三跳以上的传输链（如 sidecar → Electron main → renderer bridge → 分类器）会有三到四个真实 owner，槽位必然不够。**多出来的 owner 只能以 contribution + RS stance 承载，进不了 linter 的机械校验。

处理方式（P-0000-0007 用的）：把多余 owner 写进 `consumer` / `admission details` 的**散文字段**，开 HS 拿到真人的知情与同意，并在 SUMMARY 里把「N 方义务、两个字段」显名为 coverage gap 交 Chief 裁量。**不要试图拆 BC 来补槽位** —— 新 BC 编号会溢出所有已冻结的 HS scope，把已拿到的确认全部打掉。

**`consumer owner` / `producer owner` / `owner` 字段必须是干净的单个 owner id。** 我一度在 `consumer owner` 后面加了一句解释归属的散文，linter 立刻把整串当成 owner 名字，报 `confirmation handoff HS-002 is reused for different owners` 和 `targets X, expected exactly [...]`。解释放到 `consumer` 字段里去，那个是散文。

**同一案里第二次踩到，而且这次是被告知"没问题"。** 集成 HS-002 时 chat-core 建议拆独立负向 AC，书记员转述时明确写「前提这次具备，不会溢出已冻结 scope」。实测不然：HS-002 的 scope 同样冻结在它的 HANDOFF 事件上，加编号立刻报 `scope does not cover responsibility criteria ['AC-012','AC-017']`。**别信"这次没问题"的转述，花三十秒在临时副本上加一个假编号跑一遍 linter 再决定**——`cp proposal.md /tmp/... && 改 && lint && cp 回来`。替代方案是给 SEQ 加一个 `cell 到子例映射` 字段，逐格指向同一条 AC 的具体子例，逐格可追踪性照样拿到，零新编号。
</content>
</invoke>
