# agent/homing.py
"""回到出发点：按里程计算出转向与距离，一段段走回陪伴开始时的位置和朝向."""

import asyncio
import logging
import math
import re

from app.team_gateway import TeamGateway
from domain.models import ActionStatus, Pose2D

logger = logging.getLogger(__name__)

# 叫机器人回来的说法；“我回来了”“等我回来”“什么时候回来”是孩子在说自己，不算
_RETURN_REQUEST = re.compile(
    r"回来|回到(原地|原来|起点|出发|刚才|开始|我|这)|回原地|返回|回去吧"
    + r"|(?<![转看拿递搬扭翻掉])过来|到我这"
)
_NOT_REQUEST = re.compile(r"我回来|等我|回来了吗|什么时候回来|回来以后|回来后")
# 离出发点这么近就算到了；里程计本身有误差，再近没有意义
_ARRIVED_M = 0.15
_HEADING_TOLERANCE_RAD = 0.1
# 每段最多走这么远，走完重新读位置修正方向
_SEGMENT_M = 1.0
_MAX_SEGMENTS = 12

ARRIVED_REPLY = "我回来啦！"
ALREADY_HOME_REPLY = "我就在出发的地方呀。"


def wants_return(text: str) -> bool:
    """孩子是否在叫机器人回到出发的地方."""
    stripped = text.strip()
    if _NOT_REQUEST.search(stripped):
        return False
    return _RETURN_REQUEST.search(stripped) is not None


def _wrap(angle: float) -> float:
    """把角度换算到 -π 到 π."""
    return math.atan2(math.sin(angle), math.cos(angle))


class Homing:
    """一次回到出发点的执行；动作经核心网关提交，避障由底盘守护负责."""

    def __init__(self, _gateway: TeamGateway, _task_id: int, _home: Pose2D) -> None:
        """绑定任务与出发点.

        Args:
            _gateway: 核心网关.
            _task_id: 所属任务.
            _home: 陪伴开始时的位姿.
        """
        self._gateway: TeamGateway = _gateway
        self._task_id: int = _task_id
        self._home: Pose2D = _home

    async def run(self) -> str:
        """走回出发点并转回出发时的朝向，返回给孩子的话.

        Raises:
            RuntimeError: 执行层因停车无法确认而锁定，调用方需结束任务。
        """
        home = self._home
        moved = False
        # ========== Step1: 一段段走回出发点，每段后按新位置修正方向 ==========
        for _ in range(_MAX_SEGMENTS):
            pose = await self._pose()
            remaining = math.hypot(home.x - pose.x, home.y - pose.y)
            if remaining <= _ARRIVED_M:
                break
            bearing = _wrap(math.atan2(home.y - pose.y, home.x - pose.x) - pose.yaw)
            if abs(bearing) > _HEADING_TOLERANCE_RAD and not await self._act(
                "turn_relative", {"angle_rad": bearing}
            ):
                return self._stuck(remaining)
            if not await self._act(
                "move_relative", {"distance_m": min(remaining, _SEGMENT_M)}
            ):
                return self._stuck(remaining)
            moved = True
        else:
            pose = await self._pose()
            remaining = math.hypot(home.x - pose.x, home.y - pose.y)
            if remaining > _ARRIVED_M:
                return self._stuck(remaining)
        # ========== Step2: 转回出发时的朝向 ==========
        pose = await self._pose()
        facing = _wrap(home.yaw - pose.yaw)
        if abs(facing) > _HEADING_TOLERANCE_RAD:
            moved = True
            if not await self._act("turn_relative", {"angle_rad": facing}):
                return "我回到出发的地方了，不过没能转回原来的方向。"
        return ARRIVED_REPLY if moved else ALREADY_HOME_REPLY

    @staticmethod
    def _stuck(remaining: float) -> str:
        """路上被挡住或动作失败时的说明."""
        return (
            "回去的路上有东西挡着，我先停在这儿，"
            + f"离出发的地方还有大约 {remaining:.1f} 米。你过来找我好不好？"
        )

    async def _pose(self) -> Pose2D:
        """读取当前里程计位姿."""
        return (await self._gateway.get_snapshot(self._task_id)).robot.position

    async def _act(self, skill: str, args: dict[str, object]) -> bool:
        """提交并等待动作；失败登记为已处理，执行层锁定时抛出."""
        record = await self._gateway.submit_action(self._task_id, skill, args, 10.0)
        try:
            result = await self._gateway.wait_for_action(
                self._task_id, record.action_id
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._halt(record.action_id))
            raise
        if result.status == ActionStatus.SUCCEEDED:
            return True
        logger.info(
            "[Homing._act] 回程动作未成功 skill=%s failure=%s", skill, result.failure
        )
        snapshot = await self._gateway.get_snapshot(self._task_id)
        if snapshot.blocked_reason is not None:
            raise RuntimeError(f"Runtime blocked: {snapshot.blocked_reason}")
        self._gateway.acknowledge_failures(self._task_id, (record.action_id,))
        return False

    async def _halt(self, action_id: int) -> None:
        """取消进行中的动作并等待其停止确认."""
        _ = await self._gateway.cancel_action(self._task_id, action_id)
        _ = await self._gateway.wait_for_action(self._task_id, action_id)
        self._gateway.acknowledge_failures(self._task_id, (action_id,))
