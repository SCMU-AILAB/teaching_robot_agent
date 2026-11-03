# agent/homing.py
"""回到出发点：按里程计算出转向与距离，一段段走回陪伴开始时的位置和朝向."""

import asyncio
import logging
import math
import re

from app.team_gateway import TeamGateway
from domain.models import ActionStatus, Pose2D
from perception.mapping import OccupancyMap

logger = logging.getLogger(__name__)

# 叫机器人回来的说法；“我回来了”“等我回来”“什么时候回来”是孩子在说自己，不算
_RETURN_REQUEST = re.compile(
    r"回来|回到(原地|原来|起点|出发|刚才|开始|我|这)|回原地|返回|回去吧"
    + r"|(?<![转看拿递搬扭翻掉])过来|到我这"
)
_NOT_REQUEST = re.compile(r"我回来|等我|回来了吗|什么时候回来|回来以后|回来后")
# 把出发点改到车现在的位置；要先于“回来”判断：“以后回到这里”不是叫它现在回来
_SET_HOME_REQUEST = re.compile(
    r"记住这(里|儿)|这(里|儿)就?是(新的)?(出发点|起点)"
    + r"|把?这(里|儿)(当|设为|作为|当作|当成)(新的)?(出发点|起点)|以后(就)?回到这(里|儿)"
)
# 离出发点这么近就算到了；里程计本身有误差，再近没有意义
_ARRIVED_M = 0.15
_HEADING_TOLERANCE_RAD = 0.1
# 每段最多走这么远，走完重新读位置修正方向
_SEGMENT_M = 1.0
_MAX_SEGMENTS = 16
# 规划时与已知障碍保持的距离：车宽一半加余量
_PLAN_CLEARANCE_M = 0.2

ARRIVED_REPLY = "我回来啦！"
SET_HOME_REPLY = "好，我记住这里啦，以后叫我回来就回到这儿。"
SET_HOME_MOVING_REPLY = "我还在动呢，等我停下再跟我说一次吧。"
ALREADY_HOME_REPLY = "我就在出发的地方呀。"


def wants_set_home(text: str) -> bool:
    """孩子是否要把车现在的位置当作新的出发点."""
    return _SET_HOME_REQUEST.search(text.strip()) is not None


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

    def __init__(
        self,
        _gateway: TeamGateway,
        _task_id: int,
        _home: Pose2D,
        _map: OccupancyMap | None = None,
    ) -> None:
        """绑定任务与出发点.

        Args:
            _gateway: 核心网关.
            _task_id: 所属任务.
            _home: 陪伴开始时的位姿.
            _map: 有地图时绕开已知障碍规划；没有或找不到路时走直线.
        """
        self._gateway: TeamGateway = _gateway
        self._task_id: int = _task_id
        self._home: Pose2D = _home
        self._map: OccupancyMap | None = _map

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
            target_x, target_y = self._next_point(pose)
            leg = math.hypot(target_x - pose.x, target_y - pose.y)
            bearing = _wrap(math.atan2(target_y - pose.y, target_x - pose.x) - pose.yaw)
            if abs(bearing) > _HEADING_TOLERANCE_RAD and not await self._act(
                "turn_relative", {"angle_rad": bearing}
            ):
                return self._stuck(remaining)
            if not await self._act(
                "move_relative", {"distance_m": min(leg, _SEGMENT_M)}
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

    def _next_point(self, pose: Pose2D) -> tuple[float, float]:
        """下一个要走到的点：地图规划路径的第一个拐点，没有路径时直接是出发点."""
        if self._map is not None:
            path = self._map.plan(pose, self._home, _PLAN_CLEARANCE_M)
            if path:
                return path[0]
        return self._home.x, self._home.y

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
