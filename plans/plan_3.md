# Add control_mouse toolkit for mouse control, screenshot, and computer operation

## Summary
Implement a new builtin toolkit named control_mouse that enables mouse control (move, click, drag, scroll), keyboard input, screen information retrieval, and screenshot capabilities with workspace-safe file handling and operation confirmation.

## Goal
Implement a new builtin toolkit named control_mouse that enables mouse control (move, click, drag, scroll), keyboard input, screen information retrieval, and screenshot capabilities with workspace-safe file handling and operation confirmation.

## Constraints
- Use cross-platform compatible Python libraries (pyautogui, optionally pynput, mss, Pillow). Ensure all control operations require user confirmation for safety. Screenshot files must be saved only inside the workspace path. Provide a basic implementation for application/window control limited to opening and focusing apps initially. Follow existing toolkit registration and manifest conventions. Add tests and documentation.
