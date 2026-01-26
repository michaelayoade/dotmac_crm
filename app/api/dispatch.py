from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.services.response import list_response
from app.db import SessionLocal
from app.schemas.common import ListResponse
from app.schemas.dispatch import (
    AvailabilityBlockCreate,
    AvailabilityBlockRead,
    AvailabilityBlockUpdate,
    AutoAssignResponse,
    DispatchRuleCreate,
    DispatchRuleRead,
    DispatchRuleUpdate,
    ShiftCreate,
    ShiftRead,
    ShiftUpdate,
    SkillCreate,
    SkillRead,
    SkillUpdate,
    TechnicianProfileCreate,
    TechnicianProfileRead,
    TechnicianProfileUpdate,
    TechnicianSkillCreate,
    TechnicianSkillRead,
    TechnicianSkillUpdate,
    WorkOrderAssignmentQueueCreate,
    WorkOrderAssignmentQueueRead,
    WorkOrderAssignmentQueueUpdate,
)
from app.services import dispatch as dispatch_service

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/skills", response_model=SkillRead, status_code=status.HTTP_201_CREATED)
def create_skill(payload: SkillCreate, db: Session = Depends(get_db)):
    return dispatch_service.skills.create(db, payload)


@router.get("/skills/{skill_id}", response_model=SkillRead)
def get_skill(skill_id: str, db: Session = Depends(get_db)):
    return dispatch_service.skills.get(db, skill_id)


