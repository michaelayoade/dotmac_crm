from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.projects import (
    Project,
    ProjectComment,
    ProjectPriority,
    ProjectStatus,
    ProjectTask,
    ProjectTaskComment,
    ProjectTemplate,
    ProjectTemplateTask,
    ProjectType,
    TaskPriority,
    TaskStatus,
)
from app.models.subscriber import SubscriberAccount
from app.models.tickets import Ticket
from app.models.workforce import WorkOrder
from app.models.domain_settings import SettingDomain
from app.schemas.projects import (
    ProjectCommentCreate,
    ProjectCreate,
    ProjectTaskCreate,
    ProjectTaskCommentCreate,
    ProjectTaskUpdate,
    ProjectTemplateCreate,
    ProjectTemplateTaskCreate,
    ProjectTemplateTaskUpdate,
    ProjectTemplateUpdate,
    ProjectUpdate,
)
from app.services.common import (
    apply_ordering,
    apply_pagination,
    coerce_uuid,
    ensure_exists,
    validate_enum,
)
from app.services.response import ListResponseMixin
from app.services import settings_spec


def _ensure_person(db: Session, person_id: str):
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")


def _ensure_account(db: Session, account_id: str):
    account = db.get(SubscriberAccount, coerce_uuid(account_id))
    if not account:
        raise HTTPException(status_code=404, detail="Subscriber account not found")


def _ensure_project_template(db: Session, template_id: str):
    template = db.get(ProjectTemplate, coerce_uuid(template_id))
    if not template:
        raise HTTPException(status_code=404, detail="Project template not found")
    return template


class Projects(ListResponseMixin):
    @staticmethod
    def list_for_site_surveys(db: Session):
        return (
            db.query(Project)
            .filter(Project.status.notin_([ProjectStatus.canceled, ProjectStatus.completed]))
            .order_by(Project.name)
            .all()
        )

    @staticmethod
    def create(db: Session, payload: ProjectCreate):
        if payload.account_id:
            _ensure_account(db, str(payload.account_id))
        if payload.created_by_person_id:
            _ensure_person(db, str(payload.created_by_person_id))
        if payload.owner_person_id:
            _ensure_person(db, str(payload.owner_person_id))
        if payload.manager_person_id:
            _ensure_person(db, str(payload.manager_person_id))
        if payload.project_template_id:
            _ensure_project_template(db, str(payload.project_template_id))
        data = payload.model_dump()
        fields_set = payload.model_fields_set
        if "status" not in fields_set:
            default_status = settings_spec.resolve_value(
                db, SettingDomain.projects, "default_project_status"
            )
            if default_status:
                data["status"] = validate_enum(
                    default_status, ProjectStatus, "status"
                )
        if "priority" not in fields_set:
            default_priority = settings_spec.resolve_value(
                db, SettingDomain.projects, "default_project_priority"
            )
            if default_priority:
                data["priority"] = validate_enum(
                    default_priority, ProjectPriority, "priority"
                )
        project = Project(**data)
        db.add(project)
        db.commit()
        db.refresh(project)
        if payload.project_template_id:
            ProjectTemplateTasks.replace_project_tasks(
                db=db, project_id=str(project.id), template_id=str(payload.project_template_id)
            )
        return project

    @staticmethod
    def get(db: Session, project_id: str):
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    @staticmethod
    def list(
        db: Session,
        account_id: str | None,
        status: str | None,
        priority: str | None,
        owner_person_id: str | None,
        manager_person_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Project)
        if account_id:
            query = query.filter(Project.account_id == account_id)
        if status:
            query = query.filter(Project.status == validate_enum(status, ProjectStatus, "status"))
        if priority:
            query = query.filter(
                Project.priority == validate_enum(priority, ProjectPriority, "priority")
            )
        if owner_person_id:
            query = query.filter(Project.owner_person_id == owner_person_id)
        if manager_person_id:
            query = query.filter(Project.manager_person_id == manager_person_id)
        if is_active is None:
            query = query.filter(Project.is_active.is_(True))
        else:
            query = query.filter(Project.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Project.created_at, "name": Project.name, "priority": Project.priority},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, project_id: str, payload: ProjectUpdate):
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        previous_template_id = str(project.project_template_id) if project.project_template_id else None
        data = payload.model_dump(exclude_unset=True)
        if "account_id" in data and data["account_id"]:
            _ensure_account(db, str(data["account_id"]))
        if "created_by_person_id" in data and data["created_by_person_id"]:
            _ensure_person(db, str(data["created_by_person_id"]))
        if "owner_person_id" in data and data["owner_person_id"]:
            _ensure_person(db, str(data["owner_person_id"]))
        if "manager_person_id" in data and data["manager_person_id"]:
            _ensure_person(db, str(data["manager_person_id"]))
        if "project_template_id" in data and data["project_template_id"]:
            _ensure_project_template(db, str(data["project_template_id"]))
        for key, value in data.items():
            setattr(project, key, value)
        db.commit()
        db.refresh(project)
        if "project_template_id" in data:
            new_template_id = str(project.project_template_id) if project.project_template_id else None
            if previous_template_id != new_template_id:
                ProjectTemplateTasks.replace_project_tasks(
                    db=db, project_id=str(project.id), template_id=new_template_id
                )
        return project


