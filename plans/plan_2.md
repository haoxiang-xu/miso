# Control Mouse Toolkit

## Summary
新增一个内置Control Mouse Toolkit，实现跨平台鼠标和键盘控制、截图和基础电脑操作功能。

## Goal
新增一个内置Control Mouse Toolkit，实现鼠标控制、截图和基础电脑操作功能，支持跨平台，具备鼠标移动、点击、拖拽、滚轮、键盘输入、截图等能力，确保操作安全并支持工作区路径管理。

## Constraints
- 必须支持跨平台；截图文件路径限制在工作区；所有控制操作需开启确认；依赖库限定在pyautogui、pynput、mss、Pillow；初期窗口控制功能简化实现。

## Steps
- [pending] 设计并新增Control Mouse Toolkit目录和toolkit.toml清单，符合内置工具包规范。
- [pending] 实现鼠标控制工具，包括移动、点击、拖拽和滚轮。
- [pending] 实现键盘输入和快捷键操作工具。
- [pending] 实现屏幕信息查询工具，如分辨率和当前鼠标坐标。
- [pending] 实现截图工具，支持全屏和指定区域截图，截图保存路径限制在工作区。
- [pending] 确保所有控制操作开启操作确认以增强安全性。
- [pending] 完成toolkit.toml和运行时注册一致性校验。
- [pending] 添加基础测试覆盖，确保核心功能稳定。
- [pending] 编写文档说明，包括依赖、使用示例及安全提示。

## Key Changes
- 新增Control Mouse Toolkit及相关工具和依赖管理。

## Public Interfaces
- 新增ControlMouseToolkit及其暴露的鼠标控制、键盘控制、截图等工具接口。

## Test Cases
- 编写基础的鼠标移动、点击、截图等功能的单元测试和集成测试。

## Assumptions
- 用户允许引入第三方依赖库；基础窗口控制功能可简化实现；工作区路径安全策略可有效防止越界写入。

## References
- pyautogui官方文档；pynput官方文档；mss截图库文档；Pillow图像处理库文档。

## Open Questions
- 是否允许引入第三方依赖库？基础窗口控制功能是否只做简化版本？是否需要支持键盘操作以外的其他输入设备？是否有特殊的截图格式或存储需求？是否需要支持更多高级窗口管理功能？
