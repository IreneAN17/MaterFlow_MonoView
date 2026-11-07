# MaterFlow V4.2 实施、数据准备与运行

本版保留V4.0/V4.1入口及历史产物，新增ChemFlow-v4.2契约和独立输出目录。当前使用原有12个用户已确认类别，尚无完整20类清单、包装目录/图库或专用训练权重；没有编造这些输入。

## 本轮真实验证

输入为raw/clear_video.mp4的75–105秒，共30秒、150个5fps内部采样点。真实执行固定commit的GroundingDINO、隔离RapidOCR、Qwen3.5-4B/NF4，并以OpenCV KLT进行实际光流更新。语义复核包括11个单图请求与6个真正有序的局部多图请求，不把独立图像batch称为时序输入。

产物在output/benchmarks/v42_75_105/：
- observations_v4.2.xlsx：33条主表记录，含Object Identities。
- events_v4.2.xlsx：5条自动候选，完整边界/承载关系未确认。
- lineage_v4.2.xlsx：Materials和Lineage为空，没有虚构材料节点。
- observations/events/materials/lineage/objects.jsonl：公开日期时间字段。
- interval_evidence.json、time_report.md：区分证据范围、未知缺口、时钟跨度和处理时长。
- clear_video_annotated.mp4：900帧、1920×1080、30fps、原相对PTS、无音轨。
- capability_report.json：配置、验证与待数据状态。
- benchmark_report.md/json、diagnostics/validation.json：计时、缓存、单元格/引用/视频核验。
- internal/：原始识读、模型请求、秒数/PTS、SQLite索引、候选与筛选记录。

注意：这是自动识别草稿。首/中/尾预览可见可疑类别，例如filter_funnel框覆盖天平上部，balance框偏大；没有独立精度/召回率验收。已有标签筛选会将缺少包装匹配的瓶子留在内部候选中。没有正式训练或3小时全片测试。

同一开发run保留最初编号，因此失效/合并后可能跳号；不回收旧ID。新run从O001/E001和类别_1开始。

## 模块与实际验证状态

| 部分 | 已实施内容 | 验证边界 |
|---|---|---|
| 契约与时间 | 单一schema_v4_2.json、派生Registry/空模板、ISO8601与Excel真实日期单元格、公开/内部时间分离 | 三表与JSON逐单元格及时间字段检查通过 |
| 检测 | GroundingDINO多短语查询、原始低阈值结果缓存、规范化/冲突筛选、显式专用检测后端 | 基线路径真实运行；自定义权重未提供 |
| 跟踪 | 光流与预测区分、同帧多对象光流批处理、真实PTS间隔、分块状态与拒识记录恢复 | 真实短片＋合成分块恢复测试；非3小时实测 |
| OCR | 原图区域检测/识读、保留逐字原文和框、显示/标签区分、内容/模型缓存 | 81个区域真实识读；识读准确率未人工验收 |
| 包装筛选 | 必需标签字段完整匹配、歧义/未知拒识、图库仅辅助、包装与物理瓶号分离 | 匹配规则单元测试；真实品牌/规格待用户数据 |
| 参考检索 | 可选真实DINOv2参考索引和查询接口 | 空索引不加载模型的测试通过；无真实用户图库查询 |
| 专用小模型 | 数据检查、训练/评价CLI、显式权重/类别映射、ByteTrack/BoT-SORT可选适配器 | 未安装可选环境、未正式训练、未做两种跟踪器真实对比 |
| 按需SAM2 | 仅在启用且指定类别需要时加载图像分割，独立mask缓存 | 本片段配置off，调用0；此新适配路径尚未真实短片验证 |
| 时间过程 | 点读数、可见区间、读数平台、带未知缺口的状态包络、边界事实转O后派生E | 合成正例物理谱系通过；实际片段M/S/L为空 |
| 续跑/导出 | 显式依赖DAG、原子记录、持久ID检查、严格前置校验、静默视频 | 八阶段热缓存验证通过，约1.44秒；首次真实计时另存 |

## 环境

现有.venv的Torch/Transformers/bitsandbytes及4B量化设置保留。V4.2只新增固定OpenCV与Windows时区数据：

    Set-Location -LiteralPath 'E:\Projects\MaterFlow_Monoview'
    uv pip install --python .\.venv\Scripts\python.exe --no-deps -r requirements-v42.txt

