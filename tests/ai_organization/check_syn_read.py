"""File checks for the SYN-READ-01 synthetic reading bundle.

Exit 0 means the valid fixtures have no file defects and the labeled fault
copy is detected. It does not mean a knowledge-content acceptance role passed
the material.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA_VERSION = "2026-11-08"
TASK_ID = "SYN-READ-01"

QUOTE_A_V1 = "固定设备 X，室温。样本 10 个。平均用时 12 秒。"
QUOTE_A_V2 = "固定设备 X，室温。样本 10 个。平均用时 14 秒。"
QUOTE_ENERGY = "能源未记录。"
QUOTE_CROSS = "后续跨设备检查未做。"
QUOTE_B = "设备 Y，低温。样本 8 个。平均用时 15 秒。"
QUOTE_FAILURE = "发生一次失败。原因未知。没有重复。"
QUOTE_COMMENT = "评论者认为甲可能更快，并希望做长期批处理。"
QUOTE_NO_MEASUREMENT = "这条评论没有新的测量。"
QUOTE_NOT_COMMITMENT = "它不是使用者的承诺。"
QUOTE_COST = "成本没有数据。"
QUOTE_ENERGY_ABSENCE = "能源没有数据。"
QUOTE_STABILITY = "长期稳定没有数据。"
QUOTE_PREFERENCE = "用户偏好没有数据。"
QUOTE_CORRECTION = "记录 A 的平均用时由 v1 的 12 秒更正为 14 秒。v1 保留，不删除。"

ABSENCE_ITEMS = ("cost", "energy", "long_term_stability", "user_preference")
ABSENCE_QUOTES = {
    "cost": QUOTE_COST,
    "energy": QUOTE_ENERGY_ABSENCE,
    "long_term_stability": QUOTE_STABILITY,
    "user_preference": QUOTE_PREFERENCE,
}
UNAFFECTED = (
    "device",
    "temperature",
    "sample_count",
    "direct_comparison_prohibited",
    "absences",
    "failure",
    "comment",
)
IMPACT_STATUS = {"needs_update", "pending_recheck", "not_valid_for_v2"}
REQUIRED_FAULT_CODES = {
    "AUTHOR_ACCEPTANCE_COLLAPSED",
    "CLASS_MIXED",
    "COMMENT_AS_COMMITMENT",
    "CROSS_DEVICE_DONE",
    "DIRECT_COMPARISON",
    "FAILURE_CAUSE_FILLED",
    "FAILURE_REPEATED",
    "IMPACT_NOT_TRACKED",
    "MISSING_AS_ZERO",
    "MISSING_FILLED",
    "NEW_MEASUREMENT_INVENTED",
    "SCRIPT_PASS_AS_ACCEPTANCE",
    "SOURCE_VALUE_MISMATCH",
    "VERSION_OVERWRITE",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def registry_ids() -> set[str]:
    registry = load_json(repo_root() / "docs" / "ai-organization" / "role-registry.json")
    return {role["role_id"] for role in registry["roles"]}


class Report:
    def __init__(self):
        self.defects = []

    def add(self, code: str, detail: str):
        self.defects.append({"code": code, "detail": detail})

    @property
    def codes(self) -> set[str]:
        return {item["code"] for item in self.defects}


def _claim_map(claims):
    return {claim.get("id"): claim for claim in claims}


def parse_source(text: str, version: str) -> dict:
    report = Report()
    required = [
        QUOTE_ENERGY,
        QUOTE_CROSS,
        QUOTE_B,
        QUOTE_FAILURE,
        QUOTE_COMMENT,
        QUOTE_NO_MEASUREMENT,
        QUOTE_NOT_COMMITMENT,
        QUOTE_COST,
        QUOTE_ENERGY_ABSENCE,
        QUOTE_STABILITY,
        QUOTE_PREFERENCE,
    ]
    if version == "v1":
        required.append(QUOTE_A_V1)
        if QUOTE_A_V2 in text:
            report.add("SOURCE_VALUE_MISMATCH", "v1 源同时写了更正后的 14 秒")
        mean_a = 12
    elif version == "v2":
        required.extend([QUOTE_A_V2, QUOTE_CORRECTION])
        if QUOTE_A_V1 in text:
            report.add("SOURCE_VALUE_MISMATCH", "v2 源仍把 12 秒写成当前平均用时")
        mean_a = 14
    else:
        report.add("SOURCE_VALUE_MISMATCH", f"未知源版本 {version}")
        mean_a = None
    for snippet in required:
        if snippet not in text:
            report.add("SOURCE_FIDELITY", f"源缺少：{snippet}")
    return {
        "mean_a": mean_a,
        "mean_b": 15,
        "samples_a": 10,
        "samples_b": 8,
        "device_a": "X",
        "device_b": "Y",
        "temperature_a": "room",
        "temperature_b": "low",
        "report": report,
    }


def _speed_allowed(statement_class: str) -> bool:
    return statement_class in {"comment", "hypothesis"}


def check_comparison(value, statement_class: str, report: Report, where: str):
    if value in (None, "", []):
        return
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if "不能直接比较" in text or "不可直接比较" in text:
        return
    if any(token in text for token in ("优于", "劣于", "更好", "更差", "更快", "更慢")):
        if _speed_allowed(statement_class) and "可能更快" in text:
            return
        report.add("DIRECT_COMPARISON", where)


def check_absence_value(item: str, value, report: Report):
    if value in (0, 0.0, "0"):
        report.add("MISSING_AS_ZERO", item)
        return
    if value not in {"no_data", "not_recorded"}:
        report.add("MISSING_FILLED", f"{item}={value!r}")


def check_claims(record: dict, parsed: dict, source_text: str, report: Report):
    claims = _claim_map(record.get("claims", []))
    expected_ids = {"A", "B", "failure", "C"}
    if set(claims) != expected_ids:
        report.add("SOURCE_FIDELITY", f"整理声明标识是 {sorted(claims)}")
    claim_a = claims.get("A", {})
    if claim_a.get("mean_seconds") != parsed["mean_a"]:
        report.add(
            "SOURCE_VALUE_MISMATCH",
            f"A.mean_seconds={claim_a.get('mean_seconds')} source={parsed['mean_a']}",
        )
    if claim_a.get("device") != "X" or claim_a.get("temperature") != "room":
        report.add("CONDITION_DROPPED", "A 的设备或温度")
    if claim_a.get("sample_count") != 10:
        report.add("CONDITION_DROPPED", "A 的样本数")
    if claim_a.get("energy") != "not_recorded":
        check_absence_value("A.energy", claim_a.get("energy"), report)
    if claim_a.get("cross_device_check") != "not_done":
        report.add("CROSS_DEVICE_DONE", str(claim_a.get("cross_device_check")))
    quote_a = QUOTE_A_V1 if parsed["mean_a"] == 12 else QUOTE_A_V2
    if claim_a.get("source_quote") != quote_a or quote_a not in source_text:
        report.add("SOURCE_FIDELITY", "A 的引用与源不一致")
    if claim_a.get("class") != "fact":
        report.add("CLASS_MIXED", "A")

    claim_b = claims.get("B", {})
    if claim_b.get("mean_seconds") != 15 or claim_b.get("sample_count") != 8:
        report.add("SOURCE_VALUE_MISMATCH", "B 的数字")
    if claim_b.get("device") != "Y" or claim_b.get("temperature") != "low":
        report.add("CONDITION_DROPPED", "B 的设备或温度")
    if claim_b.get("source_quote") != QUOTE_B or QUOTE_B not in source_text:
        report.add("SOURCE_FIDELITY", "B 的引用与源不一致")
    if claim_b.get("class") != "fact":
        report.add("CLASS_MIXED", "B")

    failure = claims.get("failure", {})
    if failure.get("cause") != "unknown":
        report.add("FAILURE_CAUSE_FILLED", str(failure.get("cause")))
    if failure.get("repeated") is not False:
        report.add("FAILURE_REPEATED", str(failure.get("repeated")))
    if failure.get("count") != 1:
        report.add("SOURCE_VALUE_MISMATCH", "失败次数")
    if failure.get("source_quote") != QUOTE_FAILURE:
        report.add("SOURCE_FIDELITY", "失败记录的引用")
    if failure.get("class") != "fact":
        report.add("CLASS_MIXED", "failure")

    comment = claims.get("C", {})
    if comment.get("class") not in {"comment", "hypothesis"}:
        report.add("CLASS_MIXED", "评论被写成其他类别")
    if comment.get("user_commitment") is not False:
        report.add("COMMENT_AS_COMMITMENT", str(comment.get("user_commitment")))
    if comment.get("new_measurement") is not False:
        report.add("NEW_MEASUREMENT_INVENTED", str(comment.get("new_measurement")))
    if comment.get("source_quote") != QUOTE_COMMENT:
        report.add("SOURCE_FIDELITY", "评论引用")
    check_comparison(comment.get("source_quote"), comment.get("class", ""), report, "claim C")
    check_comparison(record.get("direct_comparison"), "fact", report, "arrangement.direct_comparison")

    absences = {item.get("item"): item for item in record.get("absences", [])}
    if set(absences) != set(ABSENCE_ITEMS):
        report.add("SOURCE_FIDELITY", "缺失项不完整")
    for item_id, quote in ABSENCE_QUOTES.items():
        entry = absences.get(item_id, {})
        check_absence_value(item_id, entry.get("value"), report)
        if entry.get("source_quote") != quote or quote not in source_text:
            report.add("SOURCE_FIDELITY", f"缺失引用 {item_id}")


def check_analysis(record: dict, mean_a: int, report: Report):
    statements = _claim_map(record.get("statements", []))
    needed = {"A", "B", "failure", "C", "no-compare", "cross", "absences"}
    if not needed.issubset(statements):
        report.add("SOURCE_FIDELITY", f"分析缺少 {sorted(needed - set(statements))}")
    fact_a = statements.get("A", {})
    if fact_a.get("class") != "fact" or fact_a.get("mean_seconds") != mean_a:
        report.add("SOURCE_VALUE_MISMATCH", "分析中的 A")
    if fact_a.get("device") != "X" or fact_a.get("temperature") != "room" or fact_a.get("sample_count") != 10:
        report.add("CONDITION_DROPPED", "分析中的 A 条件")
    fact_b = statements.get("B", {})
    if (
        fact_b.get("class") != "fact"
        or fact_b.get("mean_seconds") != 15
        or fact_b.get("device") != "Y"
        or fact_b.get("temperature") != "low"
        or fact_b.get("sample_count") != 8
    ):
        report.add("CONDITION_DROPPED", "分析中的 B")
    guard = statements.get("no-compare", {})
    guard_text = guard.get("text", "")
    if guard.get("class") != "interpretation" or "不能直接比较" not in guard_text:
        report.add("DIRECT_COMPARISON", "分析没有把不可直接比较写成解释")
    comment = statements.get("C", {})
    if comment.get("class") != "hypothesis":
        report.add("CLASS_MIXED", "评论者判断的分析类别")
    if "可能更快" not in comment.get("text", ""):
        report.add("SOURCE_FIDELITY", "分析未保留评论者原话")
    if comment.get("user_commitment") is not False:
        report.add("COMMENT_AS_COMMITMENT", "分析把评论当成承诺")
    if comment.get("new_measurement") is not False:
        report.add("NEW_MEASUREMENT_INVENTED", "分析把评论当成新测量")
    failure = statements.get("failure", {})
    if failure.get("cause") != "unknown":
        report.add("FAILURE_CAUSE_FILLED", "分析填写了失败原因")
    if failure.get("repeated") is not False:
        report.add("FAILURE_REPEATED", "分析把失败写成重复")
    cross = statements.get("cross", {})
    if cross.get("cross_device_check") != "not_done":
        report.add("CROSS_DEVICE_DONE", "分析中的跨设备检查")
    absences = statements.get("absences", {})
    if absences.get("class") != "absence":
        report.add("CLASS_MIXED", "缺失项类别")
    for item_id in ABSENCE_ITEMS:
        check_absence_value(item_id, absences.get(item_id), report)
    check_comparison(record.get("direct_comparison"), "fact", report, "analysis.direct_comparison")
    for statement in record.get("statements", []):
        if statement.get("class") == "fact":
            check_comparison(statement.get("text"), "fact", report, f"analysis fact {statement.get('id')}")


def check_acceptance(record: dict, version: str, ordinal: int, report: Report):
    if record.get("checked_version") != version:
        report.add("SOURCE_VALUE_MISMATCH", "验收记录核对的版本")
    if record.get("recheck_ordinal") != ordinal:
        report.add("RECHECK_LIMIT", f"ordinal={record.get('recheck_ordinal')}")
    if record.get("author_self_check") == "independent_pass":
        report.add("AUTHOR_ACCEPTANCE_COLLAPSED", "作者自查被写成独立通过")
    professional = record.get("professional_acceptance") or {}
    if professional.get("status") != "not_performed":
        report.add("AUTHOR_ACCEPTANCE_COLLAPSED", f"status={professional.get('status')}")
    if professional.get("performed_by_author") is not False:
        report.add("AUTHOR_ACCEPTANCE_COLLAPSED", "验收由作者执行")
    if professional.get("script_pass_counts_as_acceptance") is not False:
        report.add("SCRIPT_PASS_AS_ACCEPTANCE", "脚本通过被当成专业验收")
    if professional.get("role") != "knowledge-content-acceptance":
        report.add("SOURCE_FIDELITY", "验收岗位")
    for key in ("technical_check", "user_acceptance", "action_authorization"):
        if record.get(key) in (None, "", "completed", "accepted"):
            report.add("AUTHOR_ACCEPTANCE_COLLAPSED", f"{key} 被合并成完成")
    if record.get("bot_execution") is not False:
        report.add("BOT_EXECUTION_CLAIMED", "acceptance")


def check_version_change(record: dict, report: Report):
    change = record.get("change") or {}
    if record.get("retains_v1") is not True:
        report.add("VERSION_OVERWRITE", "未保留 v1")
    if change.get("from") != 12 or change.get("to") != 14 or change.get("field") != "A.mean_seconds":
        report.add("SOURCE_VALUE_MISMATCH", "版本更正不是 12 到 14")
    impact = record.get("impact") or []
    found = {item.get("artifact"): item.get("status") for item in impact}
    for artifact in ("arrangement-v1", "analysis-v1", "acceptance-v1"):
        status = found.get(artifact)
        if status not in IMPACT_STATUS:
            report.add("IMPACT_NOT_TRACKED", f"{artifact}={status}")
    if record.get("recheck_count") != 1:
        report.add("RECHECK_LIMIT", f"recheck_count={record.get('recheck_count')}")
    unaffected = set(record.get("unaffected") or [])
    if not set(UNAFFECTED).issubset(unaffected):
        report.add("IMPACT_NOT_TRACKED", "未列出不受影响的条件")
    if record.get("bot_execution") is not False:
        report.add("BOT_EXECUTION_CLAIMED", "version_change")


def check_handoff(record: dict, known_roles: set[str], report: Report):
    if record.get("bot_execution") is not False:
        report.add("BOT_EXECUTION_CLAIMED", "handoff")
    if record.get("handoff_ack") != "unknown":
        report.add("FALSE_SYNC", str(record.get("handoff_ack")))
    for key in ("from_role", "to_role"):
        if record.get(key) not in known_roles:
            report.add("SOURCE_FIDELITY", f"交接岗位不在注册表：{record.get(key)}")
    if record.get("task_id") != TASK_ID or record.get("schema_version") != SCHEMA_VERSION:
        report.add("SOURCE_FIDELITY", "交接标识")


def check_common(record: dict, version: str, report: Report):
    if record.get("schema_version") != SCHEMA_VERSION:
        report.add("SOURCE_FIDELITY", "schema_version")
    if record.get("task_id") != TASK_ID:
        report.add("SOURCE_FIDELITY", "task_id")
    if record.get("synthetic") is not True:
        report.add("SOURCE_FIDELITY", "synthetic")
    if record.get("fault_injection") is not False:
        report.add("FAULT_LABEL_MISSING", "有效样本标成了故障注入")
    if record.get("version") not in {version, None} and record.get("source_version") != version:
        if record.get("version") != version:
            report.add("SOURCE_VALUE_MISMATCH", "记录版本")


def check_version_dir(root: Path, version: str) -> list[dict]:
    report = Report()
    source_text = (root / version / "source.md").read_text(encoding="utf-8")
    parsed = parse_source(source_text, version)
    report.defects.extend(parsed["report"].defects)
    arrangement = load_json(root / version / "arrangement.json")
    analysis = load_json(root / version / "analysis.json")
    acceptance = load_json(root / version / "acceptance.json")
    handoff = load_json(root / version / "handoff.json")
    for record in (arrangement, analysis, acceptance, handoff):
        check_common(record, version, report)
    check_claims(arrangement, parsed, source_text, report)
    check_analysis(analysis, parsed["mean_a"], report)
    ordinal = 0 if version == "v1" else 1
    check_acceptance(acceptance, version, ordinal, report)
    check_handoff(handoff, registry_ids(), report)
    if version == "v2":
        change = load_json(root / version / "version-change.json")
        check_common(change, version, report)
        check_version_change(change, report)
        if not (root / "v1" / "source.md").is_file():
            report.add("VERSION_OVERWRITE", "v1 源文件不存在")
    return report.defects


def check_fault(path: Path, source_v1: str) -> list[dict]:
    report = Report()
    record = load_json(path)
    if record.get("fault_injection") is not True:
        report.add("FAULT_LABEL_MISSING", "fault_injection")
    label = record.get("fault_label", "")
    if "故意错误副本" not in label:
        report.add("FAULT_LABEL_MISSING", "fault_label")
    if record.get("synthetic") is not True or record.get("task_id") != TASK_ID:
        report.add("SOURCE_FIDELITY", "故障副本标识")
    parsed = parse_source(source_v1, "v1")
    report.defects.extend(item for item in parsed["report"].defects if item["code"] != "SOURCE_VALUE_MISMATCH")
    check_claims(record, parsed, source_v1, report)
    check_acceptance(record.get("acceptance") or {}, "v1", 0, report)
    versioning = record.get("versioning") or {}
    if versioning.get("retains_v1") is not True:
        report.add("VERSION_OVERWRITE", "故障副本未保留旧版")
    if not versioning.get("impact"):
        report.add("IMPACT_NOT_TRACKED", "故障副本没有影响清单")
    check_comparison(record.get("direct_comparison"), "fact", report, "fault.direct_comparison")
    return report.defects


def check_task(path: Path) -> list[dict]:
    report = Report()
    record = load_json(path)
    if record.get("schema_version") != SCHEMA_VERSION or record.get("task_id") != TASK_ID:
        report.add("SOURCE_FIDELITY", "任务记录标识")
    if record.get("synthetic") is not True or record.get("fixture_only") is not True:
        report.add("BOT_EXECUTION_CLAIMED", "任务记录不像合成夹具")
    if record.get("actually_executed_by_bots") != []:
        report.add("BOT_EXECUTION_CLAIMED", "任务记录声称有机器人执行")
    if record.get("recheck_limit") != 1:
        report.add("RECHECK_LIMIT", "任务复验上限")
    if set(record.get("versions") or []) != {"v1", "v2"}:
        report.add("SOURCE_FIDELITY", "版本列表")
    required_roles = {
        "task-planning",
        "plan-review",
        "reading-arrangement",
        "knowledge-analysis",
        "knowledge-content-acceptance",
    }
    if set(record.get("baseline_roles") or []) != required_roles:
        report.add("SOURCE_FIDELITY", "首批岗位清单")
    if record.get("roles_impersonated") is not False:
        report.add("BOT_EXECUTION_CLAIMED", "夹具假装岗位已经执行")
    return report.defects


def evaluate(root: Path) -> dict:
    v1 = check_version_dir(root, "v1")
    v2 = check_version_dir(root, "v2")
    fault = check_fault(root / "fault" / "error-copy.json", (root / "v1" / "source.md").read_text(encoding="utf-8"))
    task = check_task(root / "task.json")
    fault_codes = {item["code"] for item in fault}
    return {
        "v1": v1,
        "v2": v2,
        "task": task,
        "fault": fault,
        "fault_codes": sorted(fault_codes),
        "fault_codes_complete": REQUIRED_FAULT_CODES.issubset(fault_codes),
        "file_check_pass": not v1 and not v2 and not task and REQUIRED_FAULT_CODES.issubset(fault_codes),
        "professional_acceptance": "not_performed",
        "script_pass_is_not_professional_acceptance": True,
    }


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else Path(__file__).resolve().parent / "fixtures" / "syn-read-01"
    result = evaluate(root)
    print("file_check=" + ("PASS" if result["file_check_pass"] else "FAIL"))
    print("professional_acceptance=not_performed")
    print("script_pass_is_not_professional_acceptance=true")
    for name in ("v1", "v2", "task"):
        print(f"{name}_defects={len(result[name])}")
        for item in result[name]:
            print(f"  {item['code']}: {item['detail']}")
    print("fault_codes=" + ",".join(result["fault_codes"]))
    if not result["fault_codes_complete"]:
        missing = sorted(REQUIRED_FAULT_CODES - set(result["fault_codes"]))
        print("fault_codes_missing=" + ",".join(missing))
    return 0 if result["file_check_pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
