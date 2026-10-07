# speech/resources.py
"""同一事件循环中的音频设备互斥，不创建真实设备."""


class AudioDeviceLease:
    """录音器和播放器注入同一实例时互斥使用音频设备."""

    def __init__(self) -> None:
        """初始化空闲状态."""
        self._owner: object | None = None

    def acquire(self, owner: object) -> None:
        """立即取得占用，忙碌时拒绝且不打断当前拥有者."""
        if self._owner is not None:
            raise RuntimeError("Audio device busy")
        self._owner = owner

    def release(self, owner: object) -> None:
        """仅允许拥有者释放自己的占用，重复释放安全."""
        if self._owner is owner:
            self._owner = None

    @property
    def busy(self) -> bool:
        """查询模拟设备是否被占用."""
        return self._owner is not None
