---
name: axolotl
description: 使用 Axolotl 对模型做 SFT 微调的训练技能。
---
# Axolotl 微调

用 Axolotl 训练模型（SFT）。这是一个**纯指令**技能的示例：只有 SKILL.md，
不带 scripts/references/scripts 附件，engine 只提供指令与空 filemap。

## 何时使用
- 需要对一个基座模型进行有监督微调（SFT）时。

## 操作
1. 准备 YAML 配置（data path、base model、输出目录）。
2. 安装 axolotl 依赖。
3. 运行训练命令并跟踪 loss。

## 约束
- 训练前必须先确认显存与数据规模匹配。