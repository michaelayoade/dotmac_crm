from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.network import (
    FdhCabinet,
    FiberEndpointType,
    FiberSegment,
    FiberSegmentType,
    FiberSplice,
    FiberSpliceClosure,
    FiberSpliceTray,
    FiberStrand,
    FiberStrandStatus,
    FiberTerminationPoint,
    ODNEndpointType,
    OltCard,
    OltCardPort,
    OLTDevice,
    OltPortType,
    OltPowerUnit,
    OltSfpModule,
    OltShelf,
    OntAssignment,
    OntUnit,
    PonPort,
    PonPortSplitterLink,
    Splitter,
    SplitterPort,
    SplitterPortType,
)
from app.schemas.network import (
    FdhCabinetCreate,
    FdhCabinetUpdate,
    FiberSegmentCreate,
    FiberSegmentUpdate,
    FiberSpliceClosureCreate,
    FiberSpliceClosureUpdate,
    FiberSpliceCreate,
    FiberSpliceTrayCreate,
    FiberSpliceTrayUpdate,
    FiberSpliceUpdate,
    FiberStrandCreate,
    FiberStrandUpdate,
    FiberTerminationPointCreate,
    FiberTerminationPointUpdate,
    OltCardCreate,
    OltCardPortCreate,
    OltCardPortUpdate,
    OltCardUpdate,
    OLTDeviceCreate,
    OLTDeviceUpdate,
    OltPowerUnitCreate,
    OltPowerUnitUpdate,
    OltSfpModuleCreate,
    OltSfpModuleUpdate,
    OltShelfCreate,
    OltShelfUpdate,
    OntAssignmentCreate,
    OntAssignmentUpdate,
    OntUnitCreate,
    OntUnitUpdate,
    PonPortCreate,
    PonPortSplitterLinkCreate,
    PonPortSplitterLinkUpdate,
    PonPortUpdate,
    SplitterCreate,
    SplitterPortCreate,
    SplitterPortUpdate,
    SplitterUpdate,
)
from app.services import settings_spec
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin


def _safe_get(db: Session, model, item_id: object):
    try:
        if item_id is None:
            raise ValueError("Missing id")
        return db.get(model, coerce_uuid(item_id))
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Resource not found") from exc


