# 模块实施状态（2026-11-03）

负责人：Codex。implemented 表示真实代码路径已实现；validated 只表示列出的验证范围，不代表真实视频识别质量。

| 模块 | 状态 | 验证与边界 |
| --- | --- | --- |
| run.py / config.py / pipeline.py | validated | 五阶段与 all、显式路径、指纹/散列失效检查、磁盘衔接；测试替身用于编排验证 |
| schema.py | validated | FrameRecord、稳定 ID、JSON/JSONL、v4.0 列序与字段状态 |
| materials/context.py / prompts.py | validated | 实际 registry/manifest 编译；一对多角色、命名空间别名、模型上下文隔离、null 时间保留 |
| video/reader.py / sampling.py | validated | 真实 PyAV VFR 编码/解码、PTS、尾部/单帧窗口与裁剪原图映射 |
| backends/base.py / factory.py / transformers_backend.py | implemented | 单实例本地 Transformers；独立图像与联合有序多图接口；已核对 Qwen3.5 自动模型映射，真实 Qwen 多位置探测已执行；4B 全流程已完成：Qwen 1fps、检测/跟踪 5fps；未声称准确率验收 |
| perception/relations.py | validated | 五数组/引用/合法 JSON 校验、标签独立保留、字符不清洗、单次 flow 投影；真实 caption 探测已执行，失败原文保留；全量完成状态见 run_summary.json |
| perception/grounding.py | implemented | 真实 GroundingDINO 调用、坐标边界、NMS、拒绝歧义匹配；同类瓶不合并；真实预训练 911 个采样帧定位已完成，保留模型输出及诊断记录 |
| tracking/objects.py | implemented | SAM2 image/video + DINOv2，窗口索引映射、共享检测/特征跨窗关联；小网络张量接口验证，真实预训练 image/video 分割和 DINOv2 的 911 帧全量跟踪已完成 |
| materials/ocr.py / identity.py | validated | 统一 payload、可回查 ROI、设定值保护、精确标签与拒识；真实 Clear Video ROI 补读已执行，识读准确率尚未人工验收 |
| materials/events.py | validated | 仅消费 observation，六类动作；倾斜拒绝 transfer、上样/洗液不重复计事件、重叠去重、保留未知边界 |
| materials/state.py / lineage.py | validated | 已有内容的两条来源边、未知初态不虚构、filter/wash/wash 状态链、只建可见分支、稳定 ID；未知连续性和跨来源不强接内容历史 |
| materials/sop.py | implemented | 在事件之后填写可选 sop_step_candidate；歧义保留多候选；不生成计划动作 |
| outputs/export.py | validated | 三个 Excel 主表 + Field Evidence + 说明，与原例表只比较表头；空值、类型及引用校验 |
| sglang_backend.py / anticipation.py / evaluation/* | out_of_scope | 现存占位文件，主流程不导入；无安装依赖或业务输出 |
| configs/rules/* / instruments/* | out_of_scope | 现存占位 YAML 不进入本次显式配置，无静默回退 |

测试入口：`python -m pytest -q`。语义 fixture、编排替身和随机小网络仅验证各自代码契约；实际 Clear Video、预训练权重和 CUDA 环境已配置，36 项测试已通过；全视频五阶段已完成，三个工作簿逐单元格核对及完整视频/时间戳/原音轨核验通过，未声称视觉准确率验收完成。详见 README 的最小集成命令。

本次实际输出：9,220 条 observation、79 条 event、158 条 material、79 条 lineage。标注视频保留原始 5,462 帧、1920×1080、30fps 和 AAC 音轨。Excel 导出性能修复的兼容续跑记录及最终核验报告均保存在 output/clear_video/diagnostics。
