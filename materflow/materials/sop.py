def align_events_to_sop(events, context):
    """用途：对齐实际事件与 SOP，允许重复、跳过、偏离及未确定。

    输入：events 为实际事件记录；context 为包含 SOP 的实验上下文。
    预期输出：带执行次数、关联事件、时间边界和状态的步骤记录。
    TODO：实现事件到步骤的候选对应；需明确步骤动作角色、
    完成证据、重复与交叠规则及不确定边界。SOP 只作软先验，
    不补写实际操作；未观察到步骤时保留未确定，缺证字段为 unresolved。
    """
    raise NotImplementedError("TODO: 实现具体功能")


def calculate_step_durations(step_runs):
    """用途：按明确的时间定义计算步骤历时，支持未知边界与交叠。

    输入：step_runs 为带重复执行记录、时间边界及证据的步骤记录。
    预期输出：步骤历时、所用时间定义、边界状态和证据对应。
    TODO：实现历时计算及未知边界处理；需确认主动操作时间、
    过程持续时间、重复步骤和交叠步骤的统计口径。起止缺证时
    保留 unresolved，不伪造精确时长；交叠区间不直接累加为实验总时长。
    """
    raise NotImplementedError("TODO: 实现具体功能")
