class VisionBackend:
    """视觉后端公共接口，区分独立图片推理与联合时序片段理解。

    TODO：后续约定后端能力和输出格式；需提供标签、仪表与操作片段
    示例，明确各任务所需画面证据。本次不加载模型。
    """

    def infer_images(self, images, prompt):
        """用途：对独立图片进行推理，批量输入不代表联合时序理解。

        输入：images 为独立图片集合；prompt 为任务提示词。
        预期输出：可逐图对应的原始推理结果，供解析模块保留证据。
        TODO：实现独立图片推理接口；需确认试剂标签、仪器显示区域、
        单位及读数含义的提示约定。不得把图片批处理描述为片段理解。
        """
        raise NotImplementedError("TODO: 实现具体功能")

    def infer_clip(self, frames, timestamps, prompt):
        """用途：联合分析具有时间顺序的画面及其原视频时间。

        输入：frames 为有序画面；timestamps 为逐帧原视频时间；
        prompt 为片段分析提示词。
        预期输出：基于联合时序理解的原始结果及其输入时间对应。
        TODO：实现能力检查和联合时序推理；需确认动作角色、完成证据
        与起止判据。不支持片段理解时明确报未实现，不以逐图调用
        infer_images 伪装成联合时序理解。
        """
        raise NotImplementedError("TODO: 实现具体功能")
