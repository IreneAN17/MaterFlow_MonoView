from .base import VisionBackend


class SGLangBackend(VisionBackend):
    """预留模型服务视觉后端，遵循公共后端接口。

    TODO：后续确认服务协议、固定模型与时序能力；需准备材料标签、
    仪表和操作片段的请求约定。本次不建立网络连接。
    """

    def infer_images(self, images, prompt, *, response_schema=None):
        """用途：预留通过模型服务进行独立图片推理。

        输入：images 为独立图片集合；prompt 为任务提示词。
        预期输出：逐图对应的服务原始结果及后续证据关联依据。
        TODO：实现服务请求、响应校验与错误反馈；需确认试剂名称、
        别名、仪表单位和显示含义。批量请求不代表联合时序理解，
        本次不发起网络调用。
        """
        raise NotImplementedError("TODO: 实现具体功能")

    def infer_clip(self, frames, timestamps, prompt):
        """用途：预留模型服务对有时间顺序画面的联合分析。

        输入：frames 为有序画面；timestamps 为原视频时间；
        prompt 为片段任务提示词。
        预期输出：服务返回的联合时序结果及画面时间对应。
        TODO：核验服务及模型支持的片段能力，再实现请求与结果校验；
        需确认动作角色、完成证据和起止判据。不支持时明确报未实现，
        不使用逐图服务调用伪装片段理解，本次不建立网络连接。
        """
        raise NotImplementedError("TODO: 实现具体功能")
