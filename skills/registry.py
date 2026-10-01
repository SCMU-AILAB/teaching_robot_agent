# skills/registry.py
"""动作名称注册与实现查找."""

from collections.abc import Iterable

from domain.validation import nonempty_string
from skills.base import RobotSkill


class SkillRegistry:
    """按唯一名称注册并查找动作实现."""

    def __init__(self, _skills: Iterable[RobotSkill] = ()) -> None:
        """初始化依赖与实例状态，不启动后台任务."""
        self._skills: dict[str, RobotSkill] = {}
        for skill in _skills:
            self.register(skill)

    def register(self, skill: RobotSkill) -> None:
        """注册唯一名称的动作实现，重复名称抛出异常."""
        name = nonempty_string(skill.name, "skill name")
        if name in self._skills:
            raise ValueError(f"skill already registered: {name}")
        self._skills[name] = skill

    def get(self, name: str) -> RobotSkill:
        """根据名称查找动作，未注册时抛出异常."""
        try:
            return self._skills[name]
        except KeyError:
            raise ValueError(f"unknown skill: {name}") from None

    def names(self) -> list[str]:
        """返回按名称排序的已注册动作列表."""
        return sorted(self._skills)