一般标签与时钟OCR继续使用独立.venv-ocr；缺少时先运行：

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\runs\v41\setup_ocr.ps1

模型commit、采样、阈值和路径见实际run.yaml。不要默认升级现有GPU环境。

## 生成配置与30秒运行

首次生成一个新run：

    .\.venv\Scripts\python.exe runs/v42/configure.py --start 75 --seconds 30 --name v42_75_105

该名称已有完成结果时，不要重复生成配置；直接续跑，或使用新name创建独立run。configure会拒绝覆盖已有run_summary。

运行八个独立进程阶段：

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\runs\v42\run.ps1 -ConfigPath .\output\benchmarks\v42_75_105\run.yaml

阶段顺序：
prepare → scan → ocr → identify → semantic → aggregate → derive → export

- prepare：视频索引与时钟OCR，不永久保存全部原分辨率采样帧。
- scan：实际检测、相机运动补偿、局部变化、光流/候选与分区状态。
- ocr：选择原图ROI并批量调用隔离OCR。
- identify：程序侧标签门槛与可选图库排序。
- semantic：缓存任务输入，集中加载Qwen复核单图与有序图片。
- aggregate：严格三层筛选、原始/业务记录、区间和内部索引。
- derive：只消费已纳入的observation生成事件/材料/谱系。
- export：按需精读缺失的边界时钟，输出三表、日期时间报告与视频。

## 指定阶段、续跑、状态与日志

从指定阶段继续：

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\runs\v42\run.ps1 -ConfigPath .\output\benchmarks\v42_75_105\run.yaml -FromStage semantic

仅重新导出，不运行识别阶段：

    .\.venv\Scripts\python.exe run.py --config .\output\benchmarks\v42_75_105\run.yaml --stage export

只运行到指定阶段：

    .\.venv\Scripts\python.exe runs/v42/run_stages.py --config .\output\benchmarks\v42_75_105\run.yaml --from-stage prepare --through-stage scan

状态：

    .\.venv\Scripts\python.exe runs/v42/status.py --config .\output\benchmarks\v42_75_105\run.yaml

日志：

    Get-Content -LiteralPath .\output\benchmarks\v42_75_105\logs\semantic.log -Tail 20 -Wait

Get-Content -Wait是日志查看；计算结束后它仍会等待。Ctrl+C退出该查看器，不是停止另一个终端中的计算。

scan按chunk_seconds提交分区。KLT恢复验证通过的已提交分区，恢复活动轨迹与拒识记录；尚未提交的分区从源重放并复用逐帧神经网络任务缓存。分块不是新run，不重置物理对象号。与本次代码/上游依赖不一致的分区不能直接复用。

semantic请求完成即保留经过校验的原始响应缓存。optional语义预算未处理完任务时标记partial，不标全部完成。

## 全片配置与运行

本轮只生成全片配置，不启动全片：

    .\.venv\Scripts\python.exe runs/v42/configure.py --full

用户决定执行时：

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\runs\v42\run.ps1 -ConfigPath .\output\clear_video_v4_2\run.yaml

输出为output/clear_video_v4_2。默认仍指向当前clear_video.mp4，不是凭空存在的3小时视频。新视频应配置新的manifest、run_id和output_dir，保持原输入只读。

## 数据未齐时的行为

- classes_path未配置：使用现有Registry真实类别，不自动补足20类。
- catalog_path未配置：瓶子包装身份为not_configured，不分配虚假品牌/规格，不通过瓶子标签门槛。
- gallery_index_path未配置：不加载参考特征模型，不声称已完成图片匹配。
- custom_detector_weights未配置：采用显式GroundingDINO基线；选择custom_yolo时必须有权重和完整类别映射，否则明确失败；不静默换模型。
- 缺少上述数据不会阻止日期迁移、区间、缓存、既有类别基线和短片输出。
- 标签通过仍不等于实验相关；前景/持续出现也不再是独立入表理由。
- 不把衡器等class_only仪器当作物料容器端点。
- 未识读的空OCR留在原始记录中，不写成空白业务事实。

## 用户数据接入

