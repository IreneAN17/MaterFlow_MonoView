# MaterFlow V4.1 运行与验证

本版本依据本次用户修订：动作仅为 stir、transfer、hold、filter；新版标注视频不含音轨。旧 V4.0 路径和历史结果继续保留。当前代码根据 manifest.schema_version 选择管线。

## 输入与环境

使用项目 GPU 环境 `.venv/Scripts/python.exe`，Qwen3.5-4B/NF4 与已固定 commit 的 GroundingDINO、SAM2、DINOv2。不需要训练新权重。V4.1 的 Reagents 由程序读取，但不进入模型上下文，也不生成 R_* 身份或 reagent_ids。

时钟使用单独的 CPU OCR 环境，避免改变 GPU 环境：

```powershell
Set-Location -LiteralPath 'E:\Projects\MaterFlow_Monoview'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\runs\v41\setup_ocr.ps1
```

直接依赖见 requirements-ocr.txt，完整锁定依赖见 requirements-ocr.lock.txt。RapidOCR 1.4.4、ONNX Runtime 1.23.2 及其自带预训练 OCR 模型版本和 SHA256 会写入 clock_raw.json。原文始终保留；只在完整数字和日期合法时规范分隔符，缺少数字、低分或不一致时保留未知。

若固定版本视觉权重尚未缓存，可执行 `.venv/Scripts/python.exe runs/v41/download_models.py`。已有缓存无需重新下载。

## 25 秒代表片段

```powershell
.\.venv\Scripts\python.exe runs/v41/configure.py --benchmark --start 75 --seconds 25 --name v41_75_100
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\runs\v41\run.ps1 -ConfigPath .\output\benchmarks\v41_75_100\run.yaml
```

这会生成实际 manifest 与 YAML，并按独立进程执行五阶段。输出目录为 `output/benchmarks/v41_75_100/`。片内秒保留原视频 75–100 秒位置；导出短片的播放 PTS 从零开始，元数据记录其对应的源时间起点。完整模式从原视频起点输出。

## 全片配置与运行

生成配置本身不会启动全片：

```powershell
.\.venv\Scripts\python.exe runs/v41/configure.py --full
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\runs\v41\run.ps1 -ConfigPath .\output\clear_video_v4_1\run.yaml
```

全片结果写入 `output/clear_video_v4_1/`，不覆盖旧 `output/clear_video/`。模型上下文只含类别、关系、动作证据语义和指定词表，不读取人工业务行。

断点续跑与查看状态：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\runs\v41\run.ps1 -ConfigPath .\output\benchmarks\v41_75_100\run.yaml -FromStage consolidate
.\.venv\Scripts\python.exe runs/v41/status.py --config .\output\benchmarks\v41_75_100\run.yaml
Get-Content -LiteralPath .\output\benchmarks\v41_75_100\logs\consolidate.log -Tail 20 -Wait
```

`Get-Content -Wait` 是日志跟踪，计算结束后仍会等待；Ctrl+C 只结束该日志查看。五阶段均 complete 且日志出现 Completed derive 才表示该次任务完成。状态工具检查进程存活和可用的创建标识，避免把进程退出后的 running 当成正常计算。

## 缓存、编号与证据

- 多类别检测合并查询；短类别词仅在能唯一映射时接纳。类别冲突及低分结果保留筛选原因。初始 oven/container 阈值较保守，可配置；尚不能据此宣称通过独立精度验收。
- SAM2 图像批量框解码、DINOv2 批量特征及非重叠多对象视频会话减少重复计算。特征缓存原子写入并核对内容摘要，损坏后重新真实推理。
- 各阶段按输入内容、配置、模型与相关代码依赖验证缓存。Excel 样式等导出变更不触发 Qwen。缓存校验与原始冷运行计时分别记录。若运行中相关代码/配置依赖变化，阶段会失败并将可能受影响的视觉缓存隔离保存，禁止将其标记为正常完成。
- 运行时承载件编号如 beaker_1；仪器公开输出规范类别，内部轨迹保留。O/E/M/S/L 映射位于 id_map.json，失效与合并保留追踪记录。编号不随排序或续跑重置。
- raw_observations.jsonl 保留原始细粒度证据；normalized_observations.jsonl 保留绑定后的点证据；observations.jsonl 是业务聚合记录。aggregation_map.json 与 visual_evidence_index.json 支持回查。
- SAM2 传播证据明确标记，不复制 OCR、状态或物料流到未分析帧。不跨剪辑、身份冲突和可见性缺口合并。
- start_s/end_s 始终是片内秒。真实时钟见 time_mapping.json 与 time_report.md；区间定位不等于完整动作持续时间。
- M/S 由有证据的动作与物料流生成，容器出现不等于内部材料已确认。未知初态不默认空，部分转移不默认耗尽，未观察的过滤输出不生成。

## 验证与基准

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe runs/v41/validate.py --config .\output\benchmarks\v41_75_100\run.yaml
```

