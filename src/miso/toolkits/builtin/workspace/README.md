# Workspace Toolkit

`WorkspaceToolkit` 提供工作区内的文件、目录和行级编辑能力。

## 用法

```python
from miso.toolkits import WorkspaceToolkit

tk = WorkspaceToolkit(workspace_root=".")
```

## 包含的能力

- 文件读取、写入、创建、删除、复制、移动
- 目录列出与创建
- 行级读取、插入、替换、删除、复制、移动
- 文本搜索与批量替换
- workspace pin 上下文管理

## 设计约束

- 只操作 `workspace_root` 范围内的路径
- 不包含 terminal 能力
- 需要 shell 时，应与 `TerminalToolkit` 组合使用
