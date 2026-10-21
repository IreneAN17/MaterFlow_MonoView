# Materflow_Monoview

面向材料制备实验的第一人称视频结构化记录框架。

## 当前状态

本文是第一版代码架构与协作规范，尚不代表以下模块已实现或命令已可运行。本次只提供 README，未修改本地 Windows 仓库。请放到 `E:\Projects\ChemFlow_MonoView\README.md`，并随代码一起提交 Git。

以 SG-Ego 的视频读取、关系提取、对象定位和跨帧整合结构为参考，使用固定识别后端，不进行模型微调。材料示例为 Formamide–Long-chain Alkane–CTAB–TEOS；具体烷烃、操作顺序及条件由实际 SOP 提供。

目标：记录实际操作、OCR 读数、步骤起止时间与时长，导出结构化记录；后续增加样品谱系和下一行动／试剂候选。SOP 是软先验，不能代替视频证据。

## 目录与文件放置

以下路径均相对仓库根目录。使用根目录下的 Python 包 `materflow/`，避免首版额外的安装配置。

| 路径 | 内容 |
| --- | --- |
| `README.md` | 本文：入口、模块、配置及协作约定 |
| `run.py` | 命令行入口，只解析参数并调用主流程 |
| `materflow/__init__.py` | Python 包初始化 |
| `materflow/pipeline.py` | `process_run()`，组织完整处理顺序 |
| `materflow/schema.py` | 模块共用数据类型与字段约定 |
| `materflow/config.py` | 配置读取、路径解析、基本校验 |
| `materflow/video/` | 视频读取、抽帧、窗口和裁剪 |
| `materflow/backends/` | 模型调用接口及后端实现 |
| `materflow/perception/` | 关系提取和对象定位 |
| `materflow/tracking/` | 对象跨帧、跨窗口关联 |
| `materflow/materials/` | OCR、身份、事件、SOP、状态、谱系和预测 |
| `materflow/outputs/` | 汇总、JSON 和 Excel 导出 |
| `materflow/evaluation/` | 人工参考比较和错误报告 |
| `configs/pipeline.yaml` | 通用模型、采样、窗口与启用模块配置 |
| `configs/materials/registry_v3.4.xlsx` | 试剂、别名、实体、动作、SOP 与计划参数 |
| `configs/instruments/instrument_profiles.yaml` | 仪器单位、显示含义和可复用识读配置 |
| `configs/rules/action_rules.yaml` | 动作角色、完成证据及起止判据 |
| `configs/rules/state_rules.yaml` | 加料、转移、混合、取样的状态规则 |
| `configs/prompts/` | 通用关系、实验动作、标签和仪表提示词 |
| `runs/run_001/run.yaml` | 本次实验输入路径、配置引用与覆盖参数 |
| `runs/run_001/manifest_v3.4.json` | 本 run 的实物、容器、仪器和初始内容 |
| `runs/run_001/reference/` | 独立人工参考；识别阶段不可读取 |
| `raw/run_001/ego.mp4` | 原始视频，或由 run 配置引用外部硬盘位置 |
| `outputs/run_001/` | 本次运行的自动结果、证据索引和评价 |
| `docs/annotation_guide.md` | 标注、字段和步骤边界说明 |
| `docs/module_status.md` | 模块负责人、实现状态及待补事项 |
| `tests/fixtures/` | 少量脱敏或合成的回归样例 |
| `requirements.txt` | 核验后的依赖；按已实现后端维护 |
| `.gitignore` | 排除原视频、模型、缓存及大体积自动结果 |

各子包添加 `__init__.py`。已有项目文件先通过路径引用，不需要为了符合示例立即移动。`registry_v3.4.xlsx` 和 `manifest_v3.4.json` 沿用现有命名；本 README 不改动其内部表头或子表。

### 图中五个配置文件放在哪里

