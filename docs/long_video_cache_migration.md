# 内容缓存迁移与恢复

原始run和ID账本不改写。新run继承内容地址缓存时，先结构校验digest，推理阶段再按像素、模型、prompt/schema、生成参数重新计算请求键；两层都通过才能命中。迁移不是阶段完成，也不修改complete/signature。

只读源/dry-run：
`.venv/Scripts/python.exe runs/v42/cache_migrate.py --source output/benchmarks/v42_75_105 --config output/clear_video_optimized_20261107/run.yaml`

实际复制加`--apply`。输出migration.jsonl及migration_summary.json。目标有不同内容则保留并报告，不覆盖。旧OCR缓存缺少完整签名维度时计为legacy_ocr_unverified，不自动推测迁移；新OCR2缓存可按内容签名验证。检测、Qwen原始成功响应保留，失败原文留原目录。

旧843MB总索引不读取迁移；按旧scan目录各块meta盘点可避免整个巨型JSON驻留。本版未提供将旧轨迹状态直接升格新stage的自动迁移；改用新run重放并复用内容任务缓存。已发布编号留在原run，不混入新run。回滚代码可用output/optimization_20261107/source_backup核对，旧结果始终保持可读，禁止reset/clean或删旧缓存。


## 大型旧分区索引迁移

`runs/v42/partition_migrate.py --source <旧run> --output <独立迁移目录>`逐块读取scan/*.meta.json，不加载843MB总索引；不加--apply是dry-run。加--apply先核对源块内容SHA，将state和rejection独立保存，最后写摘要commit与紧凑JSONL索引。源文件和旧stage signature不改写，迁移目录不是已完成的推理run，不自动升格为当前版本阶段缓存。恢复/回滚保留原run。索引条目超过2KB报错，不删事实凑尺寸。
