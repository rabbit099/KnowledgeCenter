"""Admin panel routes (thin: forms in, services do the work, templates out).

Every route has a permission dependency; HTMX partial responses use the same routes, so they are
protected the same way. Forms are plain POST forms with a CSRF field and work without JavaScript;
HTMX only refreshes list tables while filtering.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from baza_wiedzy import __version__
from baza_wiedzy.core.admin import ad_groups, roles, users
from baza_wiedzy.core.admin.ad_groups import NeedsAdminConfirmation
from baza_wiedzy.core.admin.errors import AdminError
from baza_wiedzy.core.admin.roles import RoleInUse
from baza_wiedzy.core.audit import viewer
from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.auth.deps import Db, require_any_permission, require_permission
from baza_wiedzy.core.auth.ldap import AdDirectory, normalize_login
from baza_wiedzy.core.auth.routes import client_ip
from baza_wiedzy.core.db.models import SYSTEM_ROLE_ADMIN, AdGroupRoleMap, Role, User
from baza_wiedzy.core.db.models import Session as DbSession
from baza_wiedzy.core.health import run_checks
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.core.registry import ModuleRegistry

router = APIRouter(prefix="/admin")

ADMIN_CODES = ("users.manage", "roles.manage", "audit.view", "settings.manage", "system.view")
UsersManage = Annotated[CurrentUser, Depends(require_permission("users.manage"))]
RolesManage = Annotated[CurrentUser, Depends(require_permission("roles.manage"))]
SystemView = Annotated[CurrentUser, Depends(require_permission("system.view"))]
AuditView = Annotated[CurrentUser, Depends(require_permission("audit.view"))]


# --- helpers --------------------------------------------------------------------------------


def _actor(user: CurrentUser) -> Actor:
    return Actor(user.id, user.username)


def _render(
    request: Request, template: str, context: dict[str, Any], *, active: str, status: int = 200
) -> Response:
    registry: ModuleRegistry = request.app.state.registry
    user: CurrentUser = request.state.user
    nav = [i for i in registry.menu("admin") if i.permission is None or user.can(i.permission)]
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        template,
        {"admin_nav": nav, "active": active, **context},
        status_code=status,
    )


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404) from None


def _uuids(values: list[str]) -> set[uuid.UUID]:
    return {_uuid(v) for v in values if v}


def _get[T](db: Db, model: type[T], object_id: str) -> T:
    found = db.get(model, _uuid(object_id))
    if found is None:
        raise HTTPException(status_code=404)
    return found


def _redirect(path: str, done: str) -> Response:
    return RedirectResponse(f"{path}?done={done}", status_code=303)


def _page(value: str | None) -> int:
    try:
        return max(1, int(value or 1))
    except ValueError:
        return 1


# --- shell ----------------------------------------------------------------------------------


@router.get("", include_in_schema=False)
def admin_home(
    request: Request, user: Annotated[CurrentUser, Depends(require_any_permission(*ADMIN_CODES))]
) -> Response:
    registry: ModuleRegistry = request.app.state.registry
    for item in registry.menu("admin"):
        if item.permission is None or user.can(item.permission):
            return RedirectResponse(item.path, status_code=303)
    raise HTTPException(status_code=403)


# --- users ----------------------------------------------------------------------------------


@router.get("/users", include_in_schema=False)
def users_list(
    request: Request,
    db: Db,
    user: UsersManage,
    q: str = "",
    source: str = "",
    active: str = "",
    page: str = "1",
) -> Response:
    result = users.list_users(db, q=q.strip(), source=source, active=active, page=_page(page))
    context = {
        "result": result,
        "roles_of": users.roles_of(db, [u.id for u in result.items]),
        "filters": {"q": q, "source": source, "active": active},
    }
    template = "admin/_users_table.html" if _is_htmx(request) else "admin/users_list.html"
    return _render(request, template, context, active="/admin/users")


def _user_form(
    request: Request,
    db: Db,
    *,
    error: AdminError | None = None,
    values: dict[str, Any] | None = None,
) -> Response:
    context = {"all_roles": roles.all_roles(db), "error": error, "values": values or {}}
    return _render(
        request, "admin/user_new.html", context, active="/admin/users", status=400 if error else 200
    )


@router.get("/users/new", include_in_schema=False)
def user_new(request: Request, db: Db, user: UsersManage) -> Response:
    return _user_form(request, db)


def _planned_name(request: Request, raw: str) -> str:
    directory: AdDirectory | None = request.app.state.authenticator.directory
    if directory is not None:
        return directory.normalize(raw)
    settings = request.app.state.settings
    if settings.ldap_login_attribute == "userPrincipalName" and not settings.ldap_upn_suffix:
        return raw.strip().lower()
    return normalize_login(raw, settings.ldap_login_attribute, settings.ldap_upn_suffix)


@router.post("/users", include_in_schema=False)
def user_create(
    request: Request,
    db: Db,
    user: UsersManage,
    username: Annotated[str, Form(max_length=256)] = "",
    display_name: Annotated[str, Form(max_length=256)] = "",
    role_ids: Annotated[list[str] | None, Form()] = None,
) -> Response:
    values = {"username": username, "display_name": display_name, "role_ids": role_ids or []}
    try:
        created = users.create_planned_ad_user(
            db,
            _actor(user),
            username=_planned_name(request, username) if username.strip() else "",
            display_name=display_name.strip(),
            role_ids=_uuids(role_ids or []),
            ip=client_ip(request),
        )
        db.commit()
    except AdminError as exc:
        db.rollback()
        return _user_form(request, db, error=exc, values=values)
    return _redirect(f"/admin/users/{created.id}", "created")


def _user_detail(
    request: Request, db: Db, target: User, *, error: AdminError | None = None
) -> Response:
    held = users.roles_of(db, [target.id]).get(target.id, [])
    context = {
        "target": target,
        "manual_role_ids": {role.id for role, source in held if source == "manual"},
        "ad_roles": [role for role, source in held if source == "ad"],
        "all_roles": roles.all_roles(db),
        "sessions": users.session_count(db, target.id),
        "error": error,
        "done": request.query_params.get("done"),
    }
    return _render(
        request,
        "admin/user_detail.html",
        context,
        active="/admin/users",
        status=400 if error else 200,
    )


@router.get("/users/{user_id}", include_in_schema=False)
def user_detail(request: Request, db: Db, user: UsersManage, user_id: str) -> Response:
    return _user_detail(request, db, _get(db, User, user_id))


@router.post("/users/{user_id}/roles", include_in_schema=False)
def user_roles(
    request: Request,
    db: Db,
    user: UsersManage,
    user_id: str,
    role_ids: Annotated[list[str] | None, Form()] = None,
) -> Response:
    target = _get(db, User, user_id)
    try:
        users.set_manual_roles(
            db, _actor(user), target, _uuids(role_ids or []), ip=client_ip(request)
        )
        db.commit()
    except AdminError as exc:
        db.rollback()
        return _user_detail(request, db, target, error=exc)
    return _redirect(f"/admin/users/{target.id}", "saved")


@router.post("/users/{user_id}/active", include_in_schema=False)
def user_active(
    request: Request,
    db: Db,
    user: UsersManage,
    user_id: str,
    active: Annotated[str, Form()] = "",
) -> Response:
    target = _get(db, User, user_id)
    try:
        users.set_active(db, _actor(user), target, active == "yes", ip=client_ip(request))
        db.commit()
    except AdminError as exc:
        db.rollback()
        return _user_detail(request, db, target, error=exc)
    return _redirect(f"/admin/users/{target.id}", "activated" if active == "yes" else "deactivated")


@router.post("/users/{user_id}/sessions", include_in_schema=False)
def user_sessions(request: Request, db: Db, user: UsersManage, user_id: str) -> Response:
    target = _get(db, User, user_id)
    users.revoke_user_sessions(db, _actor(user), target, ip=client_ip(request))
    db.commit()
    return _redirect(f"/admin/users/{target.id}", "sessions")


# --- roles ----------------------------------------------------------------------------------


def _catalog(request: Request) -> list[tuple[str, list[Any]]]:
    """Permissions grouped by the module that declares them (for the matrix)."""
    registry: ModuleRegistry = request.app.state.registry
    return [(m.name, list(m.permissions)) for m in registry.modules if m.permissions]


@router.get("/roles", include_in_schema=False)
def roles_list(
    request: Request, db: Db, user: RolesManage, q: str = "", page: str = "1"
) -> Response:
    result = roles.list_roles(db, q=q.strip(), page=_page(page))
    context = {
        "result": result,
        "counts": roles.user_counts(db, [r.id for r in result.items]),
        "filters": {"q": q},
        "done": request.query_params.get("done"),
    }
    template = "admin/_roles_table.html" if _is_htmx(request) else "admin/roles_list.html"
    return _render(request, template, context, active="/admin/roles")


def _role_form(
    request: Request,
    db: Db,
    role: Role | None,
    *,
    error: AdminError | None = None,
    values: dict[str, Any] | None = None,
) -> Response:
    codes = roles.stored_codes(db, role) if role else set()
    context = {
        "role": role,
        "is_admin_role": role is not None and role.system_key == SYSTEM_ROLE_ADMIN,
        "catalog": _catalog(request),
        "codes": set(values["codes"]) if values else codes,
        "values": values
        or {
            "name": role.name if role else "",
            "description": (role.description or "") if role else "",
        },
        "error": error,
        "done": request.query_params.get("done"),
    }
    status = 400 if error else 200
    return _render(request, "admin/role_form.html", context, active="/admin/roles", status=status)


@router.get("/roles/new", include_in_schema=False)
def role_new(request: Request, db: Db, user: RolesManage) -> Response:
    return _role_form(request, db, None)


@router.post("/roles", include_in_schema=False)
def role_create(
    request: Request,
    db: Db,
    user: RolesManage,
    name: Annotated[str, Form(max_length=256)] = "",
    description: Annotated[str, Form(max_length=1000)] = "",
    codes: Annotated[list[str] | None, Form()] = None,
) -> Response:
    values = {"name": name, "description": description, "codes": codes or []}
    try:
        role = roles.create_role(
            db,
            _actor(user),
            name=name,
            description=description,
            codes=set(codes or []),
            catalog=request.app.state.permission_catalog,
            ip=client_ip(request),
        )
        db.commit()
    except AdminError as exc:
        db.rollback()
        return _role_form(request, db, None, error=exc, values=values)
    return _redirect(f"/admin/roles/{role.id}", "created")


@router.get("/roles/{role_id}", include_in_schema=False)
def role_edit(request: Request, db: Db, user: RolesManage, role_id: str) -> Response:
    return _role_form(request, db, _get(db, Role, role_id))


@router.post("/roles/{role_id}", include_in_schema=False)
def role_update(
    request: Request,
    db: Db,
    user: RolesManage,
    role_id: str,
    name: Annotated[str, Form(max_length=256)] = "",
    description: Annotated[str, Form(max_length=1000)] = "",
    codes: Annotated[list[str] | None, Form()] = None,
) -> Response:
    role = _get(db, Role, role_id)
    values = {"name": name, "description": description, "codes": codes or []}
    try:
        roles.update_role(
            db,
            _actor(user),
            role,
            name=name,
            description=description,
            codes=set(codes or []),
            catalog=request.app.state.permission_catalog,
            ip=client_ip(request),
        )
        db.commit()
    except AdminError as exc:
        db.rollback()
        return _role_form(request, db, role, error=exc, values=values)
    return _redirect(f"/admin/roles/{role.id}", "saved")


@router.get("/roles/{role_id}/delete", include_in_schema=False)
def role_delete_confirm(request: Request, db: Db, user: RolesManage, role_id: str) -> Response:
    role = _get(db, Role, role_id)
    context = {"role": role, "affected": roles.users_with_role(db, role), "error": None}
    return _render(request, "admin/role_delete.html", context, active="/admin/roles")


@router.post("/roles/{role_id}/delete", include_in_schema=False)
def role_delete(
    request: Request,
    db: Db,
    user: RolesManage,
    role_id: str,
    confirm: Annotated[str, Form()] = "",
) -> Response:
    role = _get(db, Role, role_id)
    try:
        roles.delete_role(db, _actor(user), role, confirmed=confirm == "yes", ip=client_ip(request))
        db.commit()
    except AdminError as exc:
        db.rollback()
        affected = exc.users if isinstance(exc, RoleInUse) else roles.users_with_role(db, role)
        context = {"role": role, "affected": affected, "error": exc}
        return _render(
            request, "admin/role_delete.html", context, active="/admin/roles", status=400
        )
    return _redirect("/admin/roles", "deleted")


# --- AD group mapping -----------------------------------------------------------------------


def _groups_page(
    request: Request,
    db: Db,
    *,
    q: str = "",
    page: int = 1,
    error: AdminError | None = None,
    values: dict[str, Any] | None = None,
    partial: bool = False,
) -> Response:
    result = ad_groups.list_mappings(db, q=q, page=page)
    role_names = {r.id: r for r in roles.all_roles(db)}
    context = {
        "result": result,
        "roles_by_id": role_names,
        "all_roles": roles.all_roles(db),
        "filters": {"q": q},
        "error": error,
        "needs_admin_confirmation": isinstance(error, NeedsAdminConfirmation),
        "values": values or {"verify": "yes"},
        "ad_enabled": request.app.state.authenticator.directory is not None,
        "done": request.query_params.get("done"),
    }
    template = "admin/_ad_groups_table.html" if partial else "admin/ad_groups.html"
    return _render(
        request, template, context, active="/admin/ad-groups", status=400 if error else 200
    )


@router.get("/ad-groups", include_in_schema=False)
def ad_groups_list(
    request: Request, db: Db, user: RolesManage, q: str = "", page: str = "1"
) -> Response:
    return _groups_page(request, db, q=q.strip(), page=_page(page), partial=_is_htmx(request))


@router.post("/ad-groups", include_in_schema=False)
def ad_group_create(
    request: Request,
    db: Db,
    user: RolesManage,
    dn: Annotated[str, Form(max_length=2048)] = "",
    role_id: Annotated[str, Form()] = "",
    confirm_admin: Annotated[str, Form()] = "",
    verify: Annotated[str, Form()] = "",
) -> Response:
    values = {"dn": dn, "role_id": role_id, "verify": verify}
    try:
        _, verification = ad_groups.create_mapping(
            db,
            _actor(user),
            dn=dn,
            role_id=_uuid(role_id) if role_id else uuid.UUID(int=0),
            confirm_admin=confirm_admin == "yes",
            verify=verify == "yes",
            directory=request.app.state.authenticator.directory,
            ip=client_ip(request),
        )
        db.commit()
    except AdminError as exc:
        db.rollback()
        return _groups_page(request, db, error=exc, values=values)
    return _redirect("/admin/ad-groups", f"mapped_{verification}")


@router.post("/ad-groups/{mapping_id}/delete", include_in_schema=False)
def ad_group_delete(request: Request, db: Db, user: RolesManage, mapping_id: str) -> Response:
    mapping = _get(db, AdGroupRoleMap, mapping_id)
    ad_groups.delete_mapping(db, _actor(user), mapping, ip=client_ip(request))
    db.commit()
    return _redirect("/admin/ad-groups", "deleted")


# --- system ---------------------------------------------------------------------------------


@router.get("/system", include_in_schema=False)
def system_view(request: Request, db: Db, user: SystemView) -> Response:
    ok, results = run_checks(request.app.state.ready_checks)
    settings = request.app.state.settings
    registry: ModuleRegistry = request.app.state.registry
    context = {
        "version": __version__,
        "modules": [(m.name, m.version) for m in registry.modules],
        "ok": ok,
        "results": results,
        "ad": {
            "enabled": settings.ldap_enabled,
            "uri": settings.ldap_uri,
            "base_dn": settings.ldap_base_dn,
            "login_attribute": settings.ldap_login_attribute,
            "nested_groups": settings.ldap_nested_groups,
        },
        "counts": {
            "users": db.scalar(select(func.count()).select_from(User)) or 0,
            "active_users": db.scalar(
                select(func.count()).select_from(User).where(User.is_active.is_(True))
            )
            or 0,
            "sessions": db.scalar(select(func.count()).select_from(DbSession)) or 0,
        },
    }
    return _render(request, "admin/system.html", context, active="/admin/system")


# --- audit log ------------------------------------------------------------------------------


@router.get("/audit", include_in_schema=False)
def audit_list(
    request: Request,
    db: Db,
    user: AuditView,
    date_from: str = "",
    date_to: str = "",
    actor: str = "",
    action: str = "",
    entity: str = "",
    page: str = "1",
) -> Response:
    filters = viewer.AuditFilters.parse(
        {
            "date_from": date_from,
            "date_to": date_to,
            "actor": actor,
            "action": action,
            "entity": entity,
        }
    )
    result = viewer.list_entries(db, filters, _page(page))
    context = {
        "result": result,
        "filters": filters.as_query(),
        "actions": viewer.known_actions(db),
        "pairs": {e.id: viewer.detail_pairs(e.details) for e in result.items},
        "max_export": request.app.state.settings.audit_export_max_rows,
    }
    template = "admin/_audit_table.html" if _is_htmx(request) else "admin/audit.html"
    return _render(request, template, context, active="/admin/audit")


@router.post("/audit/export", include_in_schema=False)
def audit_export(
    request: Request,
    db: Db,
    user: AuditView,
    date_from: Annotated[str, Form()] = "",
    date_to: Annotated[str, Form()] = "",
    actor: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "",
    entity: Annotated[str, Form()] = "",
) -> Response:
    filters = viewer.AuditFilters.parse(
        {
            "date_from": date_from,
            "date_to": date_to,
            "actor": actor,
            "action": action,
            "entity": entity,
        }
    )
    limit = request.app.state.settings.audit_export_max_rows
    content, rows, truncated = viewer.export_csv(db, filters, limit)
    # Exporting personal data is itself recorded (who, which filters, how many rows).
    record(
        db,
        "audit.export",
        _actor(user),
        ip=client_ip(request),
        details={"filters": filters.as_query(), "rows": rows, "truncated": truncated},
    )
    db.commit()
    stamp = datetime.now(viewer.LOCAL_TZ).strftime("%Y%m%d-%H%M")
    return Response(
        content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="audit-{stamp}.csv"',
            "X-Export-Rows": str(rows),
            "X-Export-Truncated": "true" if truncated else "false",
        },
    )
