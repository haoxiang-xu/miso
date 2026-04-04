# Documentation Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all documentation from `.claude/commands/`, `.github/skills/`, and `docs/` into a unified `docs/` folder with bilingual support, following PuPu's single-source-of-truth pattern.

**Architecture:** All documentation lives in `docs/en/` and `docs/zh-CN/` as the single source of truth. `.claude/commands/` becomes thin stubs pointing to docs. `.github/skills/` stubs stay as-is (already correct). Fix stale "miso" naming throughout. Verify every doc claim against actual source code.

**Tech Stack:** Markdown, Python source verification, git

---

## Current State Analysis

| Location | Files | Status |
|----------|-------|--------|
| `docs/en/skills/` | 7 skill chapters | Full English docs, some reference "miso" |
| `docs/en/api/` | 6 API references | Full English docs, need verification |
| `docs/en/appendix/` | 4 appendices | Need verification |
| `docs/zh-CN/skills/` | 7 skill chapters | **SUMMARIES ONLY (~2KB each vs ~12KB English)** |
| `docs/zh-CN/api/` | 6 API references | Full Chinese translations |
| `docs/zh-CN/appendix/` | 4 appendices | Need verification |
| `.claude/commands/` | 9 command guides | Full content, should be in docs/ |
| `.github/skills/` | 7 stub files | Already redirect to docs/ |
| `.claude/CLAUDE.md` | 1 file | Duplicates docs content, should be slim |
| `AGENTS.md` | 1 file | Mirrors CLAUDE.md, should be slim |
| `docs/README.en.md` | 1 index | Missing guides section, says "Miso" |
| `docs/README.zh-CN.md` | 1 index | Missing guides section, says "Miso" |

## Target File Structure

```
docs/
├── README.en.md              (UPDATE: add guides, fix miso→unchain)
├── README.zh-CN.md           (UPDATE: add guides, fix miso→unchain)
├── new_models.md             (VERIFY)
├── en/
│   ├── skills/               (EXISTING: verify + fix naming)
│   │   ├── architecture-overview.md
│   │   ├── agent-and-team.md
│   │   ├── runtime-engine.md
│   │   ├── tool-system-patterns.md
│   │   ├── memory-system.md
│   │   ├── creating-builtin-toolkits.md
│   │   └── testing-conventions.md
│   ├── api/                  (EXISTING: verify + fix naming)
│   │   ├── agents.md
│   │   ├── runtime.md
│   │   ├── tools.md
│   │   ├── toolkits.md
│   │   ├── memory.md
│   │   └── input-workspace-schemas.md
│   ├── appendix/             (EXISTING: verify + fix naming)
│   │   ├── class-index.md
│   │   ├── export-index.md
│   │   ├── glossary.md
│   │   └── return-shapes-and-state-flow.md
│   └── guides/               (NEW: migrated from .claude/commands/)
│       ├── add-harness.md
│       ├── add-model.md
│       ├── add-provider.md
│       ├── add-tool.md
│       ├── add-toolkit.md
│       ├── debug-stream.md
│       ├── explore.md
│       ├── sync-packages.md
│       └── test.md
├── zh-CN/
│   ├── skills/               (EXISTING: expand summaries to full translations)
│   ├── api/                  (EXISTING: verify + fix naming)
│   ├── appendix/             (EXISTING: verify + fix naming)
│   └── guides/               (NEW: Chinese translations)
│       ├── add-harness.md
│       ├── add-model.md
│       ├── add-provider.md
│       ├── add-tool.md
│       ├── add-toolkit.md
│       ├── debug-stream.md
│       ├── explore.md
│       ├── sync-packages.md
│       └── test.md
```

Also modify:
```
.claude/CLAUDE.md             (SLIM: remove inline guides, point to docs/)
.claude/commands/*.md         (SLIM: one-line redirect to docs/en/guides/)
AGENTS.md                     (SLIM: remove inline guides, point to docs/)
```

---

### Task 1: Create English Guides (`docs/en/guides/`)

Convert the 9 `.claude/commands/` files into proper human-readable documentation under `docs/en/guides/`. While writing each guide, verify that every file path and code pattern mentioned actually exists in the current codebase.