| 文件 | 推荐位置 | 作用域 |
| --- | --- | --- |
| `registry_v3.4.xlsx` | `configs/materials/` | 同一方案可复用；修改后保留版本 |
| `manifest_v3.4.json` | `runs/<run_id>/` | 每个 run 独立，不能用下一次实验覆盖 |
| `instrument_profiles.yaml` | `configs/instruments/` | 共用仪器定义；本 run 的区域／显示模式可覆盖 |
| `action_rules.yaml` | `configs/rules/` | 实验动作与完成判据 |
| `state_rules.yaml` | `configs/rules/` | 样品状态与来源变化 |

第一人称视角会移动。共用仪器配置不应假定屏幕始终位于同一像素坐标；优先相对仪器检测框定义 ROI，或逐帧重新定位。温度和转速需区分设定值、显示值与实际测量值。

### 路径规则与 run 配置示例

约定：命令中的 `--config` 相对当前工作目录；YAML 内的相对路径统一相对仓库根目录解析，由 `materflow/config.py` 实现，绝对路径直接保留。

```yaml
# runs/run_001/run.yaml（待实现的配置约定）
run_id: run_001
pipeline_config: configs/pipeline.yaml
video_path: raw/run_001/ego.mp4
registry_path: configs/materials/registry_v3.4.xlsx
manifest_path: runs/run_001/manifest_v3.4.json
instrument_profiles_path: configs/instruments/instrument_profiles.yaml
action_rules_path: configs/rules/action_rules.yaml
state_rules_path: configs/rules/state_rules.yaml
output_dir: outputs/run_001
reference_dir: runs/run_001/reference
enable_evaluation: true
enable_lineage: false
enable_anticipation: false
```

规则文件当前可先定义最小样例，随后补齐并冻结字段。共用规则不写本次实验的实物身份；本次初始内容、实例和覆盖项由 manifest／run 配置提供。

## 主程序入口写在哪里

采用两个文件分工：`run.py` 负责启动，`materflow/pipeline.py` 负责业务流程。不要将 OCR、跟踪和材料规则全部写入 `run.py`。

### 根目录 run.py

以下是待创建的入口代码；依赖主流程实现后才能运行。

```python
import argparse
from materflow.pipeline import process_run


def main():
    parser = argparse.ArgumentParser(description="处理一次材料实验视频")
    parser.add_argument("--config", required=True, help="本次 run 的 YAML 配置")
    args = parser.parse_args()
    process_run(args.config)


if __name__ == "__main__":
    main()
```

### materflow/pipeline.py

定义 `process_run(config_path: str) -> RunResult`。推荐分阶段保存中间结果，允许重用已完成的模型推理。

下面是处理顺序的设计伪代码，不是可直接执行的完整实现；函数接口见后表。

```python
def process_run(config_path):
    config = load_config(config_path)
    context = load_run_context(config)
    video_info = inspect_video(context.video_path)

    frames = sample_frames(context.video_path, config.sampling)
    windows = build_time_windows(video_info, config.windowing)
    backend = create_backend(config.backend)

    relations = extract_frame_relations(frames, backend, context)
    detections = ground_relation_objects(frames, relations, context)
    tracks = consolidate_object_tracks(detections, context)
    tracks = link_window_tracks(tracks, context)

    observations = extract_lab_observations(frames, tracks, backend, context)
    identities = resolve_object_identities(tracks, observations, context)
    candidates = propose_lab_events(windows, tracks, identities, backend, context)
    events = resolve_event_candidates(candidates, observations, context)
    events = check_event_state_consistency(events, context)

    step_runs = align_events_to_sop(events, context)
    step_times = calculate_step_durations(step_runs)
    lineage = reconstruct_lineage(events, context) if config.enable_lineage else None
    # 内部按预测时刻截断历史，禁止用未来事件预测过去的下一步。
    predictions = predict_next_actions(events, step_runs, context) if config.enable_anticipation else None

    result = assemble_run_result(events, observations, step_times, lineage, predictions)
    export_run_result(result, context)
    # 人工参考只允许进入评价，不传给识别、消歧或预测模块。
    evaluate_if_reference_available(result, config)
    return result
```

`frames` 推荐是可重复读取的帧索引／图像引用，不能依赖已经耗尽的一次性生成器。长视频按窗口加载图像，避免将全过程所有图像同时放入内存。

