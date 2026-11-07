V4.2：日期时间业务表、严格目标筛选、按需语义复核与流式跟踪见 [运行说明](docs/v42_migration.md)。缺少目录/权重时明确待配置，不虚构20类或品牌识别成果。

V4.1 的独立输出、运行与续跑说明见 [docs/v41_migration.md](docs/v41_migration.md)。本轮修订仅允许四类动作，新版 annotated video 不含音轨。

# MaterFlow_Monoview

将材料实验的第一人称视频或图片转换为可回查的视觉观察、实验事件与材料谱系。当前已实现 `prepare → caption → ground → consolidate → derive`，保留 Qwen、GroundingDINO、SAM2 image/video 和 DINOv2 的真实模型调用。模型错误会中止运行并保存失败信息；缺少视觉证据使用 `unresolved`。

## 输入契约

本次采用 `docs/V4.0/example_registry_v4.0.xlsx` 与 `docs/V4.0/example_manifest_v4.0_filter.json` 的结构，输出遵循同目录三个 example 工作簿的表头。`templates/` 是另一套契约，程序不自动选择、混合或回退。项目名为 MaterFlow，`schema_version` 保持 `ChemFlow-v4.0`。原 example 文件及事实行未修改。

`configs/example_manifest_filter.json` 是原 manifest 的副本，仅增加用户声明的 `protocol.scene_description`。场景、SOP、候选标签、角色与初态登记不会被当作已执行事实。`C_PRODUCT` 保留多个实物候选；未知乙醇瓶或真空泵不强配已有实例。`filter_paper_1` 的登记不证明实际用了滤纸。

运行仅读取指定 registry 的 `Protocol`、`Object Classes`、`Relations`、`Entities`、`Event Types`、`SOP`、`输入词表`；不会加载 `reference/`、人工 Field Evidence 或 example 输出事实行。模型上下文按 Protocol 白名单筛选，排除比较规则、自由 note、lineage_effect 和审校字段。

## 环境与最小运行

建议 Python 3.11。先安装与本机驱动匹配的 PyTorch/torchvision，再安装锁定依赖；`requirements.txt` 的模型接口基于 Transformers 5.3.0。模型首次运行会从 Hugging Face 下载权重，可先缓存并设置 `local_files_only: true`。无需安装 SG-Ego、SGLang、独立 OCR 服务或 SentenceTransformer。

