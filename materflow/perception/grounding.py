def ground_relation_objects(frames, relations, context):
    """用途：将关系候选涉及的对象定位到对应画面。

    输入：frames 为帧记录；relations 为关系候选；context 为实验上下文。
    预期输出：带实例候选、画面位置、时间和来源证据的对象检测记录。
    TODO：实现对象定位和关系到检测的证据关联；需确认试剂瓶、容器、
    仪器和样品的外观及角色。同名对象不得直接视为同一实物，
    遮挡或证据不足时保留 unresolved。
    """
    raise NotImplementedError("TODO: 实现具体功能")


def merge_frame_detections(detections):
    """用途：合并同一帧中的重复检测，保留实例区分及证据。

    输入：detections 为包含帧引用和对象候选的检测记录。
    预期输出：合并后的同帧检测及原始候选、实例和证据对应。
    TODO：实现重复判定、候选合并与来源追踪；需确认相似试剂瓶、
    容器和仪器部件的实例区分依据，避免仅因名称相同而合并不同实物。
    无法确定的对应保留为候选或 unresolved。
    """
    raise NotImplementedError("TODO: 实现具体功能")
