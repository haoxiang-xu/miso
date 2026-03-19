# access_workspace_toolkit

`access_workspace_toolkit` 是一个只负责工作区文件、目录和行级编辑的内置工具包。

## 用法

```python
from miso import access_workspace_toolkit

tk = access_workspace_toolkit(workspace_root=".")
```

## 工具清单

- `read_file`
- `write_file`
- `create_file`
- `delete_file`
- `copy_file`
- `move_file`
- `file_exists`
- `list_directory`
- `create_directory`
- `search_text`
- `read_lines`
- `insert_lines`
- `replace_lines`
- `delete_lines`
- `copy_lines`
- `move_lines`
- `search_and_replace`

## 设计意图

- 只暴露工作区内的文件与文本编辑能力。
- 保留原有 tool 名称、参数和返回结构，避免影响前端展示和审批逻辑。
- 需要 terminal 时，与 `run_terminal_toolkit` 组合使用。
