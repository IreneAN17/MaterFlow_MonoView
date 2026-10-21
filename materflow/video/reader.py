def inspect_video(video_path):
    """用途：获取原视频时长、分辨率、帧率等基本信息。

    输入：video_path 为原始实验视频路径。
    预期输出：视频元信息及原视频引用，供抽帧与窗口划分使用。
    TODO：实现元信息获取和异常校验，明确帧索引与原视频时间的对应。
    需确认实验视频的时间基准，以及变帧率、缺帧和中断的处理约定。
    """
    raise NotImplementedError("TODO: 实现具体功能")


def read_frames(video_path, frame_indices):
    """用途：按指定帧索引读取画面，保留原视频时间与证据引用。

    输入：video_path 为视频路径；frame_indices 为待读取的帧索引。
    预期输出：带帧索引、原视频时间、图像引用和来源证据的帧记录。
    TODO：实现按索引读取、时间映射和不可读取帧的明确标记。
    需确认标签、仪表和操作完成证据的回溯要求；不得用窗口内相对时间
    替代原视频时间，证据缺失情况需明确保留。
    """
    raise NotImplementedError("TODO: 实现具体功能")
