# Module: Groups

> **This module manages groups, topics, and group membership.** It supports two group modes: simple (single chat) and topic-enabled (multiple subchannels).

---

## What This Module Does

- Create groups in **simple mode** (one chat stream, like WhatsApp) or **topic-enabled mode** (multiple topic subchannels, like Telegram Topics)
- Manage topics within topic-enabled groups (create, rename, reorder, set access mode)
- Add/remove members from groups
- Manage group roles (owner, admin, member)
- Update group settings (name, description, avatar, mode)
- Upgrade a simple group to topic-enabled mode
- Pin messages within conversations (simple groups or topics)
- List groups the user belongs to

## What This Module Does NOT Do

- Handle message sending/receiving → see [chat/MODULE.md](../chat/MODULE.md)
- Upload/download files → see [media/MODULE.md](../media/MODULE.md)
- Define permission codenames → see [acl/MODULE.md](../acl/MODULE.md)

---

## Dependencies

| Depends On | Why |
|-----------|-----|
| `auth` | Authentication required for all operations |
| `users` | Members are users |
| `acl` | Permission checks for group and topic management |
| `chat` | Creates conversations when groups/topics are created |

---

## Dual Group Mode — Detailed Design

### Simple Mode

```
Group (mode=simple)
  └── conversation_id → Conversation → Messages
```

- Group has a direct `conversation_id` linking to one conversation.
- All members chat in one stream.
- No topics table involved.
- Behaves exactly like a WhatsApp group.

### Topic-Enabled Mode

```
Group (mode=topics, conversation_id=NULL)
  ├── Topic: "General" (is_default=true, access_mode=read_write)
  │     └── conversation_id → Conversation → Messages
  ├── Topic: "Announcements" (access_mode=read_only, locked)
  │     └── conversation_id → Conversation → Messages
  ├── Topic: "Health" (access_mode=read_write)
  │     └── conversation_id → Conversation → Messages
  └── Topic: "Events" (access_mode=read_write)
        └── conversation_id → Conversation → Messages
```

- Group's `conversation_id` is NULL (no direct conversation).
- A "General" topic is auto-created and cannot be deleted.
- Each topic has its own conversation, messages, unread count.
- Each topic has an `access_mode`:
  - `read_write` — all group members can post
  - `read_only` — only group admins/owner can post, members can only read
- Topics have `icon_emoji` for display (the icons visible in the Telegram screenshot).
- Topics have `sort_order` for custom ordering in the list.

### Mode Upgrade (Simple → Topic-Enabled)

When an admin upgrades a simple group to topic-enabled:

1. Create a "General" topic linked to the group's existing conversation.
2. Set `conversation.type` from `group` to `topic`.
3. Set `group.mode` to `topics`.
4. Set `group.conversation_id` to NULL.
5. The existing messages are preserved — they now live under the "General" topic.

**Downgrade (Topic-Enabled → Simple) is NOT supported.** It would require merging or discarding topic conversations. Too destructive and confusing.

---

## Key Business Rules

1. **Group creation creates a conversation**: In simple mode, one conversation is created. In topic-enabled mode, one "General" topic with its conversation is created.

2. **Group membership = conversation membership**: When a user is added to a group, they are added as members of ALL topic conversations (in topic-enabled mode) or the single conversation (in simple mode). When removed from the group, they are removed from all conversations.

