def propose_lab_events(windows, tracks, identities, backend, context):
    """用途：提出 add、transfer、mix、start、stop、return 等事件候选。

    输入：windows 为分析窗口；tracks 为轨迹；identities 为身份候选；
    backend 为视觉后端；context 为实验上下文。
    预期输出：带动作、时间、角色候选、完成证据和待解决项的候选。
    TODO：实现片段证据组织与事件候选生成；需明确各动作的来源、
    目标、操作者、完成证据及起止判据，区分实际加料与拿起后放回。
    SOP 仅作软先验，不补写实际操作；缺证字段保留 unresolved。
    """
    raise NotImplementedError("TODO: 实现具体功能")


def resolve_event_candidates(candidates, observations, context):
    """用途：关联动作、来源、目标及字段证据，保留事件歧义。

    输入：candidates 为事件候选；observations 为实验观测；
    context 为角色、动作与实物规则上下文。
    预期输出：带字段证据、候选歧义和 unresolved 状态的事件记录。
    TODO：实现候选关联、重复判定及冲突记录；需补齐动作角色、
    完成证据、起止判据和读数含义。不把候选分数当作确认概率，
    不以 SOP 或人工参考补写实际事件、来源、目标或时间边界。
    """
    raise NotImplementedError("TODO: 实现具体功能")
