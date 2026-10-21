def evaluate_run(result, reference, evaluation_config):
    """用途：比较自动结果与独立人工参考中的事件、身份、时间、
    仪表读数及来源边。

    输入：result 为自动实验结果；reference 为评价阶段提供的人工参考；
    evaluation_config 为匹配、容差及指标配置。
    预期输出：各评价维度的比较结果、匹配依据、误差和缺证记录。
    TODO：实现独立参考匹配与指标计算；需确认试剂名称及实物身份、
    容器初始内容、动作角色与完成证据、仪表单位及读数含义、样品状态
    与来源规则，以及重复、交叠、起止边界未知时的评价口径。
    缺证字段保留 unresolved，SOP 不补写实际操作；人工参考不得回流到
    识别、消歧、状态修正或预测，也不得据此改写自动结果。
    """
    raise NotImplementedError("TODO: 实现具体功能")


def evaluate_if_reference_available(result, config):
    """用途：仅在评价阶段按配置检查并加载可用的人工参考。

    输入：result 为已形成的自动结果；config 为本次实验及评价配置。
    预期输出：参考可用时的评价结果，或未启用、缺少参考等明确状态。
    TODO：实现评价开关、参考可用性检查、参考加载及 evaluate_run 调度，
    本次不读取文件。需确认人工标注版本、实物身份、仪表读数含义、
    动作角色与时间边界、SOP 重复交叠及来源边的独立标注规则。
    参考只能进入评价阶段，不能加入识别上下文或用于补写实际操作；
    缺证字段保留 unresolved，不以空结果伪装已完成评价。
    """
    raise NotImplementedError("TODO: 实现具体功能")