**Files:**
- Create: `docs/en/guides/add-harness.md`
- Create: `docs/en/guides/add-model.md`
- Create: `docs/en/guides/add-provider.md`
- Create: `docs/en/guides/add-tool.md`
- Create: `docs/en/guides/add-toolkit.md`
- Create: `docs/en/guides/debug-stream.md`
- Create: `docs/en/guides/explore.md`
- Create: `docs/en/guides/sync-packages.md`
- Create: `docs/en/guides/test.md`
- Reference: `.claude/commands/*.md` (source content)

- [ ] **Step 1: Verify source paths used in commands**

Before writing any guides, verify that the key source paths referenced in the commands still exist:

```bash
cd /Users/red/Desktop/GITRepo/unchain
# Kernel / harness paths
ls src/unchain/kernel/harness.py src/unchain/kernel/loop.py src/unchain/kernel/delta.py src/unchain/kernel/state.py
# Memory paths
ls src/unchain/memory/bootstrap.py src/unchain/memory/runtime.py src/unchain/memory/manager.py
# Tool paths
ls src/unchain/tools/execution.py src/unchain/tools/tool.py src/unchain/tools/messages.py
# Provider paths
ls src/unchain/providers/model_io.py src/unchain/agent/model_io.py
# Runtime resources
ls src/unchain/runtime/resources/model_capabilities.json src/unchain/runtime/resources/model_default_payloads.json
# Toolkit paths
ls src/unchain/toolkits/builtin/workspace/ src/unchain/toolkits/builtin/ask_user/
# Schema paths
ls src/unchain/schemas/models.py
# Test paths
ls tests/test_kernel_core.py 2>/dev/null || ls tests/test_kernel_runtime.py
```

Record any missing files. The guides must only reference paths that actually exist.

- [ ] **Step 2: Write `docs/en/guides/add-harness.md`**

Convert `.claude/commands/add-harness.md` into a proper guide. Remove `$ARGUMENTS` placeholders. Add section headers, prerequisites, and verification steps. Reference only verified file paths from Step 1.

Format:
```markdown
# Adding a New Kernel Harness

Guide for creating a new runtime harness to hook into the unchain kernel execution loop.

## Prerequisites
- Familiarity with the kernel loop execution model
- Understanding of HarnessDelta pattern

## Reference Files
| File | Role |
|------|------|
| `src/unchain/kernel/harness.py` | Harness protocol definition |
| `src/unchain/kernel/loop.py` | Phase dispatch logic |
| `src/unchain/kernel/delta.py` | HarnessDelta operations |

## Example Harnesses
- **Simple**: `src/unchain/memory/bootstrap.py` (bootstrap phase)
- **Complex**: `src/unchain/tools/execution.py` (on_tool_call + after_tool_batch)
- **Optimizer**: `src/unchain/optimizers/last_n.py` (before_model)

## Steps
1. Define the harness class extending `BaseRuntimeHarness`
2. Set `name`, `phases`, and `order`
3. Implement `build_delta()` returning `HarnessDelta | None`
4. Register in the appropriate module or builder
5. Write tests

## Template
[include the Python code template from the command file]

## Testing
[include test command and patterns]
```

- [ ] **Step 3: Write remaining 8 English guide files**

Apply the same pattern to create:
- `docs/en/guides/add-model.md` — from `.claude/commands/add-model.md`
- `docs/en/guides/add-provider.md` — from `.claude/commands/add-provider.md`
- `docs/en/guides/add-tool.md` — from `.claude/commands/add-tool.md`
- `docs/en/guides/add-toolkit.md` — from `.claude/commands/add-toolkit.md`
- `docs/en/guides/debug-stream.md` — from `.claude/commands/debug-stream.md`
- `docs/en/guides/explore.md` — from `.claude/commands/explore.md`
- `docs/en/guides/sync-packages.md` — from `.claude/commands/sync-packages.md`
- `docs/en/guides/test.md` — from `.claude/commands/test.md`

**Important for each file:**
- Remove all `$ARGUMENTS` and `## Arguments` sections
- Convert imperative Claude instructions to human-readable guide prose
- Verify every file path mentioned exists (from Step 1 results)
- Fix any "miso" references to "unchain" (e.g., `src/miso/` → `src/unchain/`)
- Add cross-links to related skill chapters and API references in `docs/en/`

The `debug-stream.md` guide references PuPu paths — keep those as-is since they're cross-project references.

- [ ] **Step 4: Commit English guides**

