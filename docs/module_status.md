<!-- V42_STATUS -->
# V4.2 实施与30秒验证（2026-11-04）

当前输出为 output/benchmarks/v42_75_105。真实执行75–105秒片段，33条Observation、5条未确认完整边界/承载的事件候选，Materials/Lineage为空。所有公开起止位置均有拍摄日期时间；三表与900帧1920×1080/30fps无音轨视频已核验。86项回归测试通过。

实现：独立V4.2契约、检测→光流/局部变化→隔离OCR→标签/图库接口→按需单图/有序多图语义→三层筛选→类型化区间→证据事件/谱系→日期导出；新增数据检查和明确的训练/评价CLI、持久ID、内容缓存与已提交KLT分块恢复。

仍为待数据/未实测：完整20类清单、真实包装目录/图库、自定义训练权重、ByteTrack/BoT-SORT真实对比、V4.2按需SAM2实际短片路径、三小时测试。未启动正式训练或全片计算。

质量限制：预览中的filter_funnel类别覆盖天平上部，balance框偏大；输出是待复核自动草稿，格式/引用核验不是识别准确率验收。合成正例支持物理M/S/L规则测试，但真实片段未建立可靠谱系。详见 [V4.2运行说明](v42_migration.md) 和对应benchmark_report。

以下内容保留V4.1/V4.0历史状态，数量、时间列与执行架构不适用于V4.2。

<!-- V41_STATUS -->
# V4.1 实施与真实验证（2026-11-04）

新版入口位于 `materflow/v41/`；以下结果独立于后文的历史 V4.0 验证。GPU 环境与四个模型 commit 保持现有版本，新增时钟 OCR 使用独立 `.venv-ocr`。

| 模块 | 当前验证范围 |
|---|---|
| Registry/manifest/YAML | 实际 V4.1 六个程序读取表；Reagents 不进入模型上下文；仅 stir/transfer/hold/filter；实际 manifest 与 YAML 单独生成 |
| 自适应 caption | 源视频 75–100 秒，11 个采样点真实 Qwen3.5-4B 推理全部结构校验通过；一次失败重试保留原文 |
| 多类别定位 | 同片段 125 帧真实 GroundingDINO；类别和冲突筛选；逐帧缓存摘要校验 |
| 共享分割与跟踪 | 真实 SAM2.1 + DINOv2；4 个多对象会话，17 条轨迹，3 条单帧轨迹；仍有漂移和类别混淆 |
| 编号、过滤与聚合 | 467 原始 -> 389 规范候选 -> 129 业务 observation；O/E 公开编号与容器实例由持久映射管理 |
| 事件与谱系 | 6 条 transfer 候选的区间/承载关系未完全确认；实际片段未生成 M/S/L，不能宣称真实谱系重建通过；正例物理证据规则有单元测试 |
| 时钟 | 固定 ROI 的轻量 OCR；25/25 锚点完整数字可解析；缺证尾段不外推；O/E/M/S/L 通过独立时间报告回查 |
| Excel/视频 | 三表逐单元格一致、公开引用与产物摘要通过；750 帧、1920×1080、30fps、逐帧相对 PTS 一致且无音轨；首中尾预览已查看 |
| 续跑与状态 | 五阶段全部热缓存验证约 1.75 秒，零新增推理；独立进程/原子状态/PID 与创建标识；保留原冷运行计时 |
| 回归覆盖 | 63 项测试通过；合成记录和随机小网络仅验证接口与契约，不是正式预测 |

同片段新旧视觉阶段计时及 GPU 状态以 `output/benchmarks/v41_75_100/benchmark_report.md` 和 JSON 为准。硬件状态变化、caption 采样和类别参数不同，不能据此宣称受控的整体加速倍数。全片配置已生成，**没有启动全片 V4.1 推理**。操作命令见 [v41_migration.md](v41_migration.md)。

以下保留 V4.0 历史记录；其中旧动作范围、音轨和计数不适用于新版。

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
