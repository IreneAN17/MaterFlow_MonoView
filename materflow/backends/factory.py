def create_backend(backend_config):
    """用途：根据配置选择并创建视觉后端。

    输入：backend_config 为后端类型、固定模型及能力要求的配置。
    预期输出：遵循现有 VisionBackend 接口的后端实例。
    TODO：实现后端选择、配置校验及能力检查，按需加载后端实现。
    需确认标签与仪表识读、操作片段理解所需能力；不支持联合时序时
    应明确标记或报错。本次不实例化模型、不导入实际模型依赖。
    """
    raise NotImplementedError("TODO: 实现具体功能")
