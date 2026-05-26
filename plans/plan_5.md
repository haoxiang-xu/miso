# Control Mouse Toolkit

## Summary
新增control_mouse内置工具包，实现鼠标基本操控、键盘输入、屏幕截图和简单应用控制，支持跨平台，依赖pyautogui等库，所有敏感操作需用户确认。

## Goal
新增一个内置工具包 control_mouse，具备鼠标操控、截图和电脑操作能力，支持跨平台，实现鼠标移动、点击、拖拽、滚轮，键盘输入，屏幕截图，安全确认机制，满足基础电脑自动化需求。

## Constraints
- 跨平台，安全路径操作，用户确认机制。

## Steps
- [pending] 设计control_mouse工具包目录结构和toolkit.toml清单，完成工具包注册。
- [pending] 实现鼠标控制工具，包括鼠标移动、点击、拖拽和滚轮操作。
- [pending] 实现键盘输入工具，支持输入文本和快捷键操作。
- [pending] 实现屏幕信息工具，获取屏幕分辨率和当前鼠标坐标。
- [pending] 实现截图工具，支持全屏截图和区域截图，确保截图文件写入工作区。
- [pending] 实现简单应用控制功能，如打开应用和切换窗口（基础版本）。
- [pending] 为所有控制操作添加用户确认机制，防止误操作。
- [pending] 完成工具包依赖管理，确保引入pyautogui、pynput、mss、Pillow等必要依赖。
- [pending] 添加基础测试用例，验证鼠标、键盘、截图功能正确。
- [pending] 编写工具包使用文档，说明依赖安装和操作方法。

## Key Changes
- 新增control_mouse工具包目录文件及配置，新增鼠标、键盘、截图相关工具代码，新增用户确认逻辑。

## Public Interfaces
- ControlMouseToolkit及其鼠标、键盘、截图、应用控制工具接口。

## Test Cases
- 测试鼠标移动、点击、拖拽、滚轮功能，测试键盘文本输入和快捷键，测试全屏及区域截图功能，测试简单应用打开和切换窗口功能。

## Assumptions
- 用户允许安装第三方依赖pyautogui及相关库，用户确认所有敏感操作。

## References
- pyautogui官方文档，pynput官方文档，mss官方文档，Pillow官方文档。

## Open Questions
- 是否确认允许安装第三方依赖？是否允许先实现基础应用控制功能，复杂窗口管理后续再做？
