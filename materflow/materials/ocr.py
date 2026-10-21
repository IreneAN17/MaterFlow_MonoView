def extract_lab_observations(frames, tracks, backend, context):
    """用途：调度标签和仪表识读，形成带证据的实验观测。

    输入：frames 为帧记录；tracks 为对象轨迹；backend 为视觉后端；
    context 为不含人工参考的实验上下文。
    预期输出：与对象、原视频时间及证据关联的标签和仪表观测。
    TODO：实现区域选择、识读调度及观测汇总；需补齐试剂标签样例、
    名称与别名，以及仪器显示区域、单位和读数含义。
    缺证字段保留 unresolved，模型分数不得直接视为事实确认。
    """
    raise NotImplementedError("TODO: 实现具体功能")


def recognize_reagent_label(image, context):
    """用途：识读试剂名称与别名，保留原文字和画面证据。

    输入：image 为带来源引用的标签图像；context 为试剂及实物上下文。
    预期输出：原始文字、名称与别名候选、证据及字段状态。
    TODO：实现标签识读与候选匹配；需提供试剂名称、别名、标签样例
    和实物身份约定。标签文字与瓶内内容需分别判断，不能仅凭模糊
    外观确定实物身份；缺证或匹配不唯一时保留 unresolved。
    """
    raise NotImplementedError("TODO: 实现具体功能")


def recognize_instrument_reading(image, instrument_profile):
    """用途：识读温度、转速、重量及单位，区分不同读数含义。

    输入：image 为仪器显示图像；instrument_profile 为现有仪器定义。
    预期输出：原文字、数值、单位、读数含义、证据和字段状态。
    TODO：实现仪表区域识读与单位校验；需提供仪器显示区域、显示
    模式、单位及读数含义，区分设定值、显示值与实测值。
    不能将显示温度直接当作样品实测温度，缺失含义保留 unresolved。
    """
    raise NotImplementedError("TODO: 实现具体功能")