class ProjectTemplates(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectTemplateCreate):
        data = payload.model_dump()
        template = ProjectTemplate(**data)
        db.add(template)
        db.commit()
        db.refresh(template)
        return template

    @staticmethod
    def get(db: Session, template_id: str):
        template = db.get(ProjectTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Project template not found")
        return template

    @staticmethod
    def list(
        db: Session,
        project_type: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectTemplate)
        if project_type:
            query = query.filter(
                ProjectTemplate.project_type
                == validate_enum(project_type, ProjectType, "project_type")
            )
        if is_active is None:
            query = query.filter(ProjectTemplate.is_active.is_(True))
        else:
            query = query.filter(ProjectTemplate.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": ProjectTemplate.created_at,
                "name": ProjectTemplate.name,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, template_id: str, payload: ProjectTemplateUpdate):
        template = db.get(ProjectTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Project template not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(template, key, value)
        db.commit()
        db.refresh(template)
        return template

    @staticmethod
    def delete(db: Session, template_id: str):
        template = db.get(ProjectTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Project template not found")
        template.is_active = False
        db.commit()

    @staticmethod
    def list_tasks(db: Session, template_id: str):
        return (
            db.query(ProjectTemplateTask)
            .filter(ProjectTemplateTask.template_id == template_id)
            .filter(ProjectTemplateTask.is_active.is_(True))
            .order_by(ProjectTemplateTask.sort_order.asc(), ProjectTemplateTask.created_at.asc())
            .all()
        )


class ProjectTemplateTasks(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectTemplateTaskCreate):
        _ensure_project_template(db, str(payload.template_id))
        data = payload.model_dump()
        task = ProjectTemplateTask(**data)
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def get(db: Session, task_id: str):
        task = db.get(ProjectTemplateTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project template task not found")
        return task

    @staticmethod
    def list(
        db: Session,
        template_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectTemplateTask)
        if template_id:
            query = query.filter(ProjectTemplateTask.template_id == template_id)
        if is_active is None:
            query = query.filter(ProjectTemplateTask.is_active.is_(True))
        else:
            query = query.filter(ProjectTemplateTask.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": ProjectTemplateTask.created_at,
                "sort_order": ProjectTemplateTask.sort_order,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, task_id: str, payload: ProjectTemplateTaskUpdate):
        task = db.get(ProjectTemplateTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project template task not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(task, key, value)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def delete(db: Session, task_id: str):
        task = db.get(ProjectTemplateTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project template task not found")
        task.is_active = False
        db.commit()

    @staticmethod
    def replace_project_tasks(db: Session, project_id: str, template_id: str | None):
        project_uuid = coerce_uuid(project_id)
        db.query(ProjectTask).filter(
            ProjectTask.project_id == project_uuid,
            ProjectTask.template_task_id.isnot(None),
        ).delete(synchronize_session=False)
        if not template_id:
            db.commit()
            return
        template_tasks = (
            db.query(ProjectTemplateTask)
            .filter(ProjectTemplateTask.template_id == template_id)
            .filter(ProjectTemplateTask.is_active.is_(True))
            .order_by(ProjectTemplateTask.sort_order.asc(), ProjectTemplateTask.created_at.asc())
            .all()
        )
        for template_task in template_tasks:
            data = {
                "project_id": project_uuid,
                "title": template_task.title,
                "template_task_id": template_task.id,
            }
            if template_task.description:
                data["description"] = template_task.description
            if template_task.status:
                data["status"] = template_task.status
            if template_task.priority:
                data["priority"] = template_task.priority
            db.add(ProjectTask(**data))
        db.commit()

    @staticmethod
    def delete(db: Session, project_id: str):
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project.is_active = False
        db.commit()


class ProjectTasks(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectTaskCreate):
        project = db.get(Project, payload.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if payload.parent_task_id:
            parent = db.get(ProjectTask, payload.parent_task_id)
            if not parent:
                raise HTTPException(status_code=404, detail="Parent task not found")
        if payload.assigned_to_person_id:
            _ensure_person(db, str(payload.assigned_to_person_id))
        if payload.created_by_person_id:
            _ensure_person(db, str(payload.created_by_person_id))
        if payload.ticket_id:
            ticket = db.get(Ticket, payload.ticket_id)
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found")
        if payload.work_order_id:
            work_order = db.get(WorkOrder, payload.work_order_id)
            if not work_order:
                raise HTTPException(status_code=404, detail="Work order not found")
        data = payload.model_dump()
        fields_set = payload.model_fields_set
        if "status" not in fields_set:
            default_status = settings_spec.resolve_value(
                db, SettingDomain.projects, "default_task_status"
            )
            if default_status:
                data["status"] = validate_enum(
                    default_status, TaskStatus, "status"
                )
        if "priority" not in fields_set:
            default_priority = settings_spec.resolve_value(
                db, SettingDomain.projects, "default_task_priority"
            )
            if default_priority:
                data["priority"] = validate_enum(
                    default_priority, TaskPriority, "priority"
                )
        task = ProjectTask(**data)
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def get(db: Session, task_id: str):
        task = db.get(ProjectTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project task not found")
        return task

    @staticmethod
    def list(
        db: Session,
        project_id: str | None,
        status: str | None,
        priority: str | None,
        assigned_to_person_id: str | None,
        parent_task_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectTask)
        if project_id:
            query = query.filter(ProjectTask.project_id == project_id)
        if status:
            query = query.filter(ProjectTask.status == validate_enum(status, TaskStatus, "status"))
        if priority:
            query = query.filter(
                ProjectTask.priority == validate_enum(priority, TaskPriority, "priority")
            )
        if assigned_to_person_id:
            query = query.filter(ProjectTask.assigned_to_person_id == assigned_to_person_id)
        if parent_task_id:
            query = query.filter(ProjectTask.parent_task_id == parent_task_id)
        if is_active is None:
            query = query.filter(ProjectTask.is_active.is_(True))
        else:
            query = query.filter(ProjectTask.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": ProjectTask.created_at,
                "status": ProjectTask.status,
                "priority": ProjectTask.priority,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, task_id: str, payload: ProjectTaskUpdate):
        task = db.get(ProjectTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project task not found")
        data = payload.model_dump(exclude_unset=True)
        if "project_id" in data:
            project = db.get(Project, data["project_id"])
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
        if "parent_task_id" in data and data["parent_task_id"]:
            parent = db.get(ProjectTask, data["parent_task_id"])
            if not parent:
                raise HTTPException(status_code=404, detail="Parent task not found")
        if "assigned_to_person_id" in data and data["assigned_to_person_id"]:
            _ensure_person(db, str(data["assigned_to_person_id"]))
        if "created_by_person_id" in data and data["created_by_person_id"]:
            _ensure_person(db, str(data["created_by_person_id"]))
        if "ticket_id" in data and data["ticket_id"]:
            ticket = db.get(Ticket, data["ticket_id"])
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found")
        if "work_order_id" in data and data["work_order_id"]:
            work_order = db.get(WorkOrder, data["work_order_id"])
            if not work_order:
                raise HTTPException(status_code=404, detail="Work order not found")
        for key, value in data.items():
            setattr(task, key, value)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def delete(db: Session, task_id: str):
        task = db.get(ProjectTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project task not found")
        task.is_active = False
        db.commit()


class ProjectTaskComments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectTaskCommentCreate):
        task = db.get(ProjectTask, payload.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project task not found")
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        comment = ProjectTaskComment(**payload.model_dump())
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def list(
        db: Session,
        task_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectTaskComment)
        if task_id:
            query = query.filter(ProjectTaskComment.task_id == task_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ProjectTaskComment.created_at},
        )
        return apply_pagination(query, limit, offset).all()


class ProjectComments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectCommentCreate):
        project = db.get(Project, payload.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        comment = ProjectComment(**payload.model_dump())
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def list(
        db: Session,
        project_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectComment)
        if project_id:
            query = query.filter(ProjectComment.project_id == project_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ProjectComment.created_at},
        )
        return apply_pagination(query, limit, offset).all()


projects = Projects()
project_tasks = ProjectTasks()
project_templates = ProjectTemplates()
project_template_tasks = ProjectTemplateTasks()
project_task_comments = ProjectTaskComments()
project_comments = ProjectComments()
