"""Populate the running local candidate with a safe Dongsheng demo summary.

This command is intentionally limited to the already-running, loopback-only
analysis-report demo.  It does not copy the historical PDF/DOC/JPG originals.
Only the previously curated, PII-free demo summary is persisted, and every
health score remains labelled as local evidence rather than a formal result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import timezone
from pathlib import Path
from typing import Any

from infra.f1 import analysis_report_uat as uat
from infra.f1 import local_seed
from infra.f1.migrate_f1 import _bootstrap_dsn


DEMO_NAMESPACE = uuid.UUID("c99d71fd-827c-47cb-9d10-b75fb98f7910")
CREATE_REQUEST_ID = uuid.uuid5(DEMO_NAMESPACE, "dongsheng-report-create-v1")
GENERATION_REQUEST_ID = uuid.uuid5(DEMO_NAMESPACE, "dongsheng-report-generate-v1")
CLIENT_DISPLAY_NAME = "池州东升药业有限公司（脱敏测试）"
CLIENT_MATERIAL_TITLE = "池州东升药业环保资料摘要（脱敏演示）"
CLIENT_MATERIAL_BODY = (
    "LOCAL_FIXTURE 脱敏演示摘要：池州东升药业有限公司位于安徽省池州市东至县，"
    "行业类别为化学药品原料药制造，本摘要不含联系人、电话、签章、证照号码或原始文件。"
    "历史资料目录共登记二十四个企业文件，其中二十个 PDF、两个旧版 DOC、一个 DOCX 和一张 JPG，"
    "二十个 PDF 共二百一十三页；这些统计只用于测试资料管理链路。"
    "资料范围包含一份 VOCs 一企一策治理方案、环评与验收批复、排污许可证图片、"
    "十二份二零二零年月度废水废气检测报告、一份二零二零年十月 LDAR 报告、"
    "废水废气治理方案、专家意见和综合治理年度实施计划。"
    "一企一策治理方案共四十九页，覆盖企业基本情况、产排污环节、控制现状、治理措施、监测要求和减排估算。"
    "环评、验收批复与排污许可证已经出现在资料目录中，但多为扫描件或图片，证号、有效期和适用范围仍待 OCR 与人工复核。"
    "十二份月度检测报告覆盖废水 pH、COD、氨氮、亚硝酸盐氮、二氯甲烷以及无组织废气甲苯、甲醇等因子。"
    "二零二零年十月 LDAR 报告登记五百四十三个设备密封点，资料摘要记载当次检测未发现超过泄漏阈值的点。"
    "现有材料缺少后续年度 LDAR 检测与泄漏修复闭环记录，也未见连续的吸收塔、活性炭、风量、药剂更换和异常工况台账。"
    "综合治理年度实施计划列出槽车进卸料密闭循环、污水站反吊膜密封、危废库密闭与引风改造等事项，"
    "但资料目录未见对应施工合同、完成照片、验收单和整改后复测报告，治理闭环仍待核实。"
    "专家意见、部分批复和排污许可证为扫描材料，当前只能作为待复核证据，不能据此形成法定合规结论。"
)


SERVICE_CASES = (
    (
        "vocs-closure",
        "VOCs 治理措施闭环核验",
        "onsite",
        "in_progress",
        "2026-09-02T01:00:00+00:00",
        "2026-09-04T09:00:00+00:00",
        "核对密闭收集、污水站与危废库改造的合同、照片、验收和复测材料。",
    ),
    (
        "permit-ocr",
        "证照批复 OCR 与人工复核",
        "review",
        "planned",
        "2026-09-05T01:00:00+00:00",
        "2026-09-08T09:00:00+00:00",
        "复核批复文号、许可编号、有效期、项目边界与盖章页。",
    ),
    (
        "ledger-gap",
        "治理设施运行台账补齐",
        "remote",
        "planned",
        "2026-09-08T01:00:00+00:00",
        "2026-09-12T09:00:00+00:00",
        "补充吸收塔、活性炭、风量、药剂更换与异常工况连续记录。",
    ),
    (
        "monitoring-review",
        "2020 年监测与 LDAR 资料复核",
        "audit",
        "completed",
        "2026-08-26T01:00:00+00:00",
        "2026-08-28T09:00:00+00:00",
        "完成十二份月度检测报告与一份 LDAR 报告的目录级复核。",
    ),
)


PROFILE_INDUSTRY_NOTE = (
    "化学药品原料药制造（脱敏演示档案）；资料复核重点为 VOCs、废水、危废、"
    "排污许可和环境监测。本字段不含联系人、电话、许可编号或签章。"
)
PROFILE_REGION_NOTE = "安徽省池州市东至县（仅保留市县级定位，详细厂址已省略）"


FINDING_FIXTURES = (
    {
        "slug": "vocs-acceptance-gap",
        "case_slug": "vocs-closure",
        "title": "VOCs 改造缺少验收与复测证据",
        "description": (
            "脱敏资料目录列有密闭收集、污水站反吊膜和危废库引风改造计划，"
            "但未见施工合同、完成照片、验收单与整改后复测报告。此项仅为资料缺口，"
            "不构成法定合规结论。"
        ),
        "severity": "high",
        "due_at": "2026-09-06T09:00:00+00:00",
        "status": "open",
        "created_at": "2026-08-31T01:20:00+00:00",
    },
    {
        "slug": "vocs-abnormal-ledger",
        "case_slug": "vocs-closure",
        "title": "异常工况记录尚未形成连续台账",
        "description": (
            "现有摘要未呈现吸收塔、活性炭、风量、药剂更换和异常工况的连续记录，"
            "需先建立统一模板并明确缺失月份。"
        ),
        "severity": "medium",
        "due_at": "2026-09-08T09:00:00+00:00",
        "status": "rectifying",
        "created_at": "2026-08-31T01:35:00+00:00",
        "rectification_started_at": "2026-08-31T02:10:00+00:00",
    },
    {
        "slug": "permit-field-review",
        "case_slug": "permit-ocr",
        "title": "证照关键字段需要人工复核",
        "description": (
            "批复与许可材料多为扫描件或图片，编号、有效期、适用范围和盖章页尚未形成"
            "可追溯字段表。"
        ),
        "severity": "medium",
        "due_at": "2026-09-09T09:00:00+00:00",
        "status": "submitted",
        "created_at": "2026-08-30T02:00:00+00:00",
        "rectification_started_at": "2026-08-30T03:00:00+00:00",
        "corrective_action": (
            "已建立脱敏字段复核清单，列出文号、有效期、项目边界和盖章页四类待核项；"
            "等待人工对照原件后确认。"
        ),
        "corrective_submitted_at": "2026-08-31T03:00:00+00:00",
    },
    {
        "slug": "facility-ledger-template",
        "case_slug": "ledger-gap",
        "title": "治理设施运行记录口径不统一",
        "description": (
            "月度资料未使用统一的设施运行与耗材更换字段，暂时无法形成跨月连续性判断。"
        ),
        "severity": "high",
        "due_at": "2026-09-12T09:00:00+00:00",
        "status": "reviewing",
        "created_at": "2026-08-29T01:00:00+00:00",
        "rectification_started_at": "2026-08-29T02:00:00+00:00",
        "corrective_action": (
            "已提交统一台账模板样例，包含设施启停、风量、药剂、活性炭更换与异常工况字段；"
            "当前仅为模板，尚未补齐历史记录。"
        ),
        "corrective_submitted_at": "2026-08-30T02:00:00+00:00",
        "review_started_at": "2026-08-31T02:00:00+00:00",
    },
    {
        "slug": "monitoring-catalog-check",
        "case_slug": "monitoring-review",
        "title": "监测与 LDAR 目录完整性复核",
        "description": (
            "核对十二份月度检测报告与一份 LDAR 报告是否全部登记，并记录后续年度资料缺口。"
        ),
        "severity": "low",
        "due_at": "2026-08-28T09:00:00+00:00",
        "status": "passed",
        "created_at": "2026-08-26T01:30:00+00:00",
        "rectification_started_at": "2026-08-26T02:00:00+00:00",
        "corrective_action": (
            "已完成目录级核对：十二份月度检测报告与一份 2020 年 10 月 LDAR 报告均已登记；"
            "后续年度 LDAR 与泄漏修复闭环继续列为缺口。"
        ),
        "corrective_submitted_at": "2026-08-27T03:00:00+00:00",
        "review_started_at": "2026-08-27T05:00:00+00:00",
        "review_decision": "passed",
        "review_comment": "脱敏目录数量与摘要一致，目录级复核通过；不代表原始报告内容合规。",
        "reviewed_at": "2026-08-28T03:00:00+00:00",
    },
)


POLICY_FIXTURES = (
    {
        "slug": "vocs-closure-checklist",
        "title": "VOCs 治理闭环资料核验清单（脱敏演示）",
        "domain": "environment",
        "summary": (
            "内部测试清单：依次核对治理方案、施工或实施证明、完成照片、验收记录与复测材料。"
            "仅用于演示内部资料复核流程，不声明法规适用，不构成法律或合规意见。"
        ),
        "created_at": "2026-08-30T01:00:00+00:00",
    },
    {
        "slug": "permit-monitoring-checklist",
        "title": "排污许可与监测资料复核清单（脱敏演示）",
        "domain": "environment",
        "summary": (
            "内部测试清单：记录证照文号、有效期、适用边界、监测月份、因子与人工复核状态。"
            "候选字段均需对照原件确认，不替代主管部门意见。"
        ),
        "created_at": "2026-08-30T01:10:00+00:00",
    },
    {
        "slug": "ldar-follow-up-checklist",
        "title": "LDAR 周期复核与修复闭环指引（脱敏演示）",
        "domain": "environment",
        "summary": (
            "内部测试指引：登记检测周期、密封点数量、超阈值点、修复与复测记录。"
            "历史摘要只证明目录中存在一份报告，不能推导持续合规。"
        ),
        "created_at": "2026-08-30T01:20:00+00:00",
    },
)


def _as_iso(value: Any) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-dir", required=True, type=Path)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--ack-sanitized-summary-only", action="store_true")
    return parser.parse_args()


def _load_state(control_dir: Path) -> tuple[dict[str, object], dict[str, Path]]:
    if not control_dir.is_absolute() or not control_dir.name.startswith("anhuan-ar-pgint-"):
        raise RuntimeError("DONGSHENG_DEMO_CONTROL_DIR_INVALID")
    state_path = control_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="ascii"))
    if not isinstance(state, dict) or state.get("control_dir") != str(control_dir):
        raise RuntimeError("DONGSHENG_DEMO_STATE_INVALID")
    paths = uat._control_paths(state)
    return state, paths


def _require_origin(origin: str, state: dict[str, object]) -> str:
    parsed = urllib.parse.urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != int(state["web_port"])
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("DONGSHENG_DEMO_ORIGIN_INVALID")
    return origin.rstrip("/")


def _set_context(connection: Any, sub: str) -> None:
    connection.execute(
        "SELECT set_config('f1.enterprise_id',%s,true),set_config('f1.sub',%s,true)",
        (str(local_seed.ENTERPRISE_A), sub),
    )


def _demo_id(kind: str, slug: str) -> uuid.UUID:
    return uuid.uuid5(DEMO_NAMESPACE, f"{kind}:{slug}")


def _upsert_timeline_event(
    connection: Any,
    *,
    slug: str,
    case_id: uuid.UUID,
    event_type: str,
    subject_type: str,
    subject_id: uuid.UUID,
    status: str | None,
    actor_id: uuid.UUID,
    occurred_at: str,
) -> None:
    connection.execute(
        "INSERT INTO f1.business_timeline ("
        "id,enterprise_id,service_case_id,event_type,subject_type,subject_id,"
        "status,actor_user_id,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO UPDATE SET "
        "service_case_id=EXCLUDED.service_case_id,event_type=EXCLUDED.event_type,"
        "subject_type=EXCLUDED.subject_type,subject_id=EXCLUDED.subject_id,"
        "status=EXCLUDED.status,actor_user_id=EXCLUDED.actor_user_id,"
        "occurred_at=EXCLUDED.occurred_at",
        (
            _demo_id("timeline", slug),
            local_seed.ENTERPRISE_A,
            case_id,
            event_type,
            subject_type,
            subject_id,
            status,
            actor_id,
            occurred_at,
        ),
    )


def _seed_service_timelines(
    connection: Any,
    *,
    actor_id: uuid.UUID,
    employee_id: uuid.UUID,
    case_ids_by_slug: dict[str, uuid.UUID],
) -> None:
    created_at_by_slug = {
        "vocs-closure": "2026-08-29T01:00:00+00:00",
        "permit-ocr": "2026-08-29T01:10:00+00:00",
        "ledger-gap": "2026-08-29T01:20:00+00:00",
        "monitoring-review": "2026-08-25T01:00:00+00:00",
    }
    for slug, _title, _kind, status, _starts, _ends, _description in SERVICE_CASES:
        case_id = case_ids_by_slug[slug]
        assignment_id = _demo_id("assignment", str(case_id))
        created_at = created_at_by_slug[slug]
        _upsert_timeline_event(
            connection,
            slug=f"{slug}:created",
            case_id=case_id,
            event_type="service_case.created",
            subject_type="service_case",
            subject_id=case_id,
            status="planned",
            actor_id=actor_id,
            occurred_at=created_at,
        )
        _upsert_timeline_event(
            connection,
            slug=f"{slug}:assigned",
            case_id=case_id,
            event_type="service_assignment.created",
            subject_type="service_assignment",
            subject_id=assignment_id,
            status="pending",
            actor_id=actor_id,
            occurred_at=created_at_by_slug[slug].replace(":00+00:00", ":10+00:00"),
        )
        _upsert_timeline_event(
            connection,
            slug=f"{slug}:accepted",
            case_id=case_id,
            event_type="service_assignment.accept",
            subject_type="service_assignment",
            subject_id=assignment_id,
            status="accepted",
            actor_id=employee_id,
            occurred_at=created_at_by_slug[slug].replace(":00+00:00", ":20+00:00"),
        )
        if status == "in_progress":
            _upsert_timeline_event(
                connection,
                slug=f"{slug}:started",
                case_id=case_id,
                event_type="service_case.started",
                subject_type="service_case",
                subject_id=case_id,
                status="in_progress",
                actor_id=employee_id,
                occurred_at="2026-08-31T01:00:00+00:00",
            )
        elif status == "completed":
            _upsert_timeline_event(
                connection,
                slug=f"{slug}:completed",
                case_id=case_id,
                event_type="service_case.auto_completed",
                subject_type="service_case",
                subject_id=case_id,
                status="completed",
                actor_id=actor_id,
                occurred_at="2026-08-28T09:00:00+00:00",
            )


def _seed_findings(
    connection: Any,
    *,
    actor_id: uuid.UUID,
    employee_id: uuid.UUID,
    reviewer_id: uuid.UUID | None,
    case_ids_by_slug: dict[str, uuid.UUID],
) -> None:
    for item in FINDING_FIXTURES:
        slug = str(item["slug"])
        case_id = case_ids_by_slug[str(item["case_slug"])]
        finding_id = _demo_id("finding", slug)
        updated_at = str(
            item.get("reviewed_at")
            or item.get("review_started_at")
            or item.get("corrective_submitted_at")
            or item.get("rectification_started_at")
            or item["created_at"]
        )
        persisted_status = (
            "reviewing"
            if item.get("review_decision") and reviewer_id is None
            else str(item["status"])
        )
        connection.execute(
            "INSERT INTO f1.finding ("
            "id,enterprise_id,service_case_id,site_visit_id,title,description,"
            "severity,responsible_user_id,due_at,status,created_by_user_id,"
            "created_at,updated_at) VALUES (%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "service_case_id=EXCLUDED.service_case_id,site_visit_id=NULL,"
            "title=EXCLUDED.title,description=EXCLUDED.description,"
            "severity=EXCLUDED.severity,responsible_user_id=EXCLUDED.responsible_user_id,"
            "due_at=EXCLUDED.due_at,status=EXCLUDED.status,"
            "created_by_user_id=EXCLUDED.created_by_user_id,"
            "created_at=EXCLUDED.created_at,updated_at=EXCLUDED.updated_at",
            (
                finding_id,
                local_seed.ENTERPRISE_A,
                case_id,
                item["title"],
                item["description"],
                item["severity"],
                employee_id,
                item["due_at"],
                persisted_status,
                actor_id,
                item["created_at"],
                updated_at,
            ),
        )
        _upsert_timeline_event(
            connection,
            slug=f"finding:{slug}:created",
            case_id=case_id,
            event_type="finding.created",
            subject_type="finding",
            subject_id=finding_id,
            status="open",
            actor_id=actor_id,
            occurred_at=str(item["created_at"]),
        )

        rectification_started_at = item.get("rectification_started_at")
        if rectification_started_at:
            _upsert_timeline_event(
                connection,
                slug=f"finding:{slug}:rectifying",
                case_id=case_id,
                event_type="finding.start_rectification",
                subject_type="finding",
                subject_id=finding_id,
                status="rectifying",
                actor_id=employee_id,
                occurred_at=str(rectification_started_at),
            )

        corrective_description = item.get("corrective_action")
        if corrective_description:
            corrective_id = _demo_id("corrective-action", slug)
            corrective_submitted_at = str(item["corrective_submitted_at"])
            connection.execute(
                "INSERT INTO f1.corrective_action ("
                "id,enterprise_id,finding_id,revision,description,"
                "submitted_by_user_id,submitted_at) VALUES (%s,%s,%s,1,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET finding_id=EXCLUDED.finding_id,"
                "revision=EXCLUDED.revision,description=EXCLUDED.description,"
                "submitted_by_user_id=EXCLUDED.submitted_by_user_id,"
                "submitted_at=EXCLUDED.submitted_at",
                (
                    corrective_id,
                    local_seed.ENTERPRISE_A,
                    finding_id,
                    corrective_description,
                    employee_id,
                    corrective_submitted_at,
                ),
            )
            _upsert_timeline_event(
                connection,
                slug=f"finding:{slug}:correction",
                case_id=case_id,
                event_type="corrective_action.submitted",
                subject_type="corrective_action",
                subject_id=corrective_id,
                status="submitted",
                actor_id=employee_id,
                occurred_at=corrective_submitted_at,
            )

        review_started_at = item.get("review_started_at")
        if review_started_at:
            _upsert_timeline_event(
                connection,
                slug=f"finding:{slug}:reviewing",
                case_id=case_id,
                event_type="finding.start_review",
                subject_type="finding",
                subject_id=finding_id,
                status="reviewing",
                actor_id=actor_id,
                occurred_at=str(review_started_at),
            )

        review_decision = item.get("review_decision")
        if review_decision and reviewer_id is not None:
            review_id = _demo_id("finding-review", slug)
            reviewed_at = str(item["reviewed_at"])
            connection.execute(
                "INSERT INTO f1.finding_review ("
                "id,enterprise_id,finding_id,decision,comment,reviewer_user_id,created_at"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET finding_id=EXCLUDED.finding_id,"
                "decision=EXCLUDED.decision,comment=EXCLUDED.comment,"
                "reviewer_user_id=EXCLUDED.reviewer_user_id,created_at=EXCLUDED.created_at",
                (
                    review_id,
                    local_seed.ENTERPRISE_A,
                    finding_id,
                    review_decision,
                    item["review_comment"],
                    reviewer_id,
                    reviewed_at,
                ),
            )
            _upsert_timeline_event(
                connection,
                slug=f"finding:{slug}:review-{review_decision}",
                case_id=case_id,
                event_type=(
                    "finding.review_pass"
                    if review_decision == "passed"
                    else "finding.review_reject"
                ),
                subject_type="finding_review",
                subject_id=review_id,
                status=str(review_decision),
                actor_id=reviewer_id,
                occurred_at=reviewed_at,
            )


def _seed_policy_sources(connection: Any, *, actor_id: uuid.UUID) -> None:
    for item in POLICY_FIXTURES:
        slug = str(item["slug"])
        source_id = _demo_id("policy-source", slug)
        version_id = _demo_id("policy-version", slug)
        reference = f"LOCAL_FIXTURE:dongsheng:{slug}"
        connection.execute(
            "INSERT INTO f1.policy_source ("
            "id,enterprise_id,title,publisher,source_type,jurisdiction,"
            "source_reference,status,created_by_user_id,created_at,updated_at"
            ") VALUES (%s,%s,%s,'A-Eco 测试服务组','internal','内部测试',%s,"
            "'active',%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
            "title=EXCLUDED.title,publisher=EXCLUDED.publisher,"
            "source_type=EXCLUDED.source_type,jurisdiction=EXCLUDED.jurisdiction,"
            "source_reference=EXCLUDED.source_reference,status=EXCLUDED.status,"
            "created_by_user_id=EXCLUDED.created_by_user_id,"
            "created_at=EXCLUDED.created_at,updated_at=EXCLUDED.updated_at",
            (
                source_id,
                local_seed.ENTERPRISE_A,
                item["title"],
                reference,
                actor_id,
                item["created_at"],
                item["created_at"],
            ),
        )
        connection.execute(
            "INSERT INTO f1.policy_version ("
            "id,enterprise_id,source_id,version_number,title,domain,effect_status,"
            "issued_on,effective_from,effective_to,summary,document_version_id,"
            "document_sha256,workflow_status,submitted_by_user_id,submitted_at,"
            "approved_by_user_id,approved_at,published_by_user_id,published_at,"
            "created_by_user_id,created_at,updated_at) VALUES ("
            "%s,%s,%s,1,%s,%s,'unknown',NULL,NULL,NULL,%s,NULL,NULL,'draft',"
            "NULL,NULL,NULL,NULL,NULL,NULL,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET source_id=EXCLUDED.source_id,"
            "version_number=EXCLUDED.version_number,title=EXCLUDED.title,"
            "domain=EXCLUDED.domain,effect_status=EXCLUDED.effect_status,"
            "issued_on=NULL,effective_from=NULL,effective_to=NULL,summary=EXCLUDED.summary,"
            "document_version_id=NULL,document_sha256=NULL,workflow_status='draft',"
            "submitted_by_user_id=NULL,submitted_at=NULL,approved_by_user_id=NULL,"
            "approved_at=NULL,published_by_user_id=NULL,published_at=NULL,"
            "created_by_user_id=EXCLUDED.created_by_user_id,"
            "created_at=EXCLUDED.created_at,updated_at=EXCLUDED.updated_at",
            (
                version_id,
                local_seed.ENTERPRISE_A,
                source_id,
                item["title"],
                item["domain"],
                item["summary"],
                actor_id,
                item["created_at"],
                item["created_at"],
            ),
        )


def _seed_database(
    state: dict[str, object], paths: dict[str, Path]
) -> tuple[Any, dict[str, int]]:
    fixture = uat._load_fixture()
    uat._rewrite_host_bootstrap_dsn(state, paths)
    with __import__("psycopg").connect(
        _bootstrap_dsn(), autocommit=False, connect_timeout=5
    ) as connection:
        head = connection.execute(
            "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM f1.alembic_version"
        ).fetchone()
        if head is None or head[0] != "f1_0023":
            raise RuntimeError("DONGSHENG_DEMO_HEAD_MISMATCH")
        _set_context(connection, fixture.TENANT_A_SUB)
        actor_id = local_seed._stable_id("profile", fixture.TENANT_A_SUB)
        employee_id = local_seed._stable_id("profile", fixture.EMPLOYEE_SUB)
        reviewer_row = connection.execute(
            "SELECT user_id FROM f1.enterprise_user "
            "WHERE enterprise_id=%s AND role='auditor' ORDER BY user_id LIMIT 1",
            (local_seed.ENTERPRISE_A,),
        ).fetchone()
        reviewer_id = reviewer_row[0] if reviewer_row is not None else None

        # Keep the fixture's one-client-source invariant: replace only the
        # body/title of its existing synthetic summary, never add raw units.
        fixture._DISPLAY_TITLES[fixture.CLIENT_MATERIAL_LABEL] = CLIENT_MATERIAL_TITLE
        fixture._MATERIAL_BODIES[fixture.CLIENT_MATERIAL_LABEL] = CLIENT_MATERIAL_BODY
        connection.execute(
            "ALTER TABLE f1.material_rag_unit DISABLE TRIGGER material_rag_unit_guard"
        )
        try:
            fixture._insert_synthetic_unit(
                connection,
                label=fixture.CLIENT_MATERIAL_LABEL,
                scope_id=fixture.CLIENT_SCOPE_ID,
                actor_id=actor_id,
            )
        finally:
            connection.execute(
                "ALTER TABLE f1.material_rag_unit ENABLE TRIGGER material_rag_unit_guard"
            )
        record_id = fixture._stable_material_id("record", fixture.CLIENT_MATERIAL_LABEL)
        version_id = fixture._stable_material_id("version", fixture.CLIENT_MATERIAL_LABEL)
        document_id = fixture._stable_material_id("document", fixture.CLIENT_MATERIAL_LABEL)
        connection.execute(
            "UPDATE f1.document_record SET title=%s,updated_at=statement_timestamp() "
            "WHERE enterprise_id=%s AND id=%s",
            (CLIENT_MATERIAL_TITLE, local_seed.ENTERPRISE_A, record_id),
        )
        connection.execute(
            "UPDATE f1.document_version SET display_filename=%s "
            "WHERE enterprise_id=%s AND id=%s",
            ("dongsheng-sanitized-demo-summary.pdf", local_seed.ENTERPRISE_A, version_id),
        )
        connection.execute(
            "UPDATE f1.document SET filename=%s WHERE enterprise_id=%s AND id=%s",
            ("dongsheng-sanitized-demo-summary.pdf", local_seed.ENTERPRISE_A, document_id),
        )

        # These presentation fields intentionally contain no contact details,
        # document bytes, permit numbers, signatures, or external object paths.
        connection.execute(
            "UPDATE f1.crm_account SET display_name=%s,stage='active',"
            "industry_note=%s,region_note=%s,next_follow_up_at=%s,"
            "updated_at=statement_timestamp() "
            "WHERE enterprise_id=%s AND id=%s",
            (
                CLIENT_DISPLAY_NAME,
                PROFILE_INDUSTRY_NOTE,
                PROFILE_REGION_NOTE,
                "2026-09-02T01:00:00+00:00",
                local_seed.ENTERPRISE_A,
                fixture.CRM_ACCOUNT_ID,
            ),
        )

        # Reuse the original fixture row so the portal has no leftover
        # engineering-only service title.
        first = SERVICE_CASES[0]
        connection.execute(
            "UPDATE f1.service_case SET title=%s,description=%s,service_type=%s,"
            "planned_start_at=%s,planned_end_at=%s "
            "WHERE enterprise_id=%s AND id=%s",
            (
                first[1], first[6], first[2], first[4], first[5],
                local_seed.ENTERPRISE_A, fixture.SYNTHETIC_SERVICE_CASE_ID,
            ),
        )
        connection.execute(
            "UPDATE f1.service_case SET status=%s WHERE enterprise_id=%s AND id=%s",
            (first[3], local_seed.ENTERPRISE_A, fixture.SYNTHETIC_SERVICE_CASE_ID),
        )

        case_ids_by_slug: dict[str, uuid.UUID] = {
            str(first[0]): fixture.SYNTHETIC_SERVICE_CASE_ID
        }
        for slug, title, service_type, status, starts, ends, description in SERVICE_CASES[1:]:
            case_id = _demo_id("service-case", slug)
            case_ids_by_slug[slug] = case_id
            connection.execute(
                "INSERT INTO f1.service_case ("
                "id,enterprise_id,plant_id,client_account_id,title,description,"
                "service_type,status,planned_start_at,planned_end_at,created_by_user_id"
                ") VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET plant_id=NULL,"
                "client_account_id=EXCLUDED.client_account_id,title=EXCLUDED.title,"
                "description=EXCLUDED.description,service_type=EXCLUDED.service_type,"
                "status=EXCLUDED.status,planned_start_at=EXCLUDED.planned_start_at,"
                "planned_end_at=EXCLUDED.planned_end_at,"
                "created_by_user_id=EXCLUDED.created_by_user_id,"
                "updated_at=statement_timestamp()",
                (
                    case_id, local_seed.ENTERPRISE_A, fixture.CRM_ACCOUNT_ID,
                    title, description, service_type, status, starts, ends, actor_id,
                ),
            )
        for case_id in case_ids_by_slug.values():
            assignment_id = _demo_id("assignment", str(case_id))
            connection.execute(
                "INSERT INTO f1.service_assignment ("
                "id,enterprise_id,service_case_id,assignee_user_id,assigned_by_user_id,"
                "capacity,status,responded_at) "
                "VALUES (%s,%s,%s,%s,%s,'employee','accepted',statement_timestamp()) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    assignment_id, local_seed.ENTERPRISE_A, case_id,
                    employee_id, actor_id,
                ),
            )
            assignment = connection.execute(
                "SELECT enterprise_id,service_case_id,assignee_user_id,"
                "assigned_by_user_id,capacity,status,responded_at,revoked_at "
                "FROM f1.service_assignment WHERE id=%s",
                (assignment_id,),
            ).fetchone()
            if (
                assignment is None
                or tuple(assignment[:6])
                != (
                    local_seed.ENTERPRISE_A,
                    case_id,
                    employee_id,
                    actor_id,
                    "employee",
                    "accepted",
                )
                or assignment[6] is None
                or assignment[7] is not None
            ):
                raise RuntimeError("DONGSHENG_DEMO_ASSIGNMENT_CONFLICT")

        # A client plant cannot be represented safely here: f1.plant is owned
        # by the provider tenant, whereas Dongsheng is the linked CRM client.
        # Keep the structured summary on crm_account rather than creating a
        # falsely owned plant row.
        _seed_service_timelines(
            connection,
            actor_id=actor_id,
            employee_id=employee_id,
            case_ids_by_slug=case_ids_by_slug,
        )
        _seed_findings(
            connection,
            actor_id=actor_id,
            employee_id=employee_id,
            reviewer_id=reviewer_id,
            case_ids_by_slug=case_ids_by_slug,
        )
        _seed_policy_sources(connection, actor_id=actor_id)
        connection.commit()
    timeline_events = len(SERVICE_CASES) * 3 + sum(
        1 for item in SERVICE_CASES if item[3] in ("in_progress", "completed")
    )
    timeline_events += sum(
        1
        + int(bool(item.get("rectification_started_at")))
        + int(bool(item.get("corrective_action")))
        + int(bool(item.get("review_started_at")))
        + int(bool(item.get("review_decision") and reviewer_id is not None))
        for item in FINDING_FIXTURES
    )
    seeded = {
        "crm_profiles": 1,
        "findings": len(FINDING_FIXTURES),
        "corrective_actions": sum(
            1 for item in FINDING_FIXTURES if item.get("corrective_action")
        ),
        "finding_reviews": sum(
            1
            for item in FINDING_FIXTURES
            if item.get("review_decision") and reviewer_id is not None
        ),
        "timeline_events": timeline_events,
        "policy_sources": len(POLICY_FIXTURES),
        "client_plants": 0,
    }
    return fixture, seeded


def _token(origin: str, paths: dict[str, Path], username: str, secret: str) -> str:
    password = (paths["secrets"] / secret).read_text(encoding="ascii").strip()
    data = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "anhuan-web",
            "username": username,
            "password": password,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        f"{origin}/realms/anhuan/protocol/openid-connect/token",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("access_token")
    if not isinstance(token, str) or len(token) < 20:
        raise RuntimeError("DONGSHENG_DEMO_TOKEN_INVALID")
    return token


def _request(
    origin: str,
    path: str,
    token: str,
    enterprise_id: uuid.UUID,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Enterprise-Id": str(enterprise_id),
    }
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{origin}{path}", data=encoded, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", "replace")
        raise RuntimeError(f"DONGSHENG_DEMO_HTTP_{error.code}:{detail}") from None
    if not isinstance(payload, dict):
        raise RuntimeError("DONGSHENG_DEMO_HTTP_RESPONSE_INVALID")
    return payload


def _publish_report(
    origin: str, paths: dict[str, Path], fixture: Any
) -> tuple[uuid.UUID, uuid.UUID]:
    token = _token(origin, paths, "tenant-a", "oidc_tenant_a")
    report = _request(
        origin,
        f"/api/v1/analysis-reports/clients/{fixture.CRM_ACCOUNT_ID}/reports",
        token,
        local_seed.ENTERPRISE_A,
        method="POST",
        body={"request_id": str(CREATE_REQUEST_ID)},
    )
    report_id = uuid.UUID(str(report["report_id"]))
    status = str(report.get("current_status") or "empty")
    current_version = report.get("current_version_id")
    if status == "published" and current_version:
        return report_id, uuid.UUID(str(current_version))
    if status != "empty":
        raise RuntimeError(f"DONGSHENG_DEMO_REPORT_STATE_UNEXPECTED:{status}")

    generated = _request(
        origin,
        f"/api/v1/analysis-reports/clients/{fixture.CRM_ACCOUNT_ID}/reports/"
        f"{report_id}/generations",
        token,
        local_seed.ENTERPRISE_A,
        method="POST",
        body={"request_id": str(GENERATION_REQUEST_ID)},
    )
    job_id = uuid.UUID(str(generated["job_id"]))
    version_id = uuid.UUID(str(generated["version_id"]))
    deadline = time.monotonic() + 55
    while True:
        job = _request(
            origin,
            f"/api/v1/analysis-reports/jobs/{job_id}",
            token,
            local_seed.ENTERPRISE_A,
        )
        job_status = str(job.get("status"))
        if job_status == "draft":
            break
        if job_status == "failed":
            raise RuntimeError(f"DONGSHENG_DEMO_GENERATION_FAILED:{job.get('error_reason')}")
        if time.monotonic() >= deadline:
            raise RuntimeError("DONGSHENG_DEMO_GENERATION_TIMEOUT")
        time.sleep(0.5)
    _request(
        origin,
        f"/api/v1/analysis-reports/versions/{version_id}/submit",
        token,
        local_seed.ENTERPRISE_A,
        method="POST",
        body={},
    )
    _request(
        origin,
        f"/api/v1/analysis-reports/versions/{version_id}/approve",
        token,
        local_seed.ENTERPRISE_A,
        method="POST",
        body={
            "checklist": {
                "citation_traceable": True,
                "risks_complete": True,
                "usage_boundary": True,
            },
            "comment": "脱敏演示摘要已核对；不含原件与个人信息，仅用于测试环境展示。",
        },
    )
    _request(
        origin,
        f"/api/v1/analysis-reports/versions/{version_id}/publish",
        token,
        local_seed.ENTERPRISE_A,
        method="POST",
    )
    return report_id, version_id


def _seed_health(
    state: dict[str, object],
    paths: dict[str, Path],
    fixture: Any,
    report_id: uuid.UUID,
    version_id: uuid.UUID,
) -> None:
    uat._rewrite_host_bootstrap_dsn(state, paths)
    with __import__("psycopg").connect(
        _bootstrap_dsn(), autocommit=False, connect_timeout=5
    ) as connection:
        row = connection.execute(
            "SELECT version.version_number,version.published_at "
            "FROM f1.analysis_report_version AS version "
            "WHERE version.enterprise_id=%s AND version.report_id=%s "
            "AND version.id=%s AND version.status='published' AND version.artifact_ready IS TRUE",
            (local_seed.ENTERPRISE_A, report_id, version_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("DONGSHENG_DEMO_PUBLISHED_VERSION_MISSING")
        published_at = row[1]
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        snapshot = {
            "report_id": str(report_id),
            "version_id": str(version_id),
            "version_number": int(row[0]),
            "report_title": "企业安环资料分析报告",
            "score": 60,
            "max_score": 100,
            "status_label": "需重点改善",
            "assessed_on": _as_iso(published_at),
            "basis_label": "基于东升药业脱敏演示摘要与已发布测试报告",
            "evidence_mode": "evidence_local",
            "dimensions": [
                {"key": "material-completeness", "label": "资料完整性", "score": 12, "max_score": 15, "summary": "24 份资料目录已整理，扫描件仍需复核", "tone": "positive"},
                {"key": "permits", "label": "证照与批复", "score": 14, "max_score": 20, "summary": "证号、有效期与适用范围待 OCR 和人工核对", "tone": "attention"},
                {"key": "monitoring", "label": "监测与台账", "score": 13, "max_score": 20, "summary": "月度检测与 LDAR 已覆盖，连续运行台账不足", "tone": "attention"},
                {"key": "remediation", "label": "整改闭环", "score": 8, "max_score": 25, "summary": "施工、验收、照片和复测证据缺少", "tone": "priority"},
                {"key": "expiry", "label": "风险与到期", "score": 6, "max_score": 10, "summary": "后续 LDAR 与整改复核时间线待建立", "tone": "attention"},
                {"key": "evidence", "label": "证据可信度", "score": 7, "max_score": 10, "summary": "部分扫描材料尚未形成可追溯复核链", "tone": "attention"},
            ],
            "priorities": [
                {"title": "补齐 VOCs 治理闭环材料", "level": "high"},
                {"title": "更新治理设施连续运行台账", "level": "medium"},
                {"title": "复核证照批复与扫描件", "level": "medium"},
            ],
            "boundary": "LOCAL_FIXTURE 脱敏测试评分，仅用于资料管理与改善优先级演示，不替代法定合规评价、执法结论或生产放行。",
        }
        # Keep the exact closed health contract used by the HTTP service.
        if (
            sum(int(item["score"]) for item in snapshot["dimensions"]) != 60
            or [item["key"] for item in snapshot["dimensions"]]
            != [
                "material-completeness",
                "permits",
                "monitoring",
                "remediation",
                "expiry",
                "evidence",
            ]
        ):
            raise RuntimeError("DONGSHENG_DEMO_HEALTH_INVALID")
        validated = snapshot
        digest = _payload_sha256(validated)
        existing = connection.execute(
            "SELECT payload_sha256 FROM f1.analysis_report_health_snapshot "
            "WHERE enterprise_id=%s AND version_id=%s",
            (local_seed.ENTERPRISE_A, version_id),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO f1.analysis_report_health_snapshot ("
                "id,enterprise_id,report_id,version_id,client_account_id,payload,"
                "payload_sha256,score,max_score) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,60,100)",
                (
                    uuid.uuid5(DEMO_NAMESPACE, f"health:{version_id}"),
                    local_seed.ENTERPRISE_A,
                    report_id,
                    version_id,
                    fixture.CRM_ACCOUNT_ID,
                    json.dumps(validated, ensure_ascii=False, separators=(",", ":")),
                    digest,
                ),
            )
        elif str(existing[0]) != digest:
            raise RuntimeError("DONGSHENG_DEMO_HEALTH_CONFLICT")
        connection.commit()


def _verify_portal(origin: str, paths: dict[str, Path]) -> dict[str, int]:
    token = _token(origin, paths, "invitee", "oidc_invitee")
    reports = _request(
        origin,
        "/api/v1/analysis-reports/published",
        token,
        local_seed.ENTERPRISE_B,
    )
    health_payload = _request(
        origin,
        "/api/v1/analysis-reports/health/latest",
        token,
        local_seed.ENTERPRISE_B,
    )
    services = _request(
        origin,
        "/api/v1/service-cases/portal",
        token,
        local_seed.ENTERPRISE_B,
    )
    report_items = reports.get("reports")
    service_items = services.get("items")
    snapshot = health_payload.get("snapshot")
    if (
        not isinstance(report_items, list)
        or len(report_items) < 1
        or not isinstance(service_items, list)
        or len(service_items) < 4
        or not isinstance(snapshot, dict)
        or snapshot.get("score") != 60
        or snapshot.get("evidence_mode") != "evidence_local"
    ):
        raise RuntimeError("DONGSHENG_DEMO_PORTAL_VERIFY_FAILED")
    return {
        "published_reports": len(report_items),
        "service_cases": len(service_items),
        "health_score": int(snapshot["score"]),
    }


def main() -> int:
    args = _parse_args()
    if not args.ack_sanitized_summary_only:
        raise RuntimeError("DONGSHENG_DEMO_SANITIZED_ACK_REQUIRED")
    state, paths = _load_state(args.control_dir)
    origin = _require_origin(args.origin, state)
    original = dict(os.environ)
    try:
        os.environ.update(uat._pg_env(state, paths))
        fixture, seeded = _seed_database(state, paths)
        report_id, version_id = _publish_report(origin, paths, fixture)
        _seed_health(state, paths, fixture, report_id, version_id)
        summary = _verify_portal(origin, paths)
        summary.update(seeded)
    finally:
        os.environ.clear()
        os.environ.update(original)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    print("DONGSHENG_SANITIZED_DEMO_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