空模板在docs/V4.2/data_templates/。具体采集清单见此前用户准备文档；这里没有真实包装样本。

建议用户整理：
data/lab_items/classes.csv
data/lab_items/packaging_catalog.csv
data/lab_items/references/
data/lab_items/detection/{images,labels}/{train,val,test}/
data/lab_items/metadata/split_manifest.csv

classes.csv至少包含object_class、id_policy，建议补充class_id、定义和别名。包装目录required_match_fields可用JSON数组或分号列表；label_aliases用按字段映射的JSON对象，例如品牌别名映射。这个说明示例不是实际产品条目。

在新run.yaml中显式填写classes_path、catalog_path、gallery_index_path；不要把空模板配置成已完整的数据。

建立参考索引：

    .\.venv\Scripts\python.exe runs/v42/build_gallery.py --config <实际run.yaml> --output <参考索引.json>

配置中必须指向真实包装目录及参考图。空目录只生成not_configured，不创建虚假参考向量。

检测数据检查：

    .\.venv\Scripts\python.exe runs/v42/check_dataset.py --dataset data/lab_items/detection/dataset.yaml --split-manifest data/lab_items/metadata/split_manifest.csv

缺数据返回awaiting_user_data及退出码2，不启动训练。检查框范围、图像、类别、精确重复、近重复候选、整段视频/采集组泄漏等；无法自动证明“所有可见目标均已标注”，需人工复核。背景中的同类器物仍按统一类别标注，业务相关性另行判断。

## 可选专用检测器训练/评价

不要把未微调的通用模型称为已验证20类模型。建议以兼容固定版本的小型YOLO11n作候选基础，实际权重由用户提供并核对摘要；不自动下载或训练。

隔离环境，不修改当前.venv：

    uv venv .venv-det --python 3.11
    uv pip install --python .\.venv-det\Scripts\python.exe -r requirements-gpu.txt -r requirements-v42.txt -r requirements-v42-detector.txt

固定可选包ultralytics==8.3.228、lap==0.5.12。此环境本轮未安装，完整依赖组合需在实际训练前验证，不宣称已通过GPU训练。

数据真实复核后，替换以下占位路径/摘要再执行：

    .\.venv-det\Scripts\python.exe runs/v42/train_detector.py --dataset <dataset.yaml> --split-manifest <split_manifest.csv> --base-weights <基础权重.pt> --base-sha256 <实际SHA256> --annotations-reviewed --epochs 50 --imgsz 640 --batch 2

实际训练会保存数据检查、版本、种子、参数、基础/最佳权重摘要及独立test结果。未准备数据和基础权重时不会伪造best.pt。

已有训练权重独立测试：

    .\.venv-det\Scripts\python.exe runs/v42/train_detector.py --dataset <dataset.yaml> --split-manifest <split_manifest.csv> --base-weights <已训练权重.pt> --base-sha256 <实际SHA256> --annotations-reviewed --evaluate-only

推理时custom_class_map.json必须明确将所有模型类别映射到本次Registry类别，并覆盖配置的目标范围。选择官方ByteTrack/BoT-SORT适配器时，本版要求每个采样点有真实检测；不能把空检测的运动预测当作新的视觉观测。第一人称实际效果需要独立视频对比。

## 测试与独立核验

    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\python.exe runs/v42/validate.py --config .\output\benchmarks\v42_75_105\run.yaml
    .\.venv\Scripts\python.exe runs/v42/check_cache.py --config .\output\benchmarks\v42_75_105\run.yaml
    .\.venv\Scripts\python.exe runs/v42/report.py --config .\output\benchmarks\v42_75_105\run.yaml

当前86项测试通过。测试中的合成记录、小网络或虚拟检测器仅用于独立契约/编排验证，不生成正式业务结果。

validate检查三表逐单元格、真实Excel日期类型、公开引用、DAG、所有阶段摘要和每一帧视频相对PTS，并生成首/中/尾预览。它不证明模型类别、文字和动作预测正确。

首次真实模型计时保存在diagnostics/first_real_*_metrics.json；后续缓存复跑的阶段耗时单独记录。旧V4.1短片为25秒，且参数/硬件状态不同，不计算未经控制的整体提速倍数。
