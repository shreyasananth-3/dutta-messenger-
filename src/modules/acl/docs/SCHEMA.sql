-- =============================================================================
-- Module: ACL (Access Control List)
-- Tables: roles, permissions, role_permissions, user_roles
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Table: roles
-- Purpose: Define roles within an institution.
-- System roles (super_admin, admin, member) are seeded on institution creation.
-- Custom roles can be created by super_admin.
-- -----------------------------------------------------------------------------
CREATE TABLE roles (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    institution_id  UUID NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    name            VARCHAR(50) NOT NULL,
    description     VARCHAR(255),
    is_system_role  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (institution_id, name)
);

-- -----------------------------------------------------------------------------
-- Table: permissions
-- Purpose: Granular capabilities that can be assigned to roles.
-- These are seeded by the system and referenced by codename in code.
-- -----------------------------------------------------------------------------
CREATE TABLE permissions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    codename    VARCHAR(100) NOT NULL UNIQUE,
    description VARCHAR(255) NOT NULL
);

-- Seed permissions
INSERT INTO permissions (id, codename, description) VALUES
    (uuid_generate_v4(), 'institution.manage_settings', 'Change institution-level settings'),
    (uuid_generate_v4(), 'institution.manage_admins', 'Promote or demote admins'),
    (uuid_generate_v4(), 'institution.manage_users', 'Invite, deactivate users'),
    (uuid_generate_v4(), 'institution.view_audit_log', 'View system audit trail'),
    (uuid_generate_v4(), 'group.create', 'Create new groups'),
    (uuid_generate_v4(), 'group.delete', 'Delete groups'),
    (uuid_generate_v4(), 'group.manage_members', 'Add or remove group members'),
    (uuid_generate_v4(), 'group.manage_settings', 'Change group name, avatar, settings'),
    (uuid_generate_v4(), 'group.send_message', 'Send messages in groups'),
    (uuid_generate_v4(), 'chat.send_message', 'Send DM or group messages'),
    (uuid_generate_v4(), 'chat.delete_own_message', 'Delete own messages'),
    (uuid_generate_v4(), 'chat.delete_any_message', 'Delete any message (moderation)'),
    (uuid_generate_v4(), 'chat.edit_own_message', 'Edit own messages'),
    (uuid_generate_v4(), 'media.upload', 'Upload files'),
    (uuid_generate_v4(), 'media.download', 'Download files');

-- -----------------------------------------------------------------------------
-- Table: role_permissions
-- Purpose: Many-to-many mapping between roles and permissions.
-- -----------------------------------------------------------------------------
CREATE TABLE role_permissions (
    role_id         UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id   UUID NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- Supports: Looking up all permissions for a role
CREATE INDEX idx_role_permissions_role ON role_permissions (role_id);

-- -----------------------------------------------------------------------------
-- Table: user_roles
-- Purpose: Maps users to their assigned roles. A user can have multiple roles.
-- -----------------------------------------------------------------------------
CREATE TABLE user_roles (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id     UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    assigned_by UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, role_id)
);

-- Supports: Looking up all roles for a user
CREATE INDEX idx_user_roles_user ON user_roles (user_id);

-- Supports: Finding all users with a specific role
CREATE INDEX idx_user_roles_role ON user_roles (role_id);
