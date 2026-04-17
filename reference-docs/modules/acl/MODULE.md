# Module: ACL (Access Control List)

> **This module controls who can do what.** Every permission check in the entire application flows through this module.

---

## What This Module Does

- Define roles at institution level (super_admin, admin, member)
- Define permissions as granular capabilities
- Assign roles to users
- Check permissions before any action
- Enforce topic-level access modes (read-write vs read-only)
- Provide middleware that other modules use for authorization

## Dependencies

| Depends On | Why |
|-----------|-----|
| `auth` | Must know who the user is before checking what they can do |
| `users` | Roles are assigned to users |

---

## Three Levels of Access Control

```
┌─────────────────────────────────────────────────┐
│  INSTITUTION LEVEL                              │
│  Who can manage users, create groups, etc.      │
│  Controlled by: user_roles table                │
│  Roles: super_admin, admin, member              │
├─────────────────────────────────────────────────┤
│  GROUP LEVEL                                    │
│  Who can manage this group, add members, etc.   │
│  Controlled by: conversation_members.role       │
│  Roles: owner, admin, member                    │
├─────────────────────────────────────────────────┤
│  TOPIC LEVEL                                    │
│  Who can post in this topic                     │
│  Controlled by: topics.access_mode              │
│  Modes: read_write, read_only                   │
└─────────────────────────────────────────────────┘
```

### Institution-Level Roles

| Role | Description | Assigned By |
|------|------------|-------------|
| `super_admin` | Full control. Can manage admins, settings, everything. | System (1 per institution) |
| `admin` | Can manage users, create groups, moderate messages. | `super_admin` |
| `member` | Can chat, join/leave groups, upload files. | `admin` or `super_admin` |

### Group-Level Roles

| Role | Description | Assigned By |
|------|------------|-------------|
| `owner` | Created the group. Can delete group, manage admins. | System (auto-assigned on create) |
| `admin` | Can add/remove members, manage group settings, manage topics, pin messages. | Group `owner` |
| `member` | Can send messages, view history. | Group `admin` or `owner` |

### Topic-Level Access Modes

| Mode | Who Can Read | Who Can Post |
|------|-------------|-------------|
| `read_write` | All group members | All group members |
| `read_only` | All group members | Only group admins and owner |

Topic access mode is set per-topic by the group admin/owner. There is no per-user topic visibility control (all group members see all topics). The lock icon in the UI indicates `read_only` mode.

---

## Permission Codenames

### Institution Permissions

| Permission | Who Has It (Default) | What It Allows |
|-----------|---------------------|----------------|
| `institution.manage_settings` | super_admin | Change institution settings |
| `institution.manage_admins` | super_admin | Promote/demote admins |
| `institution.manage_users` | admin, super_admin | Invite, deactivate users |
| `institution.view_audit_log` | admin, super_admin | View system audit trail |

### Group Permissions

| Permission | Who Has It (Default) | What It Allows |
|-----------|---------------------|----------------|
| `group.create` | admin, super_admin | Create new groups (simple or topic-enabled) |
| `group.delete` | group owner, super_admin | Delete a group |
| `group.manage_members` | group admin, group owner | Add/remove members |
| `group.manage_settings` | group admin, group owner | Change group name, avatar, mode |
| `group.manage_topics` | group admin, group owner | Create/edit/delete topics, set access modes |
| `group.send_message` | group member, admin, owner | Send messages in group/topic (subject to topic access_mode) |

### Chat Permissions

| Permission | Who Has It (Default) | What It Allows |
|-----------|---------------------|----------------|
| `chat.send_message` | all members | Send DM or group messages |
| `chat.delete_own_message` | all members | Delete own messages |
| `chat.delete_any_message` | admin, super_admin, group admin | Moderate/delete any message |
| `chat.edit_own_message` | all members | Edit own messages |
| `chat.pin_message` | group admin, group owner | Pin/unpin messages |

### Media Permissions

| Permission | Who Has It (Default) | What It Allows |
|-----------|---------------------|----------------|
| `media.upload` | all members | Upload files |
| `media.download` | all members | Download files |

---

## How to Use ACL in Other Modules

### In Route Handlers (Decorator Pattern)

```python
from shared.middleware.acl import require_permission

@router.post("/api/v1/groups")
@require_permission("group.create")
async def create_group(
    request: CreateGroupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ...
```

### Topic Read-Only Check (Used by Chat Module)

```python
from modules.acl.services import ACLService

async def check_can_post_in_conversation(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> bool:
    """Check if user can post in a conversation.

    For DM and simple group conversations: check membership only.
    For topic conversations: also check if topic is read_only
    and whether user has admin/owner role in the group.
    """
    # Check membership first
    member = await get_conversation_member(conversation_id, user_id, db)
    if not member:
        return False

    # Check if this is a topic conversation
    topic = await get_topic_by_conversation_id(conversation_id, db)
    if topic and topic.access_mode == "read_only":
        # Only admins/owners can post in read-only topics
        return member.role in ("admin", "owner")

    return True
```

### Group-Scoped Permission Check

```python
can_manage = await ACLService.user_has_group_role(
    user_id=current_user.id,
    conversation_id=conversation_id,
    required_roles=["owner", "admin"],
    db=db,
)
```

---

## Database Tables

> Full SQL: [SCHEMA.sql](SCHEMA.sql)

| Table | Purpose |
|-------|---------|
| `roles` | Role definitions (name, institution_id, is_system_role) |
| `permissions` | Permission definitions (codename, description) |
| `role_permissions` | Maps roles to permissions (many-to-many) |
| `user_roles` | Maps users to roles (many-to-many) |

---

## API Endpoints

| Method | Path | Purpose | Required Permission |
|--------|------|---------|-------------------|
| `GET` | `/api/v1/acl/roles` | List all roles | `institution.manage_admins` |
| `POST` | `/api/v1/acl/users/{id}/roles` | Assign role to user | `institution.manage_admins` |
| `DELETE` | `/api/v1/acl/users/{id}/roles/{role_id}` | Remove role from user | `institution.manage_admins` |
| `GET` | `/api/v1/acl/users/{id}/permissions` | List user's effective permissions | `institution.manage_admins` or self |

---

## Seed Data

On institution creation, the system seeds:

1. Three default roles: `super_admin`, `admin`, `member`
2. All permissions listed above
3. Default role-permission mappings
4. The creating user is assigned `super_admin`
