# V4.2 长视频基础设施优化（2026-11-07）

第一轮保持模型、采样率、类别、阈值、OCR选择和语义事实规则。旧run保持原样；新测试位于output/benchmarks/opt_10s_20261107，clear_video新识别位于output/clear_video_optimized_20261107。

已实施：线程持有临时系统执行状态（runner和单阶段）；5秒小型status.json；OCR隔离常驻JSONL进程、CPU引擎只初始化一次、单请求在途背压、stderr持续消费；真实模型/引擎/预处理签名；同键内容合并、成功逐项缓存；图像哈希验证避免重写；分区总索引不内嵌rejected/end_state、checkpoint不带完整历史；固定长度clock epoch及独立跳变证据；FrameStore重复构造不重复计算上下文已有源哈希；语义SQLite任务状态、无效引用隔离、全部独立任务运行后标partial；V4.2事务ID分配和阶段边界兼容JSON快照。

启动：`.venv/Scripts/python.exe runs/v42/run_stages.py --config output/clear_video_optimized_20261107/run.yaml`。重试失败语义：同命令加`--from-stage semantic`。成功缓存重新验证后跳过，仅未提交/失败任务获得本轮重试次数。运行时从`status.json`查看实时进展，`run_summary.json`保存提交的完整摘要。`runs/v42/status.py`兼容两者。

尚未将已有V4.0稀疏参考对齐为本轮轨迹/ROI质量对照集，第二轮T06–T09的轨迹/ROI/任务削减未作为等价策略启用。不得宣称已解决8千天平轨迹或已实现一次解码、硬件编码、独立render阶段。每块meta仍保留恢复数据，旧全局拒识文件兼容保留；后续可进一步去重。OCR仍逐图推理，没有宣称张量batch；一个在途请求保证有界传输，但全片OCR候选列表和语义计划尚未完成分块流式重构。

长片人工准确率、10–15分钟代表集、3小时内存/速度验收仍未建立。10秒只验证机制和真实模型连通。缺项时报告partial，不补造事件/谱系。

## 既有参考核对补充

Video_sample/Clear Video内已有V4.0结构化参考，不应称为完全没有人工参考。只读检查确认其中事件source_id仍为example_video，观察source_id为clear_video；start_s/end_s实际为拍摄日期时间字符串，并使用预登记容器身份。需明确字段、source别名和自动身份对齐后才能做正式评价。本轮未把这些参考输入模型。参考中的7.297g未被当前自动称量结果恢复；自动结果出现50g，存在将背景包装标签误归为仪器读数的问题，不能将输出称作质量验收通过。旧参考不提供本轮已完成验收的稠密框/轨迹/短暂读数覆盖集合。详见全片diagnostics/legacy_reference_audit.json。
