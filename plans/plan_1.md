# Add control_mouse toolkit for mouse control, screenshot, and computer operation

## Summary
Implement a new builtin toolkit named control_mouse to enable mouse control, keyboard input, screen information retrieval, and screenshot functionality. Use third-party dependencies such as pyautogui, pynput, mss, and Pillow to support cross-platform operations. Ensure all screenshot outputs are saved within the workspace path for security. Require user confirmation for any control operations that affect the computer. Provide basic application/window control capabilities initially, with complex window management as a future enhancement.

## Goal
Implement a new builtin toolkit named control_mouse to provide mouse control, keyboard input, screen info, and screenshot capabilities with workspace-safe file handling and user confirmation for control actions.

## Constraints
- Cross-platform support, third-party dependencies, workspace path safety, user confirmation for control actions.

## Steps
- [pending] Create a new builtin toolkit directory src/unchain/toolkits/builtin/control_mouse/ and add toolkit.toml for registration.
- [pending] Implement ControlMouseToolkit class with registration and toolkit interface.
- [pending] Develop mouse control tools: move, click, drag, scroll.
- [pending] Develop keyboard input tools and shortcut key support.
- [pending] Implement screen information tools to get resolution and current mouse position.
- [pending] Implement screenshot tools for full screen and specific areas with output saved in workspace.
- [pending] Ensure all file outputs use _resolve_workspace_path() to enforce workspace safety.
- [pending] Add requires_confirmation flag for all control actions to prevent unintended operations.
- [pending] Add basic application/window control tools for opening and focusing applications.
- [pending] Write minimal smoke tests for the new toolkit functions.
- [pending] Update documentation to include usage and dependency information for the control_mouse toolkit.

## Key Changes
- Add control_mouse builtin toolkit with mouse, keyboard, screen info, and screenshot tools, including workspace-safe file handling and user confirmation.

## Public Interfaces
- New control_mouse toolkit with tools for mouse control, keyboard input, screen info, screenshot, and basic app/window control.

## Test Cases
- Smoke tests verifying mouse movement, clicking, keyboard input, screenshot capture, and file output within workspace.

## Assumptions
- User environment supports pyautogui and other dependencies. Basic app/window control is sufficient for initial release.

## References
- pyautogui documentation, pynput documentation, mss documentation, Pillow documentation.

## Open Questions
- Confirm third-party dependency allowance. Confirm scope of app/window control functionality for initial version.
