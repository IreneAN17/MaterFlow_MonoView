def load_run_context(config):
    """用途：整理 registry、manifest、视频与输出路径等识别上下文。

    输入：config 为经过校验的本次实验配置。
    预期输出：沿用现有字段的实验上下文，不包含人工参考内容。
    TODO：实现 registry 与 manifest 的读取、引用校验和路径关联；
    需补齐试剂名称、别名与实物身份、容器初始内容、仪器显示定义、
    动作规则、状态与来源规则及 SOP。人工参考只能在评价阶段加载，
    不得进入识别、身份消歧、事件判定或预测上下文。
    """
    raise NotImplementedError("TODO: 实现具体功能")
