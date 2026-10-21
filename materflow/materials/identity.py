def resolve_object_identities(tracks, observations, context):
    """用途：综合 OCR、轨迹与 manifest，将画面对象绑定到实物身份。

    输入：tracks 为对象轨迹；observations 为标签与仪表观测；
    context 为包含 registry、manifest 的实验上下文。
    预期输出：实物身份对应、候选、字段证据和 unresolved 状态。
    TODO：实现多证据身份关联与冲突保留；需补齐试剂名称、别名、
    实物标识及容器初始内容。同名试剂瓶或相似容器需区分实例；
    不使用人工参考消歧，也不根据 SOP 预期试剂补齐缺证身份。
    """
    raise NotImplementedError("TODO: 实现具体功能")
