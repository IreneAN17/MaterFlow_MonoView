def load_config(config_path):
    """用途：读取并校验本次实验配置，统一解析配置中的路径。

    输入：config_path 为本次 run 配置文件路径。
    预期输出：通过校验的配置，沿用项目现有字段与数据结构。
    TODO：实现配置读取、必要字段校验和路径解析；--config 相对当前工作
    目录，YAML 内相对路径按 README 约定相对项目根目录，绝对路径保留。
    需确认 registry、manifest、仪器配置、动作规则和状态规则的路径；
    人工参考路径仅供评价阶段使用，不把参考内容加入识别配置。
    """
    raise NotImplementedError("TODO: 实现具体功能")
