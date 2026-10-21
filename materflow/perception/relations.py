def extract_frame_relations(frames, backend, context):
    """用途：根据画面与提示词生成交互关系候选。

    输入：frames 为带时间与证据的帧；backend 为视觉后端；
    context 为不含人工参考的实验识别上下文。
    预期输出：带原始模型输出、主体、关系、客体和证据的关系候选。
    TODO：实现提示词组织、后端调用和候选收集；需确认材料动作术语、
    来源与目标角色及可见证据。独立图片推理不能冒充联合时序理解，
    画面不足时保留候选歧义，不用 SOP 补写实际操作。
    """
    raise NotImplementedError("TODO: 实现具体功能")


def parse_relation_output(raw_output):
    """用途：解析并校验关系三元组，同时保留原始模型输出。

    输入：raw_output 为后端返回的原始关系识别内容。
    预期输出：经结构校验的关系候选、原始输出和未解决字段。
    TODO：实现三元组解析、字段校验与解析错误记录；需确认实验动作
    词汇及主体、关系、客体的字段约定。缺证或无法确定的字段保留
    unresolved，不丢弃原始文本，也不虚构对象或关系以通过校验。
    """
    raise NotImplementedError("TODO: 实现具体功能")