class OLTDevices(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: OLTDeviceCreate):
        device = OLTDevice(**payload.model_dump())
        db.add(device)
        db.commit()
        db.refresh(device)
        return device

    @staticmethod
    def get(db: Session, device_id: str):
        device = _safe_get(db, OLTDevice, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="OLT device not found")
        return device

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(OLTDevice)
        if is_active is None:
            query = query.filter(OLTDevice.is_active.is_(True))
        else:
            query = query.filter(OLTDevice.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": OLTDevice.created_at, "name": OLTDevice.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, device_id: str, payload: OLTDeviceUpdate):
        device = _safe_get(db, OLTDevice, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="OLT device not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(device, key, value)
        db.commit()
        db.refresh(device)
        return device

    @staticmethod
    def delete(db: Session, device_id: str):
        device = _safe_get(db, OLTDevice, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="OLT device not found")
        device.is_active = False
        db.commit()


class PonPorts(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: PonPortCreate):
        olt = _safe_get(db, OLTDevice, payload.olt_id)
        if not olt:
            raise HTTPException(status_code=404, detail="OLT device not found")
        if payload.olt_card_port_id:
            card_port = _safe_get(db, OltCardPort, payload.olt_card_port_id)
            if not card_port:
                raise HTTPException(status_code=404, detail="OLT card port not found")
        elif payload.card_id:
            card = _safe_get(db, OltCard, payload.card_id)
            if not card:
                raise HTTPException(status_code=404, detail="OLT card not found")
            if card.shelf and str(card.shelf.olt_id) != str(payload.olt_id):
                raise HTTPException(status_code=400, detail="OLT card does not belong to OLT device")
            card_port = (
                db.query(OltCardPort)
                .filter(OltCardPort.card_id == payload.card_id)
                .filter(OltCardPort.port_number == payload.port_number)
                .first()
            )
            if not card_port:
                card_port = OltCardPort(
                    card_id=payload.card_id,
                    port_number=payload.port_number or 1,
                )
                db.add(card_port)
                db.flush()
            payload.olt_card_port_id = card_port.id
        data = payload.model_dump(exclude={"card_id"})
        port = PonPort(**data)
        db.add(port)
        db.commit()
        db.refresh(port)
        return port

    @staticmethod
    def get(db: Session, port_id: str):
        port = _safe_get(db, PonPort, port_id)
        if not port or not port.is_active:
            raise HTTPException(status_code=404, detail="PON port not found")
        return port

    @staticmethod
    def list(
        db: Session,
        olt_id: str | None = None,
        is_active: bool | None = None,
        order_by: str = "created_at",
        order_dir: str = "asc",
        limit: int = 100,
        offset: int = 0,
        card_id: str | None = None,
    ):
        query = db.query(PonPort)
        if olt_id:
            query = query.filter(PonPort.olt_id == olt_id)
        if card_id:
            query = query.join(OltCardPort, OltCardPort.id == PonPort.olt_card_port_id, isouter=True)
            query = query.filter((OltCardPort.card_id == card_id) | (PonPort.olt_card_port_id.is_(None)))
        if is_active is None:
            query = query.filter(PonPort.is_active.is_(True))
        else:
            query = query.filter(PonPort.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": PonPort.created_at, "name": PonPort.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, port_id: str, payload: PonPortUpdate):
        port = _safe_get(db, PonPort, port_id)
        if not port:
            raise HTTPException(status_code=404, detail="PON port not found")
        data = payload.model_dump(exclude_unset=True)
        if "olt_id" in data:
            olt = _safe_get(db, OLTDevice, data["olt_id"])
            if not olt:
                raise HTTPException(status_code=404, detail="OLT device not found")
        if data.get("olt_card_port_id"):
            card_port = _safe_get(db, OltCardPort, data["olt_card_port_id"])
            if not card_port:
                raise HTTPException(status_code=404, detail="OLT card port not found")
        for key, value in data.items():
            setattr(port, key, value)
        db.commit()
        db.refresh(port)
        return port

    @staticmethod
    def delete(db: Session, port_id: str):
        port = _safe_get(db, PonPort, port_id)
        if not port:
            raise HTTPException(status_code=404, detail="PON port not found")
        port.is_active = False
        db.commit()

    @staticmethod
    def utilization(db: Session, olt_id: str | None):
        query = db.query(PonPort)
        if olt_id:
            query = query.filter(PonPort.olt_id == olt_id)
        total_ports = query.filter(PonPort.is_active.is_(True)).count()
        assigned_ports = db.query(OntAssignment.pon_port_id).filter(OntAssignment.active.is_(True))
        if olt_id:
            assigned_ports = assigned_ports.filter(
                OntAssignment.pon_port_id.in_(db.query(PonPort.id).filter(PonPort.olt_id == olt_id))
            )
        assigned_count = assigned_ports.distinct().count()
        return {
            "olt_id": olt_id,
            "total_ports": total_ports,
            "assigned_ports": assigned_count,
        }


class OntUnits(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: OntUnitCreate):
        unit = OntUnit(**payload.model_dump())
        db.add(unit)
        db.commit()
        db.refresh(unit)
        return unit

    @staticmethod
    def get(db: Session, unit_id: str):
        unit = _safe_get(db, OntUnit, unit_id)
        if not unit:
            raise HTTPException(status_code=404, detail="ONT unit not found")
        return unit

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(OntUnit)
        if is_active is None:
            query = query.filter(OntUnit.is_active.is_(True))
        else:
            query = query.filter(OntUnit.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": OntUnit.created_at, "serial_number": OntUnit.serial_number},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, unit_id: str, payload: OntUnitUpdate):
        unit = _safe_get(db, OntUnit, unit_id)
        if not unit:
            raise HTTPException(status_code=404, detail="ONT unit not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(unit, key, value)
        db.commit()
        db.refresh(unit)
        return unit

    @staticmethod
    def delete(db: Session, unit_id: str):
        unit = _safe_get(db, OntUnit, unit_id)
        if not unit:
            raise HTTPException(status_code=404, detail="ONT unit not found")
        unit.is_active = False
        db.commit()


class OntAssignments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: OntAssignmentCreate):
        assignment = OntAssignment(**payload.model_dump())
        db.add(assignment)
        db.commit()
        db.refresh(assignment)
        return assignment

    @staticmethod
    def get(db: Session, assignment_id: str):
        assignment = _safe_get(db, OntAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="ONT assignment not found")
        return assignment

    @staticmethod
    def list(
        db: Session,
        ont_unit_id: str | None,
        pon_port_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(OntAssignment)
        if ont_unit_id:
            query = query.filter(OntAssignment.ont_unit_id == ont_unit_id)
        if pon_port_id:
            query = query.filter(OntAssignment.pon_port_id == pon_port_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": OntAssignment.created_at, "active": OntAssignment.active},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, assignment_id: str, payload: OntAssignmentUpdate):
        assignment = _safe_get(db, OntAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="ONT assignment not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(assignment, key, value)
        db.commit()
        db.refresh(assignment)
        return assignment

    @staticmethod
    def delete(db: Session, assignment_id: str):
        assignment = _safe_get(db, OntAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="ONT assignment not found")
        db.delete(assignment)
        db.commit()


class OltShelves(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: OltShelfCreate):
        olt = _safe_get(db, OLTDevice, payload.olt_id)
        if not olt:
            raise HTTPException(status_code=404, detail="OLT device not found")
        shelf = OltShelf(**payload.model_dump())
        db.add(shelf)
        db.commit()
        db.refresh(shelf)
        return shelf

    @staticmethod
    def get(db: Session, shelf_id: str):
        shelf = _safe_get(db, OltShelf, shelf_id)
        if not shelf:
            raise HTTPException(status_code=404, detail="OLT shelf not found")
        return shelf

    @staticmethod
    def list(
        db: Session,
        olt_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(OltShelf)
        if olt_id:
            query = query.filter(OltShelf.olt_id == olt_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": OltShelf.created_at, "shelf_number": OltShelf.shelf_number},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, shelf_id: str, payload: OltShelfUpdate):
        shelf = _safe_get(db, OltShelf, shelf_id)
        if not shelf:
            raise HTTPException(status_code=404, detail="OLT shelf not found")
        data = payload.model_dump(exclude_unset=True)
        if "olt_id" in data:
            olt = _safe_get(db, OLTDevice, data["olt_id"])
            if not olt:
                raise HTTPException(status_code=404, detail="OLT device not found")
        for key, value in data.items():
            setattr(shelf, key, value)
        db.commit()
        db.refresh(shelf)
        return shelf

    @staticmethod
    def delete(db: Session, shelf_id: str):
        shelf = _safe_get(db, OltShelf, shelf_id)
        if not shelf:
            raise HTTPException(status_code=404, detail="OLT shelf not found")
        db.delete(shelf)
        db.commit()


class OltCards(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: OltCardCreate):
        shelf = _safe_get(db, OltShelf, payload.shelf_id)
        if not shelf:
            raise HTTPException(status_code=404, detail="OLT shelf not found")
        card = OltCard(**payload.model_dump())
        db.add(card)
        db.commit()
        db.refresh(card)
        return card

    @staticmethod
    def get(db: Session, card_id: str):
        card = _safe_get(db, OltCard, card_id)
        if not card:
            raise HTTPException(status_code=404, detail="OLT card not found")
        return card

    @staticmethod
    def list(
        db: Session,
        shelf_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(OltCard)
        if shelf_id:
            query = query.filter(OltCard.shelf_id == shelf_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": OltCard.created_at, "slot_number": OltCard.slot_number},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, card_id: str, payload: OltCardUpdate):
        card = _safe_get(db, OltCard, card_id)
        if not card:
            raise HTTPException(status_code=404, detail="OLT card not found")
        data = payload.model_dump(exclude_unset=True)
        if "shelf_id" in data:
            shelf = _safe_get(db, OltShelf, data["shelf_id"])
            if not shelf:
                raise HTTPException(status_code=404, detail="OLT shelf not found")
        for key, value in data.items():
            setattr(card, key, value)
        db.commit()
        db.refresh(card)
        return card

    @staticmethod
    def delete(db: Session, card_id: str):
        card = _safe_get(db, OltCard, card_id)
        if not card:
            raise HTTPException(status_code=404, detail="OLT card not found")
        db.delete(card)
        db.commit()


class OltCardPorts(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: OltCardPortCreate):
        card = _safe_get(db, OltCard, payload.card_id)
        if not card:
            raise HTTPException(status_code=404, detail="OLT card not found")
        port = OltCardPort(**payload.model_dump())
        db.add(port)
        db.commit()
        db.refresh(port)
        return port

    @staticmethod
    def get(db: Session, port_id: str):
        port = _safe_get(db, OltCardPort, port_id)
        if not port:
            raise HTTPException(status_code=404, detail="OLT card port not found")
        return port

    @staticmethod
    def list(
        db: Session,
        card_id: str | None = None,
        port_type: str | None = None,
        order_by: str = "created_at",
        order_dir: str = "asc",
        limit: int = 100,
        offset: int = 0,
    ):
        query = db.query(OltCardPort)
        if card_id:
            query = query.filter(OltCardPort.card_id == card_id)
        if port_type:
            query = query.filter(OltCardPort.port_type == validate_enum(port_type, OltPortType, "port_type"))
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": OltCardPort.created_at, "port_number": OltCardPort.port_number},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, port_id: str, payload: OltCardPortUpdate):
        port = _safe_get(db, OltCardPort, port_id)
        if not port:
            raise HTTPException(status_code=404, detail="OLT card port not found")
        data = payload.model_dump(exclude_unset=True)
        if "card_id" in data:
            card = _safe_get(db, OltCard, data["card_id"])
            if not card:
                raise HTTPException(status_code=404, detail="OLT card not found")
        for key, value in data.items():
            setattr(port, key, value)
        db.commit()
        db.refresh(port)
        return port

    @staticmethod
    def delete(db: Session, port_id: str):
        port = _safe_get(db, OltCardPort, port_id)
        if not port:
            raise HTTPException(status_code=404, detail="OLT card port not found")
        db.delete(port)
        db.commit()


class FdhCabinets(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: FdhCabinetCreate):
        cabinet = FdhCabinet(**payload.model_dump())
        db.add(cabinet)
        db.commit()
        db.refresh(cabinet)
        return cabinet

    @staticmethod
    def get(db: Session, cabinet_id: str):
        cabinet = _safe_get(db, FdhCabinet, cabinet_id)
        if not cabinet:
            raise HTTPException(status_code=404, detail="FDH cabinet not found")
        return cabinet

    @staticmethod
    def list(
        db: Session,
        region_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(FdhCabinet)
        if region_id:
            query = query.filter(FdhCabinet.region_id == region_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": FdhCabinet.created_at, "name": FdhCabinet.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, cabinet_id: str, payload: FdhCabinetUpdate):
        cabinet = _safe_get(db, FdhCabinet, cabinet_id)
        if not cabinet:
            raise HTTPException(status_code=404, detail="FDH cabinet not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(cabinet, key, value)
        db.commit()
        db.refresh(cabinet)
        return cabinet

    @staticmethod
    def delete(db: Session, cabinet_id: str):
        cabinet = _safe_get(db, FdhCabinet, cabinet_id)
        if not cabinet:
            raise HTTPException(status_code=404, detail="FDH cabinet not found")
        db.delete(cabinet)
        db.commit()


class Splitters(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: SplitterCreate):
        if payload.fdh_id:
            cabinet = _safe_get(db, FdhCabinet, payload.fdh_id)
            if not cabinet:
                raise HTTPException(status_code=404, detail="FDH cabinet not found")
        data = payload.model_dump()
        fields_set = payload.model_fields_set
        if "input_ports" not in fields_set:
            default_input = settings_spec.resolve_value(db, SettingDomain.network, "default_splitter_input_ports")
            if default_input:
                data["input_ports"] = default_input
        if "output_ports" not in fields_set:
            default_output = settings_spec.resolve_value(db, SettingDomain.network, "default_splitter_output_ports")
            if default_output:
                data["output_ports"] = default_output
        splitter = Splitter(**data)
        db.add(splitter)
        db.commit()
        db.refresh(splitter)
        return splitter

    @staticmethod
    def get(db: Session, splitter_id: str):
        splitter = _safe_get(db, Splitter, splitter_id)
        if not splitter:
            raise HTTPException(status_code=404, detail="Splitter not found")
        return splitter

    @staticmethod
    def list(
        db: Session,
        fdh_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Splitter)
        if fdh_id:
            query = query.filter(Splitter.fdh_id == fdh_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Splitter.created_at, "name": Splitter.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, splitter_id: str, payload: SplitterUpdate):
        splitter = _safe_get(db, Splitter, splitter_id)
        if not splitter:
            raise HTTPException(status_code=404, detail="Splitter not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("fdh_id"):
            cabinet = _safe_get(db, FdhCabinet, data["fdh_id"])
            if not cabinet:
                raise HTTPException(status_code=404, detail="FDH cabinet not found")
        for key, value in data.items():
            setattr(splitter, key, value)
        db.commit()
        db.refresh(splitter)
        return splitter

    @staticmethod
    def delete(db: Session, splitter_id: str):
        splitter = _safe_get(db, Splitter, splitter_id)
        if not splitter:
            raise HTTPException(status_code=404, detail="Splitter not found")
        db.delete(splitter)
        db.commit()


class SplitterPorts(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: SplitterPortCreate):
        splitter = _safe_get(db, Splitter, payload.splitter_id)
        if not splitter:
            raise HTTPException(status_code=404, detail="Splitter not found")
        port = SplitterPort(**payload.model_dump())
        db.add(port)
        db.commit()
        db.refresh(port)
        return port

    @staticmethod
    def get(db: Session, port_id: str):
        port = _safe_get(db, SplitterPort, port_id)
        if not port:
            raise HTTPException(status_code=404, detail="Splitter port not found")
        return port

    @staticmethod
    def list(
        db: Session,
        splitter_id: str | None,
        port_type: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(SplitterPort)
        if splitter_id:
            query = query.filter(SplitterPort.splitter_id == splitter_id)
        if port_type:
            query = query.filter(SplitterPort.port_type == validate_enum(port_type, SplitterPortType, "port_type"))
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": SplitterPort.created_at, "port_number": SplitterPort.port_number},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def utilization(db: Session, splitter_id: str):
        splitter = _safe_get(db, Splitter, splitter_id)
        if not splitter:
            raise HTTPException(status_code=404, detail="Splitter not found")
        total_ports = (
            db.query(SplitterPort)
            .filter(SplitterPort.splitter_id == splitter_id)
            .filter(SplitterPort.is_active.is_(True))
            .count()
        )
        splitter_ports_subquery = db.query(SplitterPort.id).filter(SplitterPort.splitter_id == splitter_id)
        fiber_used = (
            db.query(FiberStrand.upstream_id)
            .filter(FiberStrand.upstream_type == FiberEndpointType.splitter_port)
            .filter(FiberStrand.upstream_id.in_(splitter_ports_subquery))
        )
        used_ports = db.query(SplitterPort.id).filter(SplitterPort.id.in_(fiber_used)).distinct().count()
        return {"splitter_id": splitter_id, "total_ports": total_ports, "used_ports": used_ports}

    @staticmethod
    def update(db: Session, port_id: str, payload: SplitterPortUpdate):
        port = _safe_get(db, SplitterPort, port_id)
        if not port:
            raise HTTPException(status_code=404, detail="Splitter port not found")
        data = payload.model_dump(exclude_unset=True)
        if "splitter_id" in data:
            splitter = _safe_get(db, Splitter, data["splitter_id"])
            if not splitter:
                raise HTTPException(status_code=404, detail="Splitter not found")
        for key, value in data.items():
            setattr(port, key, value)
        db.commit()
        db.refresh(port)
        return port

    @staticmethod
    def delete(db: Session, port_id: str):
        port = _safe_get(db, SplitterPort, port_id)
        if not port:
            raise HTTPException(status_code=404, detail="Splitter port not found")
        db.delete(port)
        db.commit()


class FiberStrands(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: FiberStrandCreate):
        segment = None
        if payload.segment_id:
            segment = _safe_get(db, FiberSegment, payload.segment_id)
            if not segment:
                raise HTTPException(status_code=404, detail="Fiber segment not found")
        data = payload.model_dump(exclude={"segment_id"})
        if segment and (not payload.cable_name or payload.cable_name.startswith("segment-")):
            data["cable_name"] = segment.name
        fields_set = payload.model_fields_set
        if "status" not in fields_set:
            default_status = settings_spec.resolve_value(db, SettingDomain.network, "default_fiber_strand_status")
            if default_status:
                data["status"] = validate_enum(default_status, FiberStrandStatus, "status")
        strand = FiberStrand(**data)
        db.add(strand)
        db.commit()
        db.refresh(strand)
        if segment:
            segment.fiber_strand_id = strand.id
            db.commit()
        return strand

    @staticmethod
    def get(db: Session, strand_id: str):
        strand = _safe_get(db, FiberStrand, strand_id)
        if not strand:
            raise HTTPException(status_code=404, detail="Fiber strand not found")
        return strand

    @staticmethod
    def list(
        db: Session,
        cable_name: str | None = None,
        status: str | None = None,
        order_by: str = "created_at",
        order_dir: str = "asc",
        limit: int = 100,
        offset: int = 0,
        segment_id: str | None = None,
        is_active: bool | None = None,
    ):
        query = db.query(FiberStrand)
        if segment_id:
            segment = _safe_get(db, FiberSegment, segment_id)
            if not segment:
                raise HTTPException(status_code=404, detail="Fiber segment not found")
            if segment.fiber_strand_id:
                query = query.filter(FiberStrand.id == segment.fiber_strand_id)
            else:
                query = query.filter(FiberStrand.cable_name == segment.name)
        if cable_name:
            query = query.filter(FiberStrand.cable_name == cable_name)
        if status:
            query = query.filter(FiberStrand.status == validate_enum(status, FiberStrandStatus, "status"))
        if is_active is None:
            query = query.filter(FiberStrand.is_active.is_(True))
        else:
            query = query.filter(FiberStrand.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": FiberStrand.created_at, "strand_number": FiberStrand.strand_number},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, strand_id: str, payload: FiberStrandUpdate):
        strand = _safe_get(db, FiberStrand, strand_id)
        if not strand:
            raise HTTPException(status_code=404, detail="Fiber strand not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(strand, key, value)
        db.commit()
        db.refresh(strand)
        return strand

    @staticmethod
    def delete(db: Session, strand_id: str):
        strand = _safe_get(db, FiberStrand, strand_id)
        if not strand:
            raise HTTPException(status_code=404, detail="Fiber strand not found")
        strand.is_active = False
        db.commit()


class FiberSpliceClosures(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: FiberSpliceClosureCreate):
        closure = FiberSpliceClosure(**payload.model_dump())
        db.add(closure)
        db.commit()
        db.refresh(closure)
        return closure

    @staticmethod
    def get(db: Session, closure_id: str):
        closure = _safe_get(db, FiberSpliceClosure, closure_id)
        if not closure:
            raise HTTPException(status_code=404, detail="Fiber splice closure not found")
        return closure

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(FiberSpliceClosure)
        if is_active is None:
            query = query.filter(FiberSpliceClosure.is_active.is_(True))
        else:
            query = query.filter(FiberSpliceClosure.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": FiberSpliceClosure.created_at, "name": FiberSpliceClosure.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, closure_id: str, payload: FiberSpliceClosureUpdate):
        closure = _safe_get(db, FiberSpliceClosure, closure_id)
        if not closure:
            raise HTTPException(status_code=404, detail="Fiber splice closure not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(closure, key, value)
        db.commit()
        db.refresh(closure)
        return closure

    @staticmethod
    def delete(db: Session, closure_id: str):
        closure = _safe_get(db, FiberSpliceClosure, closure_id)
        if not closure:
            raise HTTPException(status_code=404, detail="Fiber splice closure not found")
        closure.is_active = False
        db.commit()


class FiberSplices(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: FiberSpliceCreate):
        data = payload.model_dump(exclude={"position"})
        if payload.closure_id and payload.from_strand_id and payload.to_strand_id:
            closure = _safe_get(db, FiberSpliceClosure, payload.closure_id)
            if not closure:
                raise HTTPException(status_code=404, detail="Fiber splice closure not found")
            from_strand = _safe_get(db, FiberStrand, payload.from_strand_id)
            if not from_strand:
                raise HTTPException(status_code=404, detail="Fiber strand not found")
            to_strand = _safe_get(db, FiberStrand, payload.to_strand_id)
            if not to_strand:
                raise HTTPException(status_code=404, detail="Fiber strand not found")
        elif payload.tray_id:
            tray = _safe_get(db, FiberSpliceTray, payload.tray_id)
            if not tray:
                raise HTTPException(status_code=404, detail="Fiber splice tray not found")
            data["tray_id"] = tray.id
            data["closure_id"] = tray.closure_id
            if not payload.from_strand_id or not payload.to_strand_id:
                base_number = payload.position or 1
                cable_name = f"tray-{tray.id}"
                from_strand = FiberStrand(
                    cable_name=cable_name,
                    strand_number=base_number * 2 - 1,
                )
                to_strand = FiberStrand(
                    cable_name=cable_name,
                    strand_number=base_number * 2,
                )
                db.add(from_strand)
                db.add(to_strand)
                db.flush()
                data["from_strand_id"] = from_strand.id
                data["to_strand_id"] = to_strand.id
        splice = FiberSplice(**data)
        db.add(splice)
        db.commit()
        db.refresh(splice)
        splice.position = payload.position
        return splice

    @staticmethod
    def get(db: Session, splice_id: str):
        splice = _safe_get(db, FiberSplice, splice_id)
        if not splice:
            raise HTTPException(status_code=404, detail="Fiber splice not found")
        return splice

    @staticmethod
    def list(
        db: Session,
        closure_id: str | None = None,
        strand_id: str | None = None,
        order_by: str = "created_at",
        order_dir: str = "asc",
        limit: int = 100,
        offset: int = 0,
        tray_id: str | None = None,
    ):
        query = db.query(FiberSplice)
        if closure_id:
            query = query.filter(FiberSplice.closure_id == closure_id)
        if tray_id:
            query = query.filter(FiberSplice.tray_id == tray_id)
        if strand_id:
            query = query.filter((FiberSplice.from_strand_id == strand_id) | (FiberSplice.to_strand_id == strand_id))
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": FiberSplice.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, splice_id: str, payload: FiberSpliceUpdate):
        splice = _safe_get(db, FiberSplice, splice_id)
        if not splice:
            raise HTTPException(status_code=404, detail="Fiber splice not found")
        data = payload.model_dump(exclude_unset=True)
        if "closure_id" in data:
            closure = _safe_get(db, FiberSpliceClosure, data["closure_id"])
            if not closure:
                raise HTTPException(status_code=404, detail="Fiber splice closure not found")
        if "from_strand_id" in data:
            from_strand = _safe_get(db, FiberStrand, data["from_strand_id"])
            if not from_strand:
                raise HTTPException(status_code=404, detail="Fiber strand not found")
        if "to_strand_id" in data:
            to_strand = _safe_get(db, FiberStrand, data["to_strand_id"])
            if not to_strand:
                raise HTTPException(status_code=404, detail="Fiber strand not found")
        for key, value in data.items():
            setattr(splice, key, value)
        db.commit()
        db.refresh(splice)
        return splice

    @staticmethod
    def delete(db: Session, splice_id: str):
        splice = _safe_get(db, FiberSplice, splice_id)
        if not splice:
            raise HTTPException(status_code=404, detail="Fiber splice not found")
        db.delete(splice)
        db.commit()


class FiberSpliceTrays(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: FiberSpliceTrayCreate):
        tray = FiberSpliceTray(**payload.model_dump())
        db.add(tray)
        db.commit()
        db.refresh(tray)
        return tray

    @staticmethod
    def get(db: Session, tray_id: str):
        tray = _safe_get(db, FiberSpliceTray, tray_id)
        if not tray:
            raise HTTPException(status_code=404, detail="Fiber splice tray not found")
        return tray

    @staticmethod
    def list(
        db: Session,
        closure_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(FiberSpliceTray)
        if closure_id:
            query = query.filter(FiberSpliceTray.closure_id == closure_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": FiberSpliceTray.created_at, "tray_number": FiberSpliceTray.tray_number},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, tray_id: str, payload: FiberSpliceTrayUpdate):
        tray = _safe_get(db, FiberSpliceTray, tray_id)
        if not tray:
            raise HTTPException(status_code=404, detail="Fiber splice tray not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(tray, key, value)
        db.commit()
        db.refresh(tray)
        return tray

    @staticmethod
    def delete(db: Session, tray_id: str):
        tray = _safe_get(db, FiberSpliceTray, tray_id)
        if not tray:
            raise HTTPException(status_code=404, detail="Fiber splice tray not found")
        db.delete(tray)
        db.commit()


class FiberTerminationPoints(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: FiberTerminationPointCreate):
        point = FiberTerminationPoint(**payload.model_dump())
        db.add(point)
        db.commit()
        db.refresh(point)
        return point

    @staticmethod
    def get(db: Session, point_id: str):
        point = _safe_get(db, FiberTerminationPoint, point_id)
        if not point:
            raise HTTPException(status_code=404, detail="Fiber termination point not found")
        return point

    @staticmethod
    def list(
        db: Session,
        endpoint_type: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(FiberTerminationPoint)
        if endpoint_type:
            query = query.filter(
                FiberTerminationPoint.endpoint_type == validate_enum(endpoint_type, ODNEndpointType, "endpoint_type")
            )
        if is_active is None:
            query = query.filter(FiberTerminationPoint.is_active.is_(True))
        else:
            query = query.filter(FiberTerminationPoint.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": FiberTerminationPoint.created_at, "name": FiberTerminationPoint.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, point_id: str, payload: FiberTerminationPointUpdate):
        point = _safe_get(db, FiberTerminationPoint, point_id)
        if not point:
            raise HTTPException(status_code=404, detail="Fiber termination point not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(point, key, value)
        db.commit()
        db.refresh(point)
        return point

    @staticmethod
    def delete(db: Session, point_id: str):
        point = _safe_get(db, FiberTerminationPoint, point_id)
        if not point:
            raise HTTPException(status_code=404, detail="Fiber termination point not found")
        db.delete(point)
        db.commit()


class FiberSegments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: FiberSegmentCreate):
        segment = FiberSegment(**payload.model_dump())
        db.add(segment)
        db.commit()
        db.refresh(segment)
        return segment

    @staticmethod
    def get(db: Session, segment_id: str):
        segment = _safe_get(db, FiberSegment, segment_id)
        if not segment:
            raise HTTPException(status_code=404, detail="Fiber segment not found")
        return segment

    @staticmethod
    def list(
        db: Session,
        segment_type: str | None,
        fiber_strand_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(FiberSegment)
        if segment_type:
            query = query.filter(
                FiberSegment.segment_type == validate_enum(segment_type, FiberSegmentType, "segment_type")
            )
        if fiber_strand_id:
            query = query.filter(FiberSegment.fiber_strand_id == fiber_strand_id)
        if is_active is None:
            query = query.filter(FiberSegment.is_active.is_(True))
        else:
            query = query.filter(FiberSegment.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": FiberSegment.created_at, "name": FiberSegment.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, segment_id: str, payload: FiberSegmentUpdate):
        segment = _safe_get(db, FiberSegment, segment_id)
        if not segment:
            raise HTTPException(status_code=404, detail="Fiber segment not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(segment, key, value)
        db.commit()
        db.refresh(segment)
        return segment

    @staticmethod
    def delete(db: Session, segment_id: str):
        segment = _safe_get(db, FiberSegment, segment_id)
        if not segment:
            raise HTTPException(status_code=404, detail="Fiber segment not found")
        segment.is_active = False
        db.commit()


class PonPortSplitterLinks(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: PonPortSplitterLinkCreate):
        link = PonPortSplitterLink(**payload.model_dump())
        db.add(link)
        db.commit()
        db.refresh(link)
        return link

    @staticmethod
    def get(db: Session, link_id: str):
        link = _safe_get(db, PonPortSplitterLink, link_id)
        if not link:
            raise HTTPException(status_code=404, detail="PON port link not found")
        return link

    @staticmethod
    def list(
        db: Session,
        pon_port_id: str | None,
        splitter_port_id: str | None,
        active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(PonPortSplitterLink)
        if pon_port_id:
            query = query.filter(PonPortSplitterLink.pon_port_id == pon_port_id)
        if splitter_port_id:
            query = query.filter(PonPortSplitterLink.splitter_port_id == splitter_port_id)
        if active is None:
            query = query.filter(PonPortSplitterLink.active.is_(True))
        else:
            query = query.filter(PonPortSplitterLink.active == active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": PonPortSplitterLink.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, link_id: str, payload: PonPortSplitterLinkUpdate):
        link = _safe_get(db, PonPortSplitterLink, link_id)
        if not link:
            raise HTTPException(status_code=404, detail="PON port link not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(link, key, value)
        db.commit()
        db.refresh(link)
        return link

    @staticmethod
    def delete(db: Session, link_id: str):
        link = _safe_get(db, PonPortSplitterLink, link_id)
        if not link:
            raise HTTPException(status_code=404, detail="PON port link not found")
        db.delete(link)
        db.commit()


class OltPowerUnits(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: OltPowerUnitCreate):
        unit = OltPowerUnit(**payload.model_dump())
        db.add(unit)
        db.commit()
        db.refresh(unit)
        return unit

    @staticmethod
    def get(db: Session, unit_id: str):
        unit = _safe_get(db, OltPowerUnit, unit_id)
        if not unit:
            raise HTTPException(status_code=404, detail="OLT power unit not found")
        return unit

    @staticmethod
    def list(
        db: Session,
        olt_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(OltPowerUnit)
        if olt_id:
            query = query.filter(OltPowerUnit.olt_id == olt_id)
        if is_active is None:
            query = query.filter(OltPowerUnit.is_active.is_(True))
        else:
            query = query.filter(OltPowerUnit.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": OltPowerUnit.created_at, "slot": OltPowerUnit.slot},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, unit_id: str, payload: OltPowerUnitUpdate):
        unit = _safe_get(db, OltPowerUnit, unit_id)
        if not unit:
            raise HTTPException(status_code=404, detail="OLT power unit not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(unit, key, value)
        db.commit()
        db.refresh(unit)
        return unit

    @staticmethod
    def delete(db: Session, unit_id: str):
        unit = _safe_get(db, OltPowerUnit, unit_id)
        if not unit:
            raise HTTPException(status_code=404, detail="OLT power unit not found")
        unit.is_active = False
        db.commit()


class OltSfpModules(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: OltSfpModuleCreate):
        module = OltSfpModule(**payload.model_dump())
        db.add(module)
        db.commit()
        db.refresh(module)
        return module

    @staticmethod
    def get(db: Session, module_id: str):
        module = _safe_get(db, OltSfpModule, module_id)
        if not module:
            raise HTTPException(status_code=404, detail="OLT SFP module not found")
        return module

    @staticmethod
    def list(
        db: Session,
        olt_card_port_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(OltSfpModule)
        if olt_card_port_id:
            query = query.filter(OltSfpModule.olt_card_port_id == olt_card_port_id)
        if is_active is None:
            query = query.filter(OltSfpModule.is_active.is_(True))
        else:
            query = query.filter(OltSfpModule.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": OltSfpModule.created_at, "serial_number": OltSfpModule.serial_number},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, module_id: str, payload: OltSfpModuleUpdate):
        module = _safe_get(db, OltSfpModule, module_id)
        if not module:
            raise HTTPException(status_code=404, detail="OLT SFP module not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(module, key, value)
        db.commit()
        db.refresh(module)
        return module

    @staticmethod
    def delete(db: Session, module_id: str):
        module = _safe_get(db, OltSfpModule, module_id)
        if not module:
            raise HTTPException(status_code=404, detail="OLT SFP module not found")
        module.is_active = False
        db.commit()


# Service instances
olt_devices = OLTDevices()
pon_ports = PonPorts()
ont_units = OntUnits()
ont_assignments = OntAssignments()
olt_shelves = OltShelves()
olt_cards = OltCards()
olt_card_ports = OltCardPorts()
fdh_cabinets = FdhCabinets()
splitters = Splitters()
splitter_ports = SplitterPorts()
fiber_strands = FiberStrands()
fiber_splice_closures = FiberSpliceClosures()
fiber_splice_trays = FiberSpliceTrays()
fiber_splices = FiberSplices()
fiber_termination_points = FiberTerminationPoints()
fiber_segments = FiberSegments()
pon_port_splitter_links = PonPortSplitterLinks()
olt_power_units = OltPowerUnits()
olt_sfp_modules = OltSfpModules()