```bash
cd /Users/red/Desktop/GITRepo/unchain
git add docs/en/guides/
git commit -m "docs: add English guides migrated from .claude/commands"
```

---

### Task 2: Create Chinese Guides (`docs/zh-CN/guides/`)

Translate all 9 English guides into Chinese. Follow the same bilingual pattern used by existing `docs/zh-CN/api/` files (full translations, not summaries).

**Files:**
- Create: `docs/zh-CN/guides/add-harness.md`
- Create: `docs/zh-CN/guides/add-model.md`
- Create: `docs/zh-CN/guides/add-provider.md`
- Create: `docs/zh-CN/guides/add-tool.md`
- Create: `docs/zh-CN/guides/add-toolkit.md`
- Create: `docs/zh-CN/guides/debug-stream.md`
- Create: `docs/zh-CN/guides/explore.md`
- Create: `docs/zh-CN/guides/sync-packages.md`
- Create: `docs/zh-CN/guides/test.md`
- Reference: `docs/en/guides/*.md` (source for translation)

- [ ] **Step 1: Write all 9 Chinese guide files**

For each English guide in `docs/en/guides/`, create a Chinese translation in `docs/zh-CN/guides/` with:
- Chinese section headers (e.g., "# 添加新的 Kernel Harness")
- Chinese prose for descriptions and explanations
- **Keep code blocks, file paths, class names, and technical terms in English**
- Keep the same structure and section order as English version
- Cross-links should point to `../` zh-CN siblings

Translation reference for section headers:
| English | Chinese |
|---------|---------|
| Prerequisites | 前提条件 |
| Reference Files | 参考文件 |
| Example Harnesses | 示例 Harness |
| Steps | 步骤 |
| Template | 模板 |
| Testing | 测试 |
| Common Issues | 常见问题 |

- [ ] **Step 2: Commit Chinese guides**

```bash
cd /Users/red/Desktop/GITRepo/unchain
git add docs/zh-CN/guides/
git commit -m "docs: add Chinese guides (translations of en/guides)"
```

---

### Task 3: Slim Down `.claude/commands/` to Stubs

Replace the 9 command files with thin stubs that point to the canonical docs. The stubs must still work as Claude Code custom commands.

**Files:**
- Modify: `.claude/commands/add-harness.md`
- Modify: `.claude/commands/add-model.md`
- Modify: `.claude/commands/add-provider.md`
- Modify: `.claude/commands/add-tool.md`
- Modify: `.claude/commands/add-toolkit.md`
- Modify: `.claude/commands/debug-stream.md`
- Modify: `.claude/commands/explore.md`
- Modify: `.claude/commands/sync-packages.md`
- Modify: `.claude/commands/test.md`

- [ ] **Step 1: Replace all 9 command files with stubs**

Each command file becomes a stub with this pattern:

```markdown
# Add a New Kernel Harness

Read the canonical guide at `docs/en/guides/add-harness.md` and follow it step by step.

## Arguments
- $ARGUMENTS: Harness name, phase(s), and description
```

Apply this pattern to all 9 files. Keep the `$ARGUMENTS` line so they still work as Claude commands, but all real content now lives in `docs/en/guides/`.

- [ ] **Step 2: Commit command stubs**

```bash
cd /Users/red/Desktop/GITRepo/unchain
git add .claude/commands/
git commit -m "docs: slim .claude/commands to stubs pointing to docs/en/guides"
```

---

### Task 4: Fix "miso" Naming Across All Docs

The docs still reference "miso" in many places. The project is now called "unchain". Fix all stale naming.

**Files:**
- Modify: `docs/README.en.md`
- Modify: `docs/README.zh-CN.md`
- Modify: `docs/en/skills/*.md` (7 files — check each)
- Modify: `docs/zh-CN/skills/*.md` (7 files — check each)
- Modify: `docs/en/api/*.md` (6 files — check each)
- Modify: `docs/zh-CN/api/*.md` (6 files — check each)
- Modify: `docs/en/appendix/*.md` (4 files — check each)
- Modify: `docs/zh-CN/appendix/*.md` (4 files — check each)
- Modify: `docs/new_models.md`

- [ ] **Step 1: Find all "miso" references in docs**

```bash
cd /Users/red/Desktop/GITRepo/unchain
grep -rn "miso" docs/ --include="*.md" | grep -v "superpowers/"
```

This produces the full list of lines to fix.

