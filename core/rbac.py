from sqlalchemy import select
from sqlalchemy.orm import Session
from models.schema import Permission, Role, RolePermission

# Standard inherent role permissions
DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "Administrator": {"admin.access", "faculty.access", "student.access"},
    "Faculty": {"faculty.access"},
    "Student": {"student.access"},
    "External Examiner": {"faculty.access"},
    "Coordinator": {"admin.access", "faculty.access"},
}


def has_permission(db: Session, user, permission_code: str) -> bool:
    """Return True if the user's role includes the permission_code."""
    if not user:
        return False
    role = getattr(user, "role", None) or (db.get(Role, user.role_id) if getattr(user, "role_id", None) else None)
    if not role:
        return False

    # Administrators have all permissions
    if role.name == "Administrator":
        return True

    # Inherent role permissions
    if permission_code in DEFAULT_ROLE_PERMISSIONS.get(role.name, set()):
        return True

    # Custom database permissions lookup
    perm = db.scalar(select(Permission).where(Permission.code == permission_code))
    if not perm:
        return False
    link = db.scalar(
        select(RolePermission).where(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == perm.id,
        )
    )
    return link is not None


