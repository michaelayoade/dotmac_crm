from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dispatch import (
    AvailabilityBlock,
    DispatchQueueStatus,
    DispatchRule,
    Shift,
    Skill,
    TechnicianProfile,
    TechnicianSkill,
    WorkOrderAssignmentQueue,
)
from app.models.person import Person
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.schemas.dispatch import (
    AvailabilityBlockCreate,
    AvailabilityBlockUpdate,
    DispatchRuleCreate,
    DispatchRuleUpdate,
    ShiftCreate,
    ShiftUpdate,
    SkillCreate,
    SkillUpdate,
    TechnicianProfileCreate,
    TechnicianProfileUpdate,
    TechnicianSkillCreate,
    TechnicianSkillUpdate,
    WorkOrderAssignmentQueueCreate,
    WorkOrderAssignmentQueueUpdate,
)
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin


def _ensure_person(db: Session, person_id: str):
    if not db.get(Person, coerce_uuid(person_id)):
        raise HTTPException(status_code=404, detail="Person not found")


def _ensure_skill(db: Session, skill_id: str):
    if not db.get(Skill, coerce_uuid(skill_id)):
        raise HTTPException(status_code=404, detail="Skill not found")


def _ensure_technician(db: Session, technician_id: str):
    if not db.get(TechnicianProfile, coerce_uuid(technician_id)):
        raise HTTPException(status_code=404, detail="Technician not found")


def _ensure_work_order(db: Session, work_order_id: str) -> WorkOrder:
    work_order = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not work_order:
        raise HTTPException(status_code=404, detail="Work order not found")
    return work_order