- [ ] **Step 2: Fix README index files**

In `docs/README.en.md`:
- Change "Miso Encyclopedia" → "Unchain Encyclopedia"
- Change "`miso`" → "`unchain`"
- Change "`src/miso`" → "`src/unchain`"

In `docs/README.zh-CN.md`:
- Change "Miso 百科文档" → "Unchain 百科文档"
- Change "`miso`" → "`unchain`"
- Change "`src/miso`" → "`src/unchain`"

- [ ] **Step 3: Fix miso references in en/skills/ and zh-CN/skills/**

For each of the 14 skills files, replace:
- `miso.tools` → `unchain.tools`
- `miso.runtime` → `unchain.runtime`
- `miso.agents` → `unchain.agents`
- `miso.memory` → `unchain.memory`
- `src/miso/` → `src/unchain/`
- Any other `miso` references

Verify each replacement makes sense (some may be intentional legacy references).

- [ ] **Step 4: Fix miso references in en/api/ and zh-CN/api/**

Same pattern for the 12 API reference files. These are large files (10-43KB each), so use targeted search-and-replace.

- [ ] **Step 5: Fix miso references in appendix/ files and new_models.md**

Same pattern for the 8 appendix files and `new_models.md`.

- [ ] **Step 6: Verify no stale miso references remain**

```bash
cd /Users/red/Desktop/GITRepo/unchain
grep -rn "miso" docs/ --include="*.md" | grep -v "superpowers/"
```

Expected: zero results (or only intentional legacy references, clearly marked).

- [ ] **Step 7: Commit naming fix**

```bash
cd /Users/red/Desktop/GITRepo/unchain
git add docs/
git commit -m "docs: rename miso → unchain across all documentation"
```

---

### Task 5: Verify and Expand Chinese Skills Chapters

The Chinese skills chapters are currently condensed summaries (~2KB each). The English versions are full chapters (~12KB each). Expand Chinese to match English structure while verifying English accuracy against actual code.

**Files:**
- Modify: `docs/zh-CN/skills/architecture-overview.md` (currently 2297 bytes, en: 10656 bytes)
- Modify: `docs/zh-CN/skills/agent-and-team.md` (currently 4180 bytes, en: 14172 bytes)
- Modify: `docs/zh-CN/skills/runtime-engine.md` (currently 4684 bytes, en: 17405 bytes)
- Modify: `docs/zh-CN/skills/tool-system-patterns.md` (currently 2068 bytes, en: 13575 bytes)
- Modify: `docs/zh-CN/skills/memory-system.md` (currently 2086 bytes, en: 13230 bytes)
- Modify: `docs/zh-CN/skills/creating-builtin-toolkits.md` (currently 2029 bytes, en: 12946 bytes)
- Modify: `docs/zh-CN/skills/testing-conventions.md` (currently 2109 bytes, en: 12899 bytes)
- Reference: `docs/en/skills/*.md` (English source for translation)
- Reference: `src/unchain/` (actual code for verification)

- [ ] **Step 1: Verify and expand `architecture-overview.md`**

1. Read `docs/en/skills/architecture-overview.md`
2. Read the actual source files it references:
   - `src/unchain/__init__.py`
   - `src/unchain/kernel/loop.py`
   - `src/unchain/agent/agent.py`
   - `src/unchain/tools/tool.py`
3. Verify: Do the class names, method signatures, and import paths in the doc match the actual code?
4. Fix any discrepancies in the English version
5. Write the full Chinese translation to `docs/zh-CN/skills/architecture-overview.md`

The Chinese version must match the English in:
- Same section structure and ordering
- Same code blocks and file paths (keep in English)
- Same class/method references
- Chinese prose for all explanatory text

- [ ] **Step 2: Verify and expand `agent-and-team.md`**

Same process:
1. Read English version
2. Read referenced source files (`src/unchain/agent/agent.py`, `builder.py`, `spec.py`, `modules/`)
3. Verify class names, constructors, method signatures
4. Fix English discrepancies
5. Write full Chinese translation

- [ ] **Step 3: Verify and expand `runtime-engine.md`**

Same process with `src/unchain/runtime/engine.py`, `src/unchain/kernel/loop.py`

- [ ] **Step 4: Verify and expand `tool-system-patterns.md`**

Same process with `src/unchain/tools/tool.py`, `execution.py`, `confirmation.py`, `decorators.py`

- [ ] **Step 5: Verify and expand `memory-system.md`**

Same process with `src/unchain/memory/manager.py`, `runtime.py`, `config.py`, `stores.py`, `strategies.py`

- [ ] **Step 6: Verify and expand `creating-builtin-toolkits.md`**

Same process with `src/unchain/toolkits/builtin/*/`, `toolkit.toml` manifests

- [ ] **Step 7: Verify and expand `testing-conventions.md`**

Same process with `tests/` directory, test patterns, fake client usage

- [ ] **Step 8: Commit expanded Chinese skills**

```bash
cd /Users/red/Desktop/GITRepo/unchain
git add docs/en/skills/ docs/zh-CN/skills/
git commit -m "docs: expand Chinese skills chapters to full translations, fix English discrepancies"
```

---

### Task 6: Verify API References Against Actual Code

The API reference docs list specific classes, methods, and signatures. Verify they match the current source code.

**Files:**
- Verify+Fix: `docs/en/api/agents.md` against `src/unchain/agent/`
- Verify+Fix: `docs/en/api/runtime.md` against `src/unchain/providers/`, `src/unchain/runtime/`
- Verify+Fix: `docs/en/api/tools.md` against `src/unchain/tools/`
- Verify+Fix: `docs/en/api/toolkits.md` against `src/unchain/toolkits/`
- Verify+Fix: `docs/en/api/memory.md` against `src/unchain/memory/`
- Verify+Fix: `docs/en/api/input-workspace-schemas.md` against `src/unchain/input/`, `src/unchain/workspace/`, `src/unchain/schemas/`
- Mirror fixes to: `docs/zh-CN/api/*.md`

- [ ] **Step 1: Verify `agents.md`**

1. Read `docs/en/api/agents.md`
2. Read `src/unchain/agent/agent.py`, `builder.py`, `spec.py`
3. Check: Do documented class names, constructors, and public methods exist?
4. Check: Are documented parameters and types correct?
5. Fix any discrepancies in both English and Chinese versions

- [ ] **Step 2: Verify `runtime.md`**

Same process with `src/unchain/providers/model_io.py`, `src/unchain/runtime/`

- [ ] **Step 3: Verify `tools.md`**

Same process with `src/unchain/tools/tool.py`, `toolkit.py`, `execution.py`, `registry.py`

- [ ] **Step 4: Verify `toolkits.md`**

Same process with `src/unchain/toolkits/builtin/*/`, all toolkit classes

- [ ] **Step 5: Verify `memory.md`**

Same process with `src/unchain/memory/manager.py`, `runtime.py`, stores, strategies

- [ ] **Step 6: Verify `input-workspace-schemas.md`**

Same process with `src/unchain/input/`, `src/unchain/workspace/`, `src/unchain/schemas/`

- [ ] **Step 7: Commit API reference fixes**

```bash
cd /Users/red/Desktop/GITRepo/unchain
git add docs/en/api/ docs/zh-CN/api/
git commit -m "docs: verify and fix API references against actual source code"
```

---

### Task 7: Verify Appendices and Fix Cross-References

**Files:**
- Verify+Fix: `docs/en/appendix/class-index.md` — all 55+ classes still exist?
- Verify+Fix: `docs/en/appendix/export-index.md` — all exports still valid?
- Verify+Fix: `docs/en/appendix/glossary.md`
- Verify+Fix: `docs/en/appendix/return-shapes-and-state-flow.md`
- Mirror fixes to: `docs/zh-CN/appendix/*.md`

- [ ] **Step 1: Verify class-index.md**

1. Read `docs/en/appendix/class-index.md`
2. For each listed class, grep the source to confirm it exists:
   ```bash
   cd /Users/red/Desktop/GITRepo/unchain
   grep -rn "class ClassName" src/unchain/
   ```
3. Remove any classes that no longer exist
4. Add any new classes not yet indexed
5. Apply same changes to Chinese version

- [ ] **Step 2: Verify export-index.md**

1. Read `docs/en/appendix/export-index.md`
2. Read each `__init__.py` to confirm exports:
   ```bash
   grep -rn "__all__" src/unchain/ --include="__init__.py"
   ```
3. Fix discrepancies in both languages

- [ ] **Step 3: Verify glossary and return-shapes**

Quick check that terms and data shapes are still accurate.

- [ ] **Step 4: Commit appendix fixes**

```bash
cd /Users/red/Desktop/GITRepo/unchain
git add docs/en/appendix/ docs/zh-CN/appendix/
git commit -m "docs: verify and fix appendices against actual source"
```

---

### Task 8: Update README Indexes and Entry Points

Update the documentation index files to include the new guides section. Slim down CLAUDE.md and AGENTS.md to point to docs.

**Files:**
- Modify: `docs/README.en.md`
- Modify: `docs/README.zh-CN.md`
- Modify: `.claude/CLAUDE.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update `docs/README.en.md`**

Add a "Guides" section after "Skills chapters":

```markdown
## Guides

- [Adding a Kernel Harness](en/guides/add-harness.md)
- [Adding a New Model](en/guides/add-model.md)
- [Adding a New Provider](en/guides/add-provider.md)
- [Adding a New Tool](en/guides/add-tool.md)
- [Adding a New Toolkit](en/guides/add-toolkit.md)
- [Debugging Stream Issues](en/guides/debug-stream.md)
- [Exploring Architecture](en/guides/explore.md)
- [Syncing Packages](en/guides/sync-packages.md)
- [Running Tests](en/guides/test.md)
```

Also update the header from "Miso" to "Unchain" and fix any remaining miso references.

- [ ] **Step 2: Update `docs/README.zh-CN.md`**

Add corresponding Chinese guides section:

```markdown
## 操作指南

- [添加 Kernel Harness](zh-CN/guides/add-harness.md)
- [添加新模型](zh-CN/guides/add-model.md)
- [添加新 Provider](zh-CN/guides/add-provider.md)
- [添加新工具](zh-CN/guides/add-tool.md)
- [添加新 Toolkit](zh-CN/guides/add-toolkit.md)
- [调试流式问题](zh-CN/guides/debug-stream.md)
- [探索架构](zh-CN/guides/explore.md)
- [同步包](zh-CN/guides/sync-packages.md)
- [运行测试](zh-CN/guides/test.md)
```

- [ ] **Step 3: Slim down `.claude/CLAUDE.md`**

Keep only:
1. Project Overview (2-3 lines)
2. Key Architecture diagram (the execution flow ASCII)
3. A "Documentation" section pointing to `docs/README.en.md`
4. Testing command (1 line)
5. Key Files Reference table

Remove:
- "Adding a New Model" section (now in docs/en/guides/add-model.md)
- "Adding a New Provider" section (now in docs/en/guides/add-provider.md)
- "Adding a New Toolkit" section (now in docs/en/guides/add-toolkit.md)
- Any other content that duplicates docs/

Add a prominent link:
```markdown
## Documentation
Full documentation: [English](docs/README.en.md) | [中文](docs/README.zh-CN.md)
```

- [ ] **Step 4: Slim down `AGENTS.md`**

Mirror the same changes as CLAUDE.md. AGENTS.md serves Codex — keep the same slim structure.

- [ ] **Step 5: Verify `.github/skills/` stubs are correct**

Read each of the 7 `.github/skills/*.md` files and verify:
- The redirect path to `docs/en/skills/` is correct
- The canonical file at that path exists
- The description matches

No changes expected — these are already correct stubs.

- [ ] **Step 6: Commit index and entry point updates**

```bash
cd /Users/red/Desktop/GITRepo/unchain
git add docs/README.en.md docs/README.zh-CN.md .claude/CLAUDE.md AGENTS.md
git commit -m "docs: update indexes, slim CLAUDE.md and AGENTS.md to point to docs/"
```

---

## Verification Summary

After all tasks, run a final check:

```bash
cd /Users/red/Desktop/GITRepo/unchain

# 1. No stale miso references
grep -rn "miso" docs/ --include="*.md" | grep -v superpowers/

# 2. All guide files exist in both languages
ls docs/en/guides/*.md docs/zh-CN/guides/*.md

# 3. All .claude/commands are stubs (< 500 bytes each)
wc -c .claude/commands/*.md

# 4. README indexes link to all docs
grep -c "\[" docs/README.en.md docs/README.zh-CN.md

# 5. No broken internal links
grep -roh "\](.*\.md)" docs/ | sort -u | while read link; do
  path=$(echo "$link" | sed 's/\](\(.*\))/\1/')
  # check relative resolution
done
```
