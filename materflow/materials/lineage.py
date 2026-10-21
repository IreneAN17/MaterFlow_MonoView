def reconstruct_lineage(events, context):
    """用途：根据实际事件重建样品版本和来源边，传递未知来源。

    输入：events 为带角色、状态与证据的事件；context 为来源规则上下文。
    预期输出：样品版本、来源边及其触发事件、证据和未知项。
    TODO：实现版本生成、来源边关联与未知依赖传递；需明确容器
    初始内容、加料、转移、混合及取样的样品状态转换和来源规则。
    来源缺证保留 unresolved，不以 SOP 推定来源；谱系草稿需保留
    审校状态，未核实的来源不得标为已确认事实。
    """
    raise NotImplementedError("TODO: 实现具体功能")