## 通用视频处理模块

| 文件 | 函数／类 | 功能 |
| --- | --- | --- |
| `video/reader.py` | `inspect_video()`、`read_frames()` | 视频信息及按时间／索引读取画面 |
| `video/sampling.py` | `sample_frames()`、`build_time_windows()` | 分析帧索引及重叠时间窗口 |
| `video/preprocess.py` | `crop_regions()` | 裁剪标签、仪表及对象区域 |
| `backends/base.py` | `VisionBackend.infer_images()`、`infer_clip()` | 明确独立图片与联合时序输入的区别 |
| `backends/transformers_backend.py` | `TransformersBackend` | 程序内加载固定模型 |
| `backends/sglang_backend.py` | `SGLangBackend` | 后续预留模型服务调用 |
| `backends/factory.py` | `create_backend()` | 按配置创建后端 |
| `perception/relations.py` | `extract_frame_relations()`、`parse_relation_output()` | 模型关系候选、原始输出与解析校验 |
| `perception/grounding.py` | `ground_relation_objects()`、`merge_frame_detections()` | 对象定位与重复检测整合 |
| `tracking/objects.py` | `consolidate_object_tracks()`、`link_window_tracks()` | 跨帧及跨窗口的轨迹对应 |
| `outputs/export.py` | `assemble_run_result()`、`export_run_result()` | 输出数据和证据引用 |
| `evaluation/compare.py` | `evaluate_run()`、`evaluate_if_reference_available()` | 独立人工参考比较 |
| `evaluation/report.py` | `build_error_report()` | 漏检、误检、身份错配、时间误差及缺证清单 |

上表路径相对 `materflow/`。SG-Ego 的 `captioning/`、`grounding/`、`consolidation/` 分别可参考至关系提取、定位和跟踪模块。窗口关系整合图不是最后一帧状态，也不是实验事件本身。

## 材料实验预留模块

| 文件 | 函数名 | 功能及需补信息 |
| --- | --- | --- |
| `materials/context.py` | `load_run_context()` | 读取 registry／manifest；不加载人工参考 |
| `materials/ocr.py` | `extract_lab_observations()` | 调度标签与仪表识读，保留画面证据 |
| 同上 | `recognize_reagent_label()` | 试剂文字及别名；需标签示例 |
| 同上 | `recognize_instrument_reading()` | 数值、单位及显示模式；需仪器定义 |
| `materials/identity.py` | `resolve_object_identities()` | OCR＋轨迹＋manifest 绑定实物身份 |
| `materials/events.py` | `propose_lab_events()` | 连续片段中的实验动作候选 |
| 同上 | `resolve_event_candidates()` | 关联动作、来源和目标，保留未匹配及歧义 |
| `materials/state.py` | `check_event_state_consistency()` | 检查初始内容、操作角色和前后状态一致性 |
| 同上 | `apply_confirmed_event()` | 将确认事件应用到样品状态 |
| `materials/sop.py` | `align_events_to_sop()` | 实际事件与 SOP 对齐，允许重复、跳过和偏离 |
| 同上 | `calculate_step_durations()` | 步骤历时、主动操作及过程持续时间 |
| `materials/lineage.py` | `reconstruct_lineage()` | 样品版本和来源边；保留未知依赖 |
| `materials/anticipation.py` | `predict_next_actions()` | 仅使用预测时刻以前的信息和 SOP，输出行动／试剂候选 |

首版不必强制安装六个参考仓库。Stone Soup 借鉴候选组织；EASG 借鉴事件标注；EgoExoLearn 借鉴关联评价；VidOSC 借鉴前后状态证据；ProbLog 借鉴依赖表达。材料状态与来源规则仍由本项目实现。

## 数据接口

在 `materflow/schema.py` 集中定义。内部结构通过导出适配器映射到现有 Excel 表头；未经协调不修改已有表头。

