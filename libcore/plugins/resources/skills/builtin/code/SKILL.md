---
name: code
description: 代码技能包：读文件、追加处理、写回的编排流程指引。
---

# Code

你正在使用「代码技能」。本技能只提供工作流指引，不代为调用任何工具：
由你（模型）自己决定依次调用 tools 门面的 file read 读入目标文件、
按需处理、file write 写回。skill 与 tools 零耦合。

## 操作步骤

1. 用 tools 门面的 file read 读入目标文件。
2. 按需要处理内容（可选追加）。
3. 用 tools 门面的 file write 写回。

## 约束

- skill 不调用任何工具；具体读写由你（模型）自行决定并点名 tools。