3. **Topic membership inherits from group**: There is no per-topic member list. All group members can see all topics. Access control is at the posting level (read_write vs read_only), not the visibility level. (If per-topic visibility is needed in the future, we add a `topic_access` join table — but the client hasn't asked for this.)

4. **Owner cannot be removed**: The group owner cannot be removed. Ownership can be transferred.

5. **Max members**: Configurable per institution (default: 500).

6. **Group names are unique within an institution** (case-insensitive).

7. **Default topic cannot be deleted**: The "General" topic in a topic-enabled group is permanent.

8. **Pinned messages**: Any conversation (simple group or topic) can have pinned messages. Pinning sets `messages.pinned_at` and `messages.pinned_by`. Only admins/owners can pin. Multiple messages can be pinned simultaneously.

---

## API Endpoints

### Group Management

| Method | Path | Purpose | Permission |
|--------|------|---------|-----------|
| `POST` | `/api/v1/groups` | Create group (specify mode) | `group.create` |
| `GET` | `/api/v1/groups` | List user's groups | any authenticated |
| `GET` | `/api/v1/groups/{id}` | Get group details | group member |
| `PATCH` | `/api/v1/groups/{id}` | Update group info | `group.manage_settings` |
| `DELETE` | `/api/v1/groups/{id}` | Delete group | `group.delete` |
| `POST` | `/api/v1/groups/{id}/upgrade-to-topics` | Convert simple → topic-enabled | group owner |

### Member Management

| Method | Path | Purpose | Permission |
|--------|------|---------|-----------|
| `GET` | `/api/v1/groups/{id}/members` | List members | group member |
| `POST` | `/api/v1/groups/{id}/members` | Add member(s) | `group.manage_members` |
| `DELETE` | `/api/v1/groups/{id}/members/{user_id}` | Remove member | `group.manage_members` |
| `PATCH` | `/api/v1/groups/{id}/members/{user_id}/role` | Change member role | group owner |
| `POST` | `/api/v1/groups/{id}/leave` | Leave group | group member (not owner) |

### Topic Management (topic-enabled groups only)

| Method | Path | Purpose | Permission |
|--------|------|---------|-----------|
| `GET` | `/api/v1/groups/{id}/topics` | List topics with unread counts | group member |
| `POST` | `/api/v1/groups/{id}/topics` | Create a new topic | `group.manage_settings` |
| `PATCH` | `/api/v1/groups/{id}/topics/{topic_id}` | Update topic (name, icon, access_mode, sort_order) | `group.manage_settings` |
| `DELETE` | `/api/v1/groups/{id}/topics/{topic_id}` | Delete topic (not default) | `group.manage_settings` |

### Pinned Messages

| Method | Path | Purpose | Permission |
|--------|------|---------|-----------|
| `POST` | `/api/v1/chat/conversations/{id}/pin/{message_id}` | Pin a message | group admin/owner |
| `DELETE` | `/api/v1/chat/conversations/{id}/pin/{message_id}` | Unpin a message | group admin/owner |
| `GET` | `/api/v1/chat/conversations/{id}/pinned` | List pinned messages | conversation member |

---

## Database Tables

> Full SQL: [SCHEMA.sql](SCHEMA.sql)

| Table | Purpose |
|-------|---------|
| `groups` | Group entity with `mode` field (simple or topics) |
| `topics` | Topic subchannels within topic-enabled groups |
| (uses `conversations` from chat) | Each group/topic has a conversation |
| (uses `conversation_members` from chat) | Membership is managed through conversations |

---

## Flutter UI Guidance

### Group List Screen
- Show all groups the user belongs to.
- For each group, show: name, avatar, last message preview, total unread count.
- Total unread for topic-enabled groups = sum of unread across all topics.

### Inside a Simple Group
- Opens directly to the chat screen (conversation view).
- Identical to a DM chat screen.

### Inside a Topic-Enabled Group
- Opens to the **topic list** (like the Telegram screenshot).
- Each topic shows: icon_emoji, name, last message preview (with sender name), unread count, lock icon if read_only.
- Tapping a topic opens its chat screen.
- Back button returns to topic list, not group list.

### Topic List Item Layout
```
[icon_emoji]  Topic Name                    [date]
              Sender: Last message pre...   [unread_badge]  [lock_icon?]
```

---

## File Structure

```
backend/modules/groups/
├── MODULE.md          ← You are here
├── API.md             ← REST endpoint specs
├── SCHEMA.sql         ← Table definitions
├── __init__.py
├── router.py          ← FastAPI router setup
├── routes/
│   ├── __init__.py
│   ├── groups.py           ← Group CRUD endpoints
│   ├── members.py          ← Member management endpoints
│   └── topics.py           ← Topic management endpoints
├── services/
│   ├── __init__.py
│   ├── group_service.py
│   ├── member_service.py
│   └── topic_service.py
├── models/
│   ├── __init__.py
│   ├── db_models.py         ← SQLAlchemy: Group, Topic
│   ├── request_models.py    ← Pydantic request bodies
│   └── response_models.py   ← Pydantic response bodies
└── tests/
    ├── __init__.py
    ├── test_group_service.py
    ├── test_topic_service.py
    ├── test_member_service.py
    ├── test_group_routes.py
    ├── test_topic_routes.py
    └── factories.py
```