验证脚本逐单元格对照自动 JSON，校验引用、导出帧数、分辨率、逐帧相对 PTS 和无音轨要求，并生成 diagnostics/validation.json 与预览帧。单元测试的合成记录和随机小网络只验证代码契约，不用于正式识别或宣称准确率。

可选的同片段旧版视觉阶段基准（确实会执行预训练模型，可能耗时较长）：

```powershell
.\.venv\Scripts\python.exe -u runs/v41/baseline.py --config .\output\benchmarks\v41_75_100\run.yaml --stage ground
.\.venv\Scripts\python.exe -u runs/v41/baseline.py --config .\output\benchmarks\v41_75_100\run.yaml --stage consolidate
```

旧版基准仅复用已验证且像素完全相同的旧 V4.0 caption，记录原请求签名及来源；不将其作为 V4.1 推理结果，也不报告旧 caption 的新测耗时。定位与跟踪时间可单独对比。必须同时披露硬件状态、缓存状态、模型加载时间和参数差异，不把历史全片与新版短片计算成加速倍数。

本轮全片是否运行、代表片段实际数量和性能结果，以对应 run_summary.json、各阶段 metrics 和 benchmark_report 为准；文档中的命令不表示已经执行。


## 当前实测结果与隔离冷缓存复测

75–100 秒短片已完成新版五阶段、三表逐单元格核验与无音轨视频校验：467 原始记录筛选为 389 条规范候选，再聚合为 129 条业务 observation。6 条转移候选尚缺完整承载关系，材料和谱系为空；这不是正例谱系识别通过。63 项回归测试通过。首、中、尾画面已复核，已发现传播框漂移、容器类别混淆和轨迹断裂，见基准报告。

新版同片段独立冷缓存视觉计时可使用以下命令。默认 cold_v41 保留已有测量，重复执行会拒绝覆盖；新测量请通过 --name 指定新目录。

```powershell
.\.venv\Scripts\python.exe -u runs/v41/visual_benchmark.py --config .\output\benchmarks\v41_75_100\run.yaml --stage ground
.\.venv\Scripts\python.exe -u runs/v41/visual_benchmark.py --config .\output\benchmarks\v41_75_100\run.yaml --stage consolidate
.\.venv\Scripts\python.exe runs/v41/check_cache.py --config .\output\benchmarks\v41_75_100\run.yaml
.\.venv\Scripts\python.exe runs/v41/report.py --config .\output\benchmarks\v41_75_100\run.yaml
```

比较报告输出在短片目录的 benchmark_report.json 和 benchmark_report.md。旧版 caption 只复用像素校验通过的 25 个历史自动结果；没有新测其 caption 时间。新版使用 11 个自适应采样点，检测阈值与关联策略也不同。GPU 功率/时钟变化会影响耗时，报告不计算未经控制的整体提速倍数。

同一短片在开发续跑中保留首次分配的编号；失效记录不回收编号，因此当前 129 条 observation 可含编号间隙。全新运行从 O001/E001 和各类别 _1 开始，不能通过删除映射来给已有引用重新编号。