class Skills(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: SkillCreate):
        skill = Skill(**payload.model_dump())
        db.add(skill)
        db.commit()
        db.refresh(skill)
        return skill

    @staticmethod
    def get(db: Session, skill_id: str):
        skill = db.get(Skill, skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return skill

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Skill)
        if is_active is None:
            query = query.filter(Skill.is_active.is_(True))
        else:
            query = query.filter(Skill.is_active == is_active)
        query = apply_ordering(
            query, order_by, order_dir, {"created_at": Skill.created_at, "name": Skill.name}
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, skill_id: str, payload: SkillUpdate):
        skill = db.get(Skill, skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(skill, key, value)
        db.commit()
        db.refresh(skill)
        return skill

    @staticmethod
    def delete(db: Session, skill_id: str):
        skill = db.get(Skill, skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        skill.is_active = False
        db.commit()


class Technicians(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: TechnicianProfileCreate):
        _ensure_person(db, str(payload.person_id))
        technician = TechnicianProfile(**payload.model_dump())
        db.add(technician)
        db.commit()
        db.refresh(technician)
        return technician

    @staticmethod
    def get(db: Session, technician_id: str):
        technician = db.get(TechnicianProfile, technician_id)
        if not technician:
            raise HTTPException(status_code=404, detail="Technician not found")
        return technician

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        region: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(TechnicianProfile)
        if person_id:
            query = query.filter(TechnicianProfile.person_id == person_id)
        if region:
            query = query.filter(TechnicianProfile.region == region)
        if is_active is None:
            query = query.filter(TechnicianProfile.is_active.is_(True))
        else:
            query = query.filter(TechnicianProfile.is_active == is_active)
        query = apply_ordering(
            query, order_by, order_dir, {"created_at": TechnicianProfile.created_at}
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, technician_id: str, payload: TechnicianProfileUpdate):
        technician = db.get(TechnicianProfile, technician_id)
        if not technician:
            raise HTTPException(status_code=404, detail="Technician not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("person_id"):
            _ensure_person(db, str(data["person_id"]))
        for key, value in data.items():
            setattr(technician, key, value)
        db.commit()
        db.refresh(technician)
        return technician

    @staticmethod
    def delete(db: Session, technician_id: str):
        technician = db.get(TechnicianProfile, technician_id)
        if not technician:
            raise HTTPException(status_code=404, detail="Technician not found")
        technician.is_active = False
        db.commit()


class TechnicianSkills(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: TechnicianSkillCreate):
        _ensure_technician(db, str(payload.technician_id))
        _ensure_skill(db, str(payload.skill_id))
        skill = TechnicianSkill(**payload.model_dump())
        db.add(skill)
        db.commit()
        db.refresh(skill)
        return skill

    @staticmethod
    def get(db: Session, skill_id: str):
        skill = db.get(TechnicianSkill, skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Technician skill not found")
        return skill

    @staticmethod
    def list(
        db: Session,
        technician_id: str | None,
        skill_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(TechnicianSkill)
        if technician_id:
            query = query.filter(TechnicianSkill.technician_id == technician_id)
        if skill_id:
            query = query.filter(TechnicianSkill.skill_id == skill_id)
        if is_active is None:
            query = query.filter(TechnicianSkill.is_active.is_(True))
        else:
            query = query.filter(TechnicianSkill.is_active == is_active)
        query = apply_ordering(
            query, order_by, order_dir, {"created_at": TechnicianSkill.created_at}
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, skill_id: str, payload: TechnicianSkillUpdate):
        skill = db.get(TechnicianSkill, skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Technician skill not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("technician_id"):
            _ensure_technician(db, str(data["technician_id"]))
        if data.get("skill_id"):
            _ensure_skill(db, str(data["skill_id"]))
        for key, value in data.items():
            setattr(skill, key, value)
        db.commit()
        db.refresh(skill)
        return skill

    @staticmethod
    def delete(db: Session, skill_id: str):
        skill = db.get(TechnicianSkill, skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Technician skill not found")
        skill.is_active = False
        db.commit()


class Shifts(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ShiftCreate):
        _ensure_technician(db, str(payload.technician_id))
        shift = Shift(**payload.model_dump())
        db.add(shift)
        db.commit()
        db.refresh(shift)
        return shift

    @staticmethod
    def get(db: Session, shift_id: str):
        shift = db.get(Shift, shift_id)
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")
        return shift

    @staticmethod
    def list(
        db: Session,
        technician_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Shift)
        if technician_id:
            query = query.filter(Shift.technician_id == technician_id)
        if is_active is None:
            query = query.filter(Shift.is_active.is_(True))
        else:
            query = query.filter(Shift.is_active == is_active)
        query = apply_ordering(
            query, order_by, order_dir, {"created_at": Shift.created_at}
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, shift_id: str, payload: ShiftUpdate):
        shift = db.get(Shift, shift_id)
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("technician_id"):
            _ensure_technician(db, str(data["technician_id"]))
        for key, value in data.items():
            setattr(shift, key, value)
        db.commit()
        db.refresh(shift)
        return shift

    @staticmethod
    def delete(db: Session, shift_id: str):
        shift = db.get(Shift, shift_id)
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")
        shift.is_active = False
        db.commit()


class AvailabilityBlocks(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: AvailabilityBlockCreate):
        _ensure_technician(db, str(payload.technician_id))
        block = AvailabilityBlock(**payload.model_dump())
        db.add(block)
        db.commit()
        db.refresh(block)
        return block

    @staticmethod
    def get(db: Session, block_id: str):
        block = db.get(AvailabilityBlock, block_id)
        if not block:
            raise HTTPException(status_code=404, detail="Availability block not found")
        return block

    @staticmethod
    def list(
        db: Session,
        technician_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(AvailabilityBlock)
        if technician_id:
            query = query.filter(AvailabilityBlock.technician_id == technician_id)
        if is_active is None:
            query = query.filter(AvailabilityBlock.is_active.is_(True))
        else:
            query = query.filter(AvailabilityBlock.is_active == is_active)
        query = apply_ordering(
            query, order_by, order_dir, {"created_at": AvailabilityBlock.created_at}
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, block_id: str, payload: AvailabilityBlockUpdate):
        block = db.get(AvailabilityBlock, block_id)
        if not block:
            raise HTTPException(status_code=404, detail="Availability block not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("technician_id"):
            _ensure_technician(db, str(data["technician_id"]))
        for key, value in data.items():
            setattr(block, key, value)
        db.commit()
        db.refresh(block)
        return block

    @staticmethod
    def delete(db: Session, block_id: str):
        block = db.get(AvailabilityBlock, block_id)
        if not block:
            raise HTTPException(status_code=404, detail="Availability block not found")
        block.is_active = False
        db.commit()


class DispatchRules(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: DispatchRuleCreate):
        rule = DispatchRule(**payload.model_dump())
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule

    @staticmethod
    def get(db: Session, rule_id: str):
        rule = db.get(DispatchRule, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="Dispatch rule not found")
        return rule

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(DispatchRule)
        if is_active is None:
            query = query.filter(DispatchRule.is_active.is_(True))
        else:
            query = query.filter(DispatchRule.is_active == is_active)
        query = apply_ordering(
            query, order_by, order_dir, {"priority": DispatchRule.priority}
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, rule_id: str, payload: DispatchRuleUpdate):
        rule = db.get(DispatchRule, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="Dispatch rule not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(rule, key, value)
        db.commit()
        db.refresh(rule)
        return rule

    @staticmethod
    def delete(db: Session, rule_id: str):
        rule = db.get(DispatchRule, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="Dispatch rule not found")
        rule.is_active = False
        db.commit()


class AssignmentQueue(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: WorkOrderAssignmentQueueCreate):
        _ensure_work_order(db, str(payload.work_order_id))
        entry = WorkOrderAssignmentQueue(**payload.model_dump())
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    @staticmethod
    def get(db: Session, entry_id: str):
        entry = db.get(WorkOrderAssignmentQueue, entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Queue entry not found")
        return entry

    @staticmethod
    def list(
        db: Session,
        work_order_id: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(WorkOrderAssignmentQueue)
        if work_order_id:
            query = query.filter(WorkOrderAssignmentQueue.work_order_id == work_order_id)
        if status:
            query = query.filter(
                WorkOrderAssignmentQueue.status
                == validate_enum(status, DispatchQueueStatus, "status")
            )
        query = apply_ordering(
            query, order_by, order_dir, {"created_at": WorkOrderAssignmentQueue.created_at}
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, entry_id: str, payload: WorkOrderAssignmentQueueUpdate):
        entry = db.get(WorkOrderAssignmentQueue, entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Queue entry not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(entry, key, value)
        db.commit()
        db.refresh(entry)
        return entry

    @staticmethod
    def delete(db: Session, entry_id: str):
        entry = db.get(WorkOrderAssignmentQueue, entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Queue entry not found")
        db.delete(entry)
        db.commit()


def is_technician_available(
    db: Session,
    technician_id: str,
    start_time: datetime,
    duration_minutes: int = 60
) -> bool:
    """Check if technician is available for the given time slot."""
    end_time = start_time + timedelta(minutes=duration_minutes)

    # Get technician profile
    technician = db.get(TechnicianProfile, coerce_uuid(technician_id))
    if not technician or not technician.is_active:
        return False

    # Check shift coverage
    shift = db.query(Shift).filter(
        Shift.technician_id == technician.id,
        Shift.start_at <= start_time,
        Shift.end_at >= end_time,
        Shift.is_active.is_(True)
    ).first()

    if not shift:
        return False

    # Check for blocking availability (unavailable blocks)
    block = db.query(AvailabilityBlock).filter(
        AvailabilityBlock.technician_id == technician.id,
        AvailabilityBlock.is_available.is_(False),
        AvailabilityBlock.start_at < end_time,
        AvailabilityBlock.end_at > start_time,
        AvailabilityBlock.is_active.is_(True)
    ).first()

    if block:
        return False

    # Check for conflicting work orders
    conflict = db.query(WorkOrder).filter(
        WorkOrder.assigned_to_person_id == technician.person_id,
        WorkOrder.status.in_([
            WorkOrderStatus.scheduled,
            WorkOrderStatus.dispatched,
            WorkOrderStatus.in_progress
        ]),
        WorkOrder.scheduled_start < end_time,
        WorkOrder.scheduled_end > start_time,
        WorkOrder.is_active.is_(True)
    ).first()

    return conflict is None


def find_technicians_with_skills(
    db: Session,
    required_skill_ids: list[str],
    region: str | None = None
) -> list[TechnicianProfile]:
    """Find technicians that have all required skills."""
    if not required_skill_ids:
        # No skills required, return all active technicians
        query = db.query(TechnicianProfile).filter(TechnicianProfile.is_active.is_(True))
        if region:
            query = query.filter(TechnicianProfile.region == region)
        return query.all()

    # Find technicians with ALL required skills
    skill_uuids = [coerce_uuid(sid) for sid in required_skill_ids]

    # Subquery: count matching skills per technician
    skill_count_subq = (
        db.query(
            TechnicianSkill.technician_id,
            func.count(TechnicianSkill.skill_id).label('skill_count')
        )
        .filter(
            TechnicianSkill.skill_id.in_(skill_uuids),
            TechnicianSkill.is_active.is_(True)
        )
        .group_by(TechnicianSkill.technician_id)
        .subquery()
    )

    query = (
        db.query(TechnicianProfile)
        .join(skill_count_subq, TechnicianProfile.id == skill_count_subq.c.technician_id)
        .filter(
            TechnicianProfile.is_active.is_(True),
            skill_count_subq.c.skill_count >= len(skill_uuids)
        )
    )

    if region:
        query = query.filter(TechnicianProfile.region == region)

    return query.all()


def score_technician(
    db: Session,
    technician: TechnicianProfile,
    required_skill_ids: list[str],
    work_order: WorkOrder
) -> float:
    """Score a technician for a work order based on skills, workload, and proficiency."""
    score = 100.0

    # Skill proficiency score (0-30 points)
    if required_skill_ids:
        skill_uuids = [coerce_uuid(sid) for sid in required_skill_ids]
        tech_skills = (
            db.query(TechnicianSkill)
            .filter(
                TechnicianSkill.technician_id == technician.id,
                TechnicianSkill.skill_id.in_(skill_uuids),
                TechnicianSkill.is_active.is_(True)
            )
            .all()
        )
        if tech_skills:
            avg_proficiency = sum(ts.proficiency or 50 for ts in tech_skills) / len(tech_skills)
            score += (avg_proficiency / 100) * 30

    # Workload score (0-30 points) - fewer assignments = higher score
    today = datetime.now(UTC).date()
    tomorrow = today + timedelta(days=1)

    active_work_orders = db.query(WorkOrder).filter(
        WorkOrder.assigned_to_person_id == technician.person_id,
        WorkOrder.status.in_([
            WorkOrderStatus.scheduled,
            WorkOrderStatus.dispatched,
            WorkOrderStatus.in_progress
        ]),
        WorkOrder.scheduled_start >= datetime.combine(today, datetime.min.time()).replace(tzinfo=UTC),
        WorkOrder.scheduled_start < datetime.combine(tomorrow, datetime.min.time()).replace(tzinfo=UTC),
        WorkOrder.is_active.is_(True)
    ).count()

    # Assume max 8 work orders per day is full capacity
    workload_factor = max(0, 1 - (active_work_orders / 8))
    score += workload_factor * 30

    # Primary skill bonus (0-10 points)
    if required_skill_ids:
        skill_uuids = [coerce_uuid(sid) for sid in required_skill_ids]
        primary_skill = db.query(TechnicianSkill).filter(
            TechnicianSkill.technician_id == technician.id,
            TechnicianSkill.skill_id.in_(skill_uuids),
            TechnicianSkill.is_primary.is_(True),
            TechnicianSkill.is_active.is_(True)
        ).first()
        if primary_skill:
            score += 10

    return score


def calculate_eta(
    db: Session,
    work_order_id: str,
    travel_time_minutes: int = 30
) -> datetime | None:
    """Calculate and update ETA for a work order."""
    work_order = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not work_order:
        return None

    # If scheduled_start is set, use that as base
    if work_order.scheduled_start:
        eta = work_order.scheduled_start
    else:
        # Default to now + travel time
        eta = datetime.now(UTC) + timedelta(minutes=travel_time_minutes)

    work_order.estimated_arrival_at = eta
    db.commit()
    db.refresh(work_order)

    return eta


def auto_assign_work_order(db: Session, work_order_id: str):
    """Enhanced auto-assign with skill matching, availability checking, and scoring."""
    work_order = _ensure_work_order(db, work_order_id)

    if work_order.assigned_to_person_id:
        return WorkOrderAssignmentQueue(
            work_order_id=work_order.id,
            status=DispatchQueueStatus.assigned,
            reason="Already assigned",
        )

    # 1. Get dispatch rules for configuration
    rules = (
        db.query(DispatchRule)
        .filter(DispatchRule.is_active.is_(True))
        .filter(DispatchRule.auto_assign.is_(True))
        .order_by(DispatchRule.priority.desc())
        .all()
    )

    selected_rule = None
    for rule in rules:
        if rule.work_type and rule.work_type != work_order.work_type.value:
            continue
        if rule.work_priority and rule.work_priority != work_order.priority.value:
            continue
        if rule.region and work_order.address_id is None:
            continue
        selected_rule = rule
        break

    # 2. Determine required skills (from work order or dispatch rule)
    required_skills = work_order.required_skills or []
    if not required_skills and selected_rule and selected_rule.skill_ids:
        required_skills = [str(sid) for sid in selected_rule.skill_ids]

    # 3. Determine region constraint
    region = selected_rule.region if selected_rule else None

    # 4. Find technicians with matching skills
    candidates = find_technicians_with_skills(db, required_skills, region)

    if not candidates:
        entry = WorkOrderAssignmentQueue(
            work_order_id=work_order.id,
            status=DispatchQueueStatus.queued,
            reason="No technicians with required skills",
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    # 5. Filter by availability if scheduled
    duration = work_order.estimated_duration_minutes or 60
    if work_order.scheduled_start:
        available_candidates = [
            t for t in candidates
            if is_technician_available(db, str(t.id), work_order.scheduled_start, duration)
        ]
        if not available_candidates:
            entry = WorkOrderAssignmentQueue(
                work_order_id=work_order.id,
                status=DispatchQueueStatus.queued,
                reason="No technicians available at scheduled time",
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)
            return entry
        candidates = available_candidates

    # 6. Score and rank technicians
    scored_candidates = [
        (t, score_technician(db, t, required_skills, work_order))
        for t in candidates
    ]
    scored_candidates.sort(key=lambda x: x[1], reverse=True)

    # 7. Assign to best match
    best_technician = scored_candidates[0][0]
    best_score = scored_candidates[0][1]

    work_order.assigned_to_person_id = best_technician.person_id

    # 8. Calculate ETA
    calculate_eta(db, work_order_id)

    entry = WorkOrderAssignmentQueue(
        work_order_id=work_order.id,
        status=DispatchQueueStatus.assigned,
        reason=f"Auto-assigned (score: {best_score:.1f})",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def auto_assign_response(db: Session, work_order_id: str) -> dict:
    entry = auto_assign_work_order(db, work_order_id)
    technician_id = None
    if entry.status == DispatchQueueStatus.assigned and entry.work_order:
        technician_id = entry.work_order.assigned_to_person_id
    return {
        "work_order_id": entry.work_order_id,
        "technician_id": technician_id,
        "assignment_status": entry.status,
        "detail": entry.reason,
    }


skills = Skills()
technicians = Technicians()
technician_skills = TechnicianSkills()
shifts = Shifts()
availability_blocks = AvailabilityBlocks()
dispatch_rules = DispatchRules()
assignment_queue = AssignmentQueue()
