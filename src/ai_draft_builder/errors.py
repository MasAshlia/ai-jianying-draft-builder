class UserFacingError(Exception):
    """可直接展示给剪辑师的中文错误。"""


class EnvironmentCheckError(UserFacingError):
    """本机剪映环境不满足生成条件。"""


class MediaProbeError(UserFacingError):
    """素材无法读取或不符合要求。"""


class DraftBuildError(UserFacingError):
    """草稿构建、校验或安装失败。"""