| 类型 | 最小内容 |
| --- | --- |
| `FrameRecord` | 帧 ID、原视频时间、图像引用、原视频引用 |
| `RelationCandidate` | 主体／关系／客体、证据、原始模型输出 |
| `ObjectTrack` | 轨迹 ID、逐帧框、身份候选、证据 |
| `Observation` | 时间、对象／仪器、信号、值、单位、字段状态、证据 |
| `EventCandidate` | 动作、区间、角色候选、证据和待解决项 |
| `EventRecord` | ID、动作、来源／目标、起止、字段状态、证据、审校状态 |
| `StepRun` | SOP 步骤、执行次数、区间、状态、关联事件 |
| `LineageEdge` | 输入／输出样品、触发事件、来源状态 |
| `ActionPrediction` | 预测时刻、行动／试剂候选、依据、预测状态 |
| `RunResult` | 上述结果集合、模块状态、运行配置引用 |

证据统一保存原视频时间与路径，支持裁剪图回溯。对象框、OCR 分数和模型候选分数不直接当作完整事件概率。步骤允许重复、交叠和边界未知；区间重叠时不简单累加得到实验总时长。SOP 步骤未观察到时，不自动判定已执行或未执行。

## 未实现模块与协作约定

预留函数保留类型、文档字符串和 `NotImplementedError`，不要用空列表伪装成功。必需模块未实现则停止并报明模块名；可选模块关闭则记录 `disabled`，启用但未实现记录 `not_implemented`。视觉证据不足使用 `unresolved`，两者不能混用。

1. 接手模块前，在 `docs/module_status.md` 标注负责人和状态：`planned / in_progress / implemented / validated`。
2. 优先补充约定函数，跨模块签名与 schema 变更先协调，避免各自定义同一字段。
3. 代码、配置和对应说明在同一次提交中更新；README 负责导航，详细判据写入标注指南和规则配置。
4. 提交可复现小样例与结果摘要：运行命令、配置版本、模型版本、错误类型。人工参考与程序输出分开保存。
5. 固定版本后用新 run 测试；开发参考不能作为输入提示或自动校正答案。
6. 借用源码时检查原仓库许可证，保留要求的声明和引用。

Git 跟踪代码、配置、文档和少量固定样例；原视频、模型权重、缓存、凭据及大体积结果放外部存储。对现有 raw 等目录先检查 `.gitignore`，避免上传大文件。

## 运行与结果（接口实现后）

在仓库根目录运行：

```powershell
python run.py --config runs/run_001/run.yaml
```

`outputs/run_001/` 建议保存 `run_summary.json`、中间候选／轨迹 JSON、`events_v3.4.xlsx`、`observations_v3.4.xlsx`、`step_times.xlsx`、可选 `lineage_v3.4.xlsx`、`next_actions.json`、`evaluation.json` 和证据索引。`step_times.xlsx` 是建议新增输出，不表示现有表格已包含该格式。配置、模型和提示词版本随运行摘要记录。

谱系可以输出自动草稿并标明审校状态；人工审校事件后重新生成，不将未经核实的来源作为已确认事实。预测输出单独保存，不混入已执行事件。

## 第一版验收

先用一段包含试剂标签、一次实际加料和一次仪表显示的视频验证：

- 能读取并回查原视频时间。
- 能关联标签身份与操作对象；缺证字段为 `unresolved`。
- 能给出加料候选的来源、目标和时间证据，区分拿起后放回。
- 能读取温度或转速并标明显示含义。
- 能导出事件、观测和步骤时间，比较独立人工参考。
- 能定位误检、漏检、身份错配及时间边界误差，修改后重复运行。

谱系和下一行动预测作为后续模块启用。第一版无需更换模型架构或训练新模型；先验证记录正确性、证据支持与覆盖率。

## 参考仓库

- [SG-Ego](https://github.com/francescapistilli/sg-ego)
- [Stone Soup](https://github.com/dstl/Stone-Soup)
- [EASG](https://github.com/fpv-iplab/EASG)
- [EgoExoLearn](https://github.com/OpenGVLab/EgoExoLearn)
- [VidOSC](https://github.com/facebookresearch/VidOSC)
- [ProbLog](https://github.com/ML-KULeuven/problog)
