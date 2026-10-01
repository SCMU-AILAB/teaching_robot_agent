from collections.abc import Iterable

from domain.validation import nonempty_string
from skills.base import RobotSkill


class SkillRegistry:
    def __init__(self, skills: Iterable[RobotSkill] = ()) -> None:
        self._skills: dict[str, RobotSkill] = {}
        for skill in skills:
            self.register(skill)

    def register(self, skill: RobotSkill) -> None:
        name = nonempty_string(skill.name, "skill name")
        if name in self._skills:
            raise ValueError(f"skill already registered: {name}")
        self._skills[name] = skill

    def get(self, name: str) -> RobotSkill:
        try:
            return self._skills[name]
        except KeyError:
            raise ValueError(f"unknown skill: {name}") from None

    def names(self) -> list[str]:
        return sorted(self._skills)