```powershell
cd E:\Projects\MaterFlow_Monoview
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

将实际视频放在 `raw/example_video.mp4`，或修改 `configs/example_manifest_filter.json` 的 `capture.sources[].file`。确认其 run_id 与 YAML 一致，然后运行：

```powershell
.\.venv\Scripts\python.exe run.py --config configs/example_run.yaml
```

默认 `--stage all`，也可逐阶段运行：

```powershell
.\.venv\Scripts\python.exe run.py --config configs/example_run.yaml --stage prepare
.\.venv\Scripts\python.exe run.py --config configs/example_run.yaml --stage caption
.\.venv\Scripts\python.exe run.py --config configs/example_run.yaml --stage ground
.\.venv\Scripts\python.exe run.py --config configs/example_run.yaml --stage consolidate
.\.venv\Scripts\python.exe run.py --config configs/example_run.yaml --stage derive
```

`--config` 相对命令当前目录；**YAML 内所有路径及 manifest.capture.sources[].file 均相对仓库根目录**，绝对路径直接使用。不另设 video_path。图片来源的 file 可指向单张图片或按文件名排序的图片目录，可选 `timestamps_s` 数组与图片逐一对应；未提供时间戳时保持 null。

```yaml
run_id: EXAMPLE_RUN
registry_path: docs/V4.0/example_registry_v4.0.xlsx
manifest_path: configs/example_manifest_filter.json
output_dir: outputs/EXAMPLE_RUN
model_name: Qwen/Qwen3.5-9B
sample_fps: 5
window_frames: 10
window_stride: 8
device: cuda
```

默认 Qwen9B 使用 bfloat16，8 GB 显存无法容纳其完整权重；需要足够内存的设备。可显式设置 `device: cpu` 做较慢推理，或配置已经核验支持相同多模态 chat 接口的其他模型。程序不会按模型版本号自动替换模型，不默认启用量化。分阶段命令可在进程结束时释放模型；`all` 中 Qwen 后端只实例化一次。`model_revision`、`grounding_revision`、`sam2_revision`、`dinov2_revision` 可固定 commit，摘要同时记录解析出的模型 commit。

`prepare` 先编译语义，再解码来源。没有视频时，词表、context 和 lab_scene 仍会保存，随后明确报告缺少来源，prepare 不会标为完成。其余单阶段要求上游阶段完成且配置、代码、来源文件元数据与产物散列相符；不会因为同名文件存在就复用结果。改配置或输入后重新 prepare 并顺序运行下游。

## 阶段与函数

| 阶段 | 函数与实际职责 | 磁盘输入 → 输出 |
| --- | --- | --- |
| prepare | `config.load_config`、`materials/context.load_run_context`、`materials/prompts.compile_sg_inputs` | YAML + registry + manifest → 词表、命名空间别名、角色候选、context、唯一模板替换得到的 lab_scene |
| prepare | `video/reader.inspect_video/read_frames`、`video/sampling.sample_frames/build_time_windows` | manifest 来源 → 原分辨率帧、PTS/原帧号/采样帧号映射、真实窗口与输入列表 |
| caption | `perception/relations.extract_frame_relations/parse_relation_output` | FrameRecord + lab_scene → 五数组结构、原始响应、帧内类别实例映射、兼容 parsed 三元组 |
| ground | `perception/grounding.ground_relation_objects/merge_frame_detections` | Stage 1 全部物理对象 → GroundingDINO 框、同帧去重与帧内引用匹配 |
| consolidate | `tracking/objects.consolidate_object_tracks/link_window_tracks` | 帧图 + 窗口 → SAM2 image/video 掩码定位、DINOv2 外观匹配与重叠窗口轨迹 |
| consolidate | `materials/ocr.extract_lab_observations`、`video/sampling.crop_regions`、`materials/identity.resolve_object_identities` | 模型结构 + 轨迹 → 统一 observation、必要 ROI 补读、精确标签绑定和证据索引 |
| derive | `materials/events.propose_lab_events(observations, context)`、`resolve_event_candidates(candidates, observations, context)` | 仅 observations → 证据候选及归并事件 |
| derive | `materials/state`、`materials/lineage.reconstruct_lineage(events, observations, context)`、`materials/sop.align_events_to_sop` | 事件 + 观察 + 明示初态 → 样品状态、来源边；SOP 最后仅提供候选标签 |
| derive | `outputs/export.assemble_run_result/export_run_result` | 观察/事件/谱系 → 三个 Excel、字段证据、说明及内部 JSON |

`infer_images` 是独立图像请求；`infer_clip` 在一次请求中联合输入按时间排序的多图。仅倾斜或未能确认到达的关键窗口会补看片段，且要求已声明连续、至少两张且时间戳已知。每来源默认最多复看 10 个窗口，可用 `max_clip_reviews_per_source` 调整；`clip_review: false` 可关闭。片段新增证据先保存为 observation，derive 不调用模型。

OCR 保留负号、小数与百分号。仅原转录含不清晰标记或空文字、且有检测框时调用同一 Qwen 补读 ROI；每轨迹/文字类型首个不清晰区域补读一次。`roi_reread: false` 可关闭。裁剪函数实际位于 `video/sampling.py`，保存原图像素映射；不存在 video/preprocess.py。

## 时间、身份与事实边界

PyAV 按解码 PTS 采样；原始 PTS/time_base 及 source_frame_index 保留。source_time_s 以流的 start_time（已知时）为原点，否则使用解码 PTS 的秒值。绝不使用 frame_id/5 回填源视频时间。内部窗口统一 `[start,end)`；windows.txt 是显式适配的闭区间 `start-(end-1)`。frames.txt 包含 source_id、真实采样 frame_id、source_frame_index、时间和图片路径。该磁盘结构需要本项目适配器，不能直接传给原版 SG-Ego CLI。

SAM2 跟踪物理承载对象；DINOv2 和 SAM2 共同参与关联，歧义匹配保留独立轨迹。关系/OCR/流动只能引用原始观察帧，SAM2 传播框不会自行产生流动或关系。跨窗口利用重叠帧的检测 ID 和外观特征关联；不会仅凭类别合并。模型按窗口加载画面，不将全过程图像常驻内存。

对象绑定目前采用规范化后精确可见标签及 manifest 候选匹配；仅类别相同、角色唯一或 SOP 预期都不足以确认。没有标签的容器可能保持 object_id 空，但轨迹、观察与来源不丢失。局部 object_class、binding 和 reading 各有独立字段状态。

唯一视觉事实入口为 `observations.jsonl` 的结构化 payload。动作只含 transfer/stir/hold/age/filter/wash；帧关系 hold 不会变为实验 hold。倾斜无到达不产生 transfer。过滤要求悬浊液进入漏斗且有截留/分离或可回查滤液去向；洗涤要求洗液接触已截留样品且排液。初次上样计入 filter，洗液流动计入 wash，避免重复 transfer。两轮有可区分证据才输出两个 wash，计划次数不补事实。

事件归并默认相邻证据容差为 `2/sample_fps` 秒，可显式配置 `event_gap_s`。这只是可见证据归并阈值，不是实验过程时长；未知连续性不跨间断归并。只有有序片段的明确起止状态与已声明连续性都满足时，过程区间才可标为 supported。单帧、未知边界与窗口端点均不自动生成起止时间。time_origin_iso/run_offset_s/time_scale/is_continuous_clip 的 null 不补 0、1 或 true，程序不导出真实过程时长。

设定温度/转速、含义不明的数字保留 ocr_text。数值质量要求已绑定天平，仍不确认净质量；样品温度要求 measured + 探针测量状态 + 目标关系；实际转速要求 measured + 实际反馈状态。GroundingDINO 只定位，不能证明化学身份或物料到达。

初态为空列表且 unresolved 不代表容器空。可支持的显式初态条目使用 `container_id`、`reagent_ids`、`field_status: supported` 和 `provenance_status`；确认空容器需另有 `contents: empty`。登记证据使用 `REG:initial_conditions:<index>` 回指 manifest，不冒充视觉证据。后续局部操作不会自动修复未知配制历史。材料 ID（M/S）与实物 ID 分开；洗涤不自动把洗液保留为产物成分，过滤只建立实际观察到的 retentate/filtrate 分支，不默认源耗尽。

## 运行目录与导出

谱系当前仅在同一来源且已声明连续的事件间继承样品状态。跨来源或未知连续性的后续观察另建来源未明的根节点；同一个物理容器 ID 不会自动证明其中内容连续。未确认时宁可保留断开的来源关系，也不凭文件名顺序连接样品历史。

```text
outputs/<run_id>/
  annotations/{objects,relations,alias_map}.json
  prompts/{lab_scene.txt,lab_frame.template.txt}
  context.json
  input_files/{videos,frames,windows}.txt
  frames/<source_id>/*.png
  frame_index.jsonl
  capture_info.json
  windows.json
  captions/<model_tag>/<source_id>.json
  frame_graphs/v1/<source_id>.json
  video_graphs/v1/<source_id>.json
  crops/...
  observations.jsonl
  identities.json
  visual_evidence_index.json
  evidence_index.json
  derived.json
  run_summary.json
  observations_v4.0.xlsx
  events_v4.0.xlsx
  lineage_v4.0.xlsx
```

原视频只引用，不复制。visual_evidence_index 是不可变的上游视觉证据索引，derive 在 evidence_index 中补充明示登记来源。模型坏 JSON 保存原文后报具体帧/片段；不会写空图伪装成功。

三个工作簿分别含 Observations、Events、以及 Materials/Lineage 主表，并各含 Field Evidence 与说明。列序按原实现附件的 v4.0 契约冻结于 `tests/fixtures/export_headers_v4.json`，不会随可编辑示例的额外列静默迁移；run_id 仅在说明。Excel 多值 ID 用分号，内部 JSON 用数组；未知单元格为空，数值保持数值。观察引用 V/REG 证据，事件引用 O 观察，谱系引用 E 事件。所有审校者默认为空，human_review_complete=false。

SGLang、预测、步骤时长、计划比较与评价不在本次必经路径，现存相关占位文件不被主流程导入。不输出这些独立业务表。

## 已执行验证与待实测项

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

语义测试使用合成 observation，不是视觉识别验收答案；阶段衔接测试明确用测试替身，实际命令不支持假模型后端。PyAV 测试实际编码/解码变时间戳视频，验证窗口末尾、坐标与时间映射。模型接口测试在锁定库上用随机小尺寸网络检查 SAM2 image/video 和 DINOv2 的真实张量调用，不下载预训练权重，也不证明识别效果。

实际 Clear Video 的五阶段推理已完成：9,220 条 observation、79 条 event、158 条 material、79 条 lineage，以及完整标注视频已生成；状态和文件散列见 output/clear_video/run_summary.json。真实 GroundingDINO、SAM2 image/video 与 DINOv2 首帧联通检查已通过；Qwen 的首帧/中段/末段探测及失败响应保存在 diagnostics。单元测试与接口探测均不能代替完整视频结果或识别准确率验收。

## 模型与方法依据

参考 [SG-Ego ecf24c3](https://github.com/francescapistilli/sg-ego/tree/ecf24c3776b63ad1d9e49272ea090df1d596afdd) 的 Qwen → GroundingDINO → SAM2/DINOv2 方法和目录。跟踪算法在本项目独立实现，未复制上游源码；上游窗口关系并集不用于断言关系持续时间。

接口核对依据为 [Qwen3.5-9B 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)、Transformers 5.3.0 的 [SAM2](https://huggingface.co/docs/transformers/v5.3.0/en/model_doc/sam2)、[SAM2 Video](https://huggingface.co/docs/transformers/v5.3.0/en/model_doc/sam2_video)、[GroundingDINO](https://huggingface.co/docs/transformers/v5.3.0/en/model_doc/grounding-dino) 与 DINOv2 API；模型自身许可随各官方权重和依赖保留。


## Clear Video 的本机完整推理配置

实际输入为 `raw/clear_video.mp4`，registry/manifest 显式取自 `Video_sample/Clear Video/`，人工 observations/events/lineage 不进入识别。自动生成配置位于 `output/clear_video/run.yaml`；结果、原始响应、日志和标注视频均写入 `output/clear_video/`。原 manifest 的 run_id 为 EXAMPLE_RUN，沿用此登记值。

本机环境为 Python 3.11.9、torch 2.10.0+cu128、torchvision 0.25.0+cu128、Transformers 5.3.0、bitsandbytes 0.50.2、FLA 0.4.0、triton-windows 3.6.0.post26。精确环境保存为 environment.json / environment.lock.txt。可用 `python -m pip install -r requirements-gpu.txt` 重建；Triton Windows 是独立维护的 Windows 发行版，FLA 上游不承诺 Windows 支持，本项目对实际算子另有数值和执行检查。

预训练权重已缓存到 models/hub 并固定 commit：当前用户选择 Qwen/Qwen3.5-4B @ 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a，另使用 IDEA-Research/grounding-dino-tiny、facebook/sam2.1-hiera-tiny、facebook/dinov2-small。4B 文本线性层使用 NF4 双重量化，视觉塔、词嵌入及输出层保留 BF16，全部在 GPU；9B 探测与已验证的部分帧保留为诊断记录。顺序加载通过 HF_DEACTIVATE_ASYNC_LOAD 避免未量化权重的并发显存峰值。

Windows 的该 PyTorch 构建未包含 FlashAttention，直接使用 GQA 会退回大内存数学内核。运行配置的 attention_backend: sdpa_efficient 使用 Transformers 公开 AttentionInterface，将键值按查询头数等价展开，再调用原生节省显存 SDPA；因果、掩码、解码、视觉四类数值对照均有测试。模型的位置编码、权重和事件规则不因这项设备兼容处理而改写。

在仓库根目录执行 `./runs/clear_video/run.ps1` 可逐进程运行所有阶段，释放前一阶段模型的显存；中断后可用 `./runs/clear_video/run.ps1 -FromStage caption` 等从指定阶段继续。caption 仅复用输入图片、提示词、配置指纹和实际模型版本签名一致的已验证响应；坏 JSON 原文保留，仅补发一次结构校正请求，仍失败则报错。

标注视频保留原视频帧和时间轴，画面注明最近采样证据的时间和显示滞后。框在一个采样周期内短暂延续仅用于显示；事件文字表示对应采样帧上的证据，不证明完整事件区间。说明 JSON 记录此策略，人工标注不叠加。

实际完成状态以 run_summary.json 为准；diagnostics 下的首帧测试和失败探测均与最终业务结果分开保存。

本次继续运行时还修复了 Qwen 结束标记：使用 tokenizer 的 `<|im_end|>`，而不是模型基础配置的 `<|endoftext|>`。采样参数采用官方 non-thinking 建议的 temperature=0.7、top_p=0.8、top_k=20、presence_penalty=1.5；presence penalty 只作用于已生成 token，不改写画面或观察。使用 [LM Format Enforcer 0.11.3](https://github.com/noamgat/lm-format-enforcer) 的公开核心 API 进行 JSON Schema 类型约束，避免布尔值被生成为字符串。其旧 Transformers 适配器不兼容 5.3 的导入路径，本项目的小型 tokenizer 适配器及 MIT 声明见 docs/third_party_notices.md；未修改依赖源码。完整 JSON 围栏可去除，原文仍保存；不修补截断响应或补写事实。

`runs/clear_video/generate_config.py` 显式读取 Clear Video registry/manifest，生成 run.yaml 和输入溯源；配置中的模型、采样与量化项是经本机检查的推理配置。`caption_batch_size: 4` 表示独立图像批次，显存不足时会明确记录并缩小批次，仍运行相同请求。用户已明确选择 4B 完成全流程；此前 9B 的 38 帧结果归档在 diagnostics，不参与 4B 的正式三表与标注视频。

当前 Clear Video 已按用户选择切换为 `caption_fps: 1` 与 `sample_fps: 5`：Qwen 计划 183 帧，GroundingDINO/SAM2/DINOv2 处理 911 帧，原视频 5462 帧完整保留。切换时归档了 322 帧有效旧响应，65 个对应新采样点；经原请求签名、图片哈希、完整提示词、模型/依赖版本、别名及重新解析结果核验后才显式复用，需新增 118 个 Qwen 请求。原始请求签名和版本留在 reuse_provenance 与 diagnostics/5fps_before_sparse。新配置和采样列表仍位于 output/clear_video。

中间的 5fps 帧使用相邻 Qwen 结果中的通用类别作为 GroundingDINO 查询先验，每个检测框仍从该帧重新推理。中间帧的 observation 仅记录实际检测类别/位置；不复制 OCR、运动状态或物料流。标注视频明确区分检测/跟踪与 Qwen 频率。当前 36 项回归检查已通过，实际全视频完成状态以 run_summary.json 为准。

JSON 读取兼容仅允许引号内的原始 Tab/LF/CR，并逐字保留这些字符；不插入或删除模型 token，不补引号、括号、引用对象或观察事实。其他原始控制字节、NaN、截断和引用错误仍拒绝。原文归档于 diagnostics/sparse_before_wire_compat；其中模型已实际生成但尚未解析的相邻有效响应可重新验证后显式复用。该项兼容处理已有回归覆盖。

已修复约束解码的 UTF-8 字节回退兼容问题：部分汉字（例如“腈”）不在约束库的初始字符表中，会使未闭合 JSON 被误判为可结束。现在在解析器消费新 token 前登记实际拼成的 Unicode 字符，并排除未转义控制 token；标签中的换行仍可通过合法 JSON 转义保留。模型权重、注意力、图像输入、提示词、采样参数和五数组结构未改动。旧有效响应经字符原文/签名/模型核验继续保留，后端代码差异与神经函数 AST 核验记录在 diagnostics/sparse_before_unicode_fix/reuse_manifest.json。36 项检查及所选 4B 的真实 tokenizer 字符检查已通过；这些检查不等于视觉准确率评价。


## Clear Video 完成产物与状态查询

结果位于 `E:/Projects/MaterFlow_Monoview/output/clear_video/`：`observations_v4.0.xlsx`（9,220 行）、`events_v4.0.xlsx`（79 行）、`lineage_v4.0.xlsx`（Materials 158 行、Lineage 79 行）、`clear_video_annotated.mp4`。这些是自动识别草稿，未进行人工准确率验收。三个工作簿的业务字段及字段证据逐单元格与自动 JSON 核对，主表表头符合冻结的 v4.0 契约。视频已完整解码核对：1920×1080、30fps、5,462 帧、182.066667 秒；逐帧 PTS 和原始 AAC 包内容均与输入一致。核验报告位于 diagnostics/workbook_validation.json 和 diagnostics/video_validation.json。

本次修复了 Excel 写表时逐行扫描整张工作表造成的二次增长。导出改为按明确行列写入，并记录各工作簿的开始与完成。18 组工作表 XML/样式对照一致，940 个上游产物哈希通过核对后显式兼容续跑 derive，原始模型响应及其请求指纹保留；变更前源码、摘要和兼容核验记录见 diagnostics/derive_export_fix/。

在 VS Code 的 PowerShell 终端中查看各阶段状态：

```powershell
Set-Location -LiteralPath 'E:\Projects\MaterFlow_Monoview'
$s = Get-Content -LiteralPath '.\output\clear_video\run_summary.json' -Raw | ConvertFrom-Json
$s.stages.PSObject.Properties | Select-Object Name, @{Name='Status';Expression={$_.Value.status}}
```

正常结束时日志显示 `Completed derive`，五个阶段均为 `complete`，执行窗口返回 PowerShell 提示符。`Get-Content ... -Wait` 只是持续跟踪日志，任务结束后仍会等待；在这个日志查看窗口按 Ctrl+C 只会结束日志查看。进程异常退出可能留下 `running`，因此该状态需要结合实际 Python 进程核查。已经完整结束的本次任务无需再次启动。
