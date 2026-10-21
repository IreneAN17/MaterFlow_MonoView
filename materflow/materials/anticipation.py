def predict_next_actions(events, step_runs, context):
    """用途：预留基于 SOP 的简单下一行动或试剂候选预测。

    输入：events 为事件记录；step_runs 为步骤执行记录；
    context 为包含 SOP 和预测时刻约定的实验上下文。
    预期输出：单独保存的行动或试剂候选、预测时刻、依据及预测状态。
    TODO：实现简单 SOP 基线并按预测时刻截断输入；需明确步骤重复、
    交叠、不确定边界与下一行动候选规则。只使用预测时刻以前已可知
    的记录及字段，不使用未来事件或事后补齐的身份、边界和读数。
    缺证字段保留 unresolved；不读取人工参考，不把预测写入已执行事件，
    不依据 SOP 补写实际操作。
    """
    raise NotImplementedError("TODO: 实现具体功能")
