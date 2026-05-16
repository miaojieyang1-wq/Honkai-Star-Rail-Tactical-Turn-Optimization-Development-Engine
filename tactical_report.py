# -*- coding: utf-8 -*-
"""Tactical report generation with an optional LLM polishing adapter."""

from dataclasses import dataclass
from typing import Protocol, Tuple

from battle_engine import TacticalAdvice


class LLMReportClient(Protocol):
    """Minimal interface for an LLM text generation client."""

    def generate(self, prompt):
        """Return generated report text for the supplied prompt."""


@dataclass(frozen=True)
class TacticalReport:
    """Structured tactical report delivered to players or portfolio reviewers."""

    title: str
    baseline_axis: Tuple[str, ...]
    estimated_total_damage: float
    hit_branch_lines: Tuple[str, ...]
    assumptions: Tuple[str, ...]
    boundary_notice: str
    llm_text: str = ""

    def to_markdown(self):
        """Render the report as Markdown."""
        lines = [
            "# {0}".format(self.title),
            "",
            "## 基准轴",
            "",
            _join_axis(self.baseline_axis),
            "",
            "## 预估收益",
            "",
            "- 目标泛函得分：{0}".format(self.estimated_total_damage),
            "",
            "## 受击变招",
            "",
        ]
        lines.extend(self.hit_branch_lines or ("- 当前窗口无受击变招分支。",))
        lines.extend(["", "## 前提假设", ""])
        lines.extend("- {0}".format(item) for item in self.assumptions)
        lines.extend(["", "## 边界说明", "", self.boundary_notice])
        if self.llm_text:
            lines.extend(["", "## LLM 润色版", "", self.llm_text])
        return "\n".join(lines)


class TemplateLLMReportClient:
    """Deterministic local stand-in for a real LLM report service."""

    def generate(self, prompt):
        """Return a concise deterministic paragraph without remote calls."""
        return (
            "基于当前搜索结果，建议优先执行基准轴；若触发受击回能，"
            "请参考对应变招分支。以下内容由本地模板生成，接入真实 LLM 后"
            "可进一步改写为自然语言战术建议书。"
        )


def build_tactical_report(advice, llm_client=None):
    """Build a tactical report from BattleEngine tactical advice."""
    if not isinstance(advice, TacticalAdvice):
        raise TypeError("advice must be a TacticalAdvice")

    hit_branch_lines = tuple(_format_hit_branch(plan) for plan in advice.hit_branch_plans)
    boundary_notice = (
        "当前仓库使用示例数据和 Mock/API 注入点验证架构。真实仇恨公式、"
        "角色机制参数和完整伤害乘区需要由企业数据中台或战斗引擎接口提供。"
    )
    prompt = _build_llm_prompt(advice, hit_branch_lines, boundary_notice)
    llm_text = llm_client.generate(prompt) if llm_client is not None else ""
    return TacticalReport(
        "星穹铁道战术建议书",
        advice.baseline_axis,
        advice.estimated_total_damage,
        hit_branch_lines,
        advice.assumptions,
        boundary_notice,
        llm_text,
    )


def _format_hit_branch(plan):
    actions = _join_axis(plan.get("actions", ()))
    return "- {0} 命中 {1}：{2}；得分变化 {3}".format(
        plan.get("branch_id", "unknown"),
        plan.get("hit_target_id", "unknown"),
        actions,
        plan.get("damage_delta", 0.0),
    )


def _join_axis(axis):
    return " -> ".join(axis) if axis else "无可执行我方行动"


def _build_llm_prompt(advice, hit_branch_lines, boundary_notice):
    return "\n".join(
        (
            "请生成一份玩家可读的星穹铁道排轴建议。",
            "基准轴：{0}".format(_join_axis(advice.baseline_axis)),
            "得分：{0}".format(advice.estimated_total_damage),
            "受击分支：{0}".format("；".join(hit_branch_lines) or "无"),
            "边界：{0}".format(boundary_notice),
        )
    )
