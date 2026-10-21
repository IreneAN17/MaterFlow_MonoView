from .base import VisionBackend


class TransformersBackend(VisionBackend):
    """预留程序内加载固定模型的视觉后端，遵循公共后端接口。

    TODO：后续确认固定模型、加载配置与支持的输入能力；需准备材料
    标签、仪表和操作片段示例。本次不加载模型，不下载或安装依赖。
    """

    def infer_images(self, images, prompt):
        """用途：使用程序内固定模型完成独立图片推理。

        输入：images 为独立图片集合；prompt 为任务提示词。
        预期输出：与各图片对应的原始模型结果，保留后续解析依据。
        TODO：实现模型加载与独立图片推理；需确认试剂名称与别名、
        标签区域及仪表显示含义。批量图片不表示联合时序理解。
        """
        raise NotImplementedError("TODO: 实现具体功能")

    def infer_clip(self, frames, timestamps, prompt):
        """用途：预留固定模型对有序画面的联合时序分析。

        输入：frames 为有序画面；timestamps 为原视频时间；
        prompt 为片段任务提示词。
        预期输出：具有画面时间对应的原始片段理解结果。
        TODO：核验模型是否支持联合时序输入，再实现相应推理；
        需确认动作完成证据、角色和起止判据。不支持时明确报未实现，
        不将独立图片推理结果拼接后宣称已完成片段理解。
        """
        raise NotImplementedError("TODO: 实现具体功能")