@router.get("/skills", response_model=ListResponse[SkillRead])
def list_skills(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = dispatch_service.skills.list(
        db, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/skills/{skill_id}", response_model=SkillRead)
def update_skill(
    skill_id: str, payload: SkillUpdate, db: Session = Depends(get_db)
):
    return dispatch_service.skills.update(db, skill_id, payload)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(skill_id: str, db: Session = Depends(get_db)):
    dispatch_service.skills.delete(db, skill_id)


@router.post(
    "/technicians",
    response_model=TechnicianProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def create_technician(
    payload: TechnicianProfileCreate, db: Session = Depends(get_db)
):
    return dispatch_service.technicians.create(db, payload)


@router.get("/technicians/{technician_id}", response_model=TechnicianProfileRead)
def get_technician(technician_id: str, db: Session = Depends(get_db)):
    return dispatch_service.technicians.get(db, technician_id)


@router.get("/technicians", response_model=ListResponse[TechnicianProfileRead])
def list_technicians(
    person_id: str | None = None,
    region: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = dispatch_service.technicians.list(
        db, person_id, region, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/technicians/{technician_id}", response_model=TechnicianProfileRead)
def update_technician(
    technician_id: str, payload: TechnicianProfileUpdate, db: Session = Depends(get_db)
):
    return dispatch_service.technicians.update(db, technician_id, payload)


@router.delete("/technicians/{technician_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_technician(technician_id: str, db: Session = Depends(get_db)):
    dispatch_service.technicians.delete(db, technician_id)


@router.post(
    "/technician-skills",
    response_model=TechnicianSkillRead,
    status_code=status.HTTP_201_CREATED,
)
def create_technician_skill(
    payload: TechnicianSkillCreate, db: Session = Depends(get_db)
):
    return dispatch_service.technician_skills.create(db, payload)


@router.get("/technician-skills/{skill_id}", response_model=TechnicianSkillRead)
def get_technician_skill(skill_id: str, db: Session = Depends(get_db)):
    return dispatch_service.technician_skills.get(db, skill_id)


@router.get("/technician-skills", response_model=ListResponse[TechnicianSkillRead])
def list_technician_skills(
    technician_id: str | None = None,
    skill_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = dispatch_service.technician_skills.list(
        db, technician_id, skill_id, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/technician-skills/{skill_id}", response_model=TechnicianSkillRead)
def update_technician_skill(
    skill_id: str, payload: TechnicianSkillUpdate, db: Session = Depends(get_db)
):
    return dispatch_service.technician_skills.update(db, skill_id, payload)


@router.delete("/technician-skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_technician_skill(skill_id: str, db: Session = Depends(get_db)):
    dispatch_service.technician_skills.delete(db, skill_id)


@router.post("/shifts", response_model=ShiftRead, status_code=status.HTTP_201_CREATED)
def create_shift(payload: ShiftCreate, db: Session = Depends(get_db)):
    return dispatch_service.shifts.create(db, payload)


@router.get("/shifts/{shift_id}", response_model=ShiftRead)
def get_shift(shift_id: str, db: Session = Depends(get_db)):
    return dispatch_service.shifts.get(db, shift_id)


@router.get("/shifts", response_model=ListResponse[ShiftRead])
def list_shifts(
    technician_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = dispatch_service.shifts.list(
        db, technician_id, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/shifts/{shift_id}", response_model=ShiftRead)
def update_shift(
    shift_id: str, payload: ShiftUpdate, db: Session = Depends(get_db)
):
    return dispatch_service.shifts.update(db, shift_id, payload)


@router.delete("/shifts/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shift(shift_id: str, db: Session = Depends(get_db)):
    dispatch_service.shifts.delete(db, shift_id)


@router.post(
    "/availability-blocks",
    response_model=AvailabilityBlockRead,
    status_code=status.HTTP_201_CREATED,
)
def create_availability_block(
    payload: AvailabilityBlockCreate, db: Session = Depends(get_db)
):
    return dispatch_service.availability_blocks.create(db, payload)


@router.get(
    "/availability-blocks/{block_id}", response_model=AvailabilityBlockRead
)
def get_availability_block(block_id: str, db: Session = Depends(get_db)):
    return dispatch_service.availability_blocks.get(db, block_id)


@router.get("/availability-blocks", response_model=ListResponse[AvailabilityBlockRead])
def list_availability_blocks(
    technician_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = dispatch_service.availability_blocks.list(
        db, technician_id, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch(
    "/availability-blocks/{block_id}",
    response_model=AvailabilityBlockRead,
)
def update_availability_block(
    block_id: str, payload: AvailabilityBlockUpdate, db: Session = Depends(get_db)
):
    return dispatch_service.availability_blocks.update(db, block_id, payload)


@router.delete(
    "/availability-blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_availability_block(block_id: str, db: Session = Depends(get_db)):
    dispatch_service.availability_blocks.delete(db, block_id)


@router.post(
    "/rules",
    response_model=DispatchRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_dispatch_rule(
    payload: DispatchRuleCreate, db: Session = Depends(get_db)
):
    return dispatch_service.dispatch_rules.create(db, payload)


@router.get("/rules/{rule_id}", response_model=DispatchRuleRead)
def get_dispatch_rule(rule_id: str, db: Session = Depends(get_db)):
    return dispatch_service.dispatch_rules.get(db, rule_id)


@router.get("/rules", response_model=ListResponse[DispatchRuleRead])
def list_dispatch_rules(
    is_active: bool | None = None,
    order_by: str = Query(default="priority"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = dispatch_service.dispatch_rules.list(
        db, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/rules/{rule_id}", response_model=DispatchRuleRead)
def update_dispatch_rule(
    rule_id: str, payload: DispatchRuleUpdate, db: Session = Depends(get_db)
):
    return dispatch_service.dispatch_rules.update(db, rule_id, payload)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dispatch_rule(rule_id: str, db: Session = Depends(get_db)):
    dispatch_service.dispatch_rules.delete(db, rule_id)


@router.post(
    "/queue",
    response_model=WorkOrderAssignmentQueueRead,
    status_code=status.HTTP_201_CREATED,
)
def create_queue_entry(
    payload: WorkOrderAssignmentQueueCreate, db: Session = Depends(get_db)
):
    return dispatch_service.assignment_queue.create(db, payload)


@router.get("/queue/{entry_id}", response_model=WorkOrderAssignmentQueueRead)
def get_queue_entry(entry_id: str, db: Session = Depends(get_db)):
    return dispatch_service.assignment_queue.get(db, entry_id)


@router.get("/queue", response_model=ListResponse[WorkOrderAssignmentQueueRead])
def list_queue_entries(
    work_order_id: str | None = None,
    status: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = dispatch_service.assignment_queue.list(
        db, work_order_id, status, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/queue/{entry_id}", response_model=WorkOrderAssignmentQueueRead)
def update_queue_entry(
    entry_id: str, payload: WorkOrderAssignmentQueueUpdate, db: Session = Depends(get_db)
):
    return dispatch_service.assignment_queue.update(db, entry_id, payload)


@router.delete("/queue/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_queue_entry(entry_id: str, db: Session = Depends(get_db)):
    dispatch_service.assignment_queue.delete(db, entry_id)


@router.post(
    "/work-orders/{work_order_id}/auto-assign",
    response_model=AutoAssignResponse,
    tags=["work-orders"],
)
def auto_assign_work_order(work_order_id: str, db: Session = Depends(get_db)):
    payload = dispatch_service.auto_assign_response(db, work_order_id)
    return AutoAssignResponse(**payload)
