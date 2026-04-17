# ADR-001: Why We Moved Away from Firebase

**Status**: Accepted
**Date**: 2025-01-15
**Decision Maker**: Shreyas

---

## Context

The initial prototype used Firebase (Firestore + Firebase Auth + Firebase Cloud Messaging). As the project scope crystallized into an institution-level messaging platform with ACL, group management, and file sharing, Firebase's limitations became blocking.

## Decision

Migrate to a self-hosted stack: Python (FastAPI) + PostgreSQL + Redis + S3-compatible storage.

Retain Firebase Cloud Messaging (FCM) ONLY for push notification delivery.

## Reasons

### 1. Relational Data Needs Relational Tools

A messaging app has deeply interconnected data: users belong to institutions, users are members of groups, groups have conversations, conversations contain messages, messages have replies, messages have media, users read messages. Firestore's document model forces denormalization, which leads to data inconsistency and complex client-side joins.

PostgreSQL handles this naturally with foreign keys, joins, and referential integrity.

### 2. Query Limitations

Firestore cannot do:
- Full-text search across messages
- Complex ACL queries ("show me all groups where this user is an admin")
- Aggregations ("unread count per conversation")
- Multi-field range queries efficiently

PostgreSQL handles all of these natively.

### 3. Cost Predictability

Firestore charges per read/write. In a chat app, every message send is 1 write + N reads (one per recipient). A group of 100 users where someone sends 1 message = 1 write + 100 reads. At scale, this becomes unpredictable and expensive.

PostgreSQL on a fixed-cost server is predictable.

### 4. Testability

Firebase emulators are fragile and slow. A standard Python + PostgreSQL stack can be tested with pytest, testcontainers, and standard CI/CD pipelines.

### 5. Vendor Independence

If Firebase changes pricing, deprecates features, or experiences outages, we have no recourse. With a self-hosted stack, we own the infrastructure.

## What We Keep from Firebase

- **Firebase Cloud Messaging (FCM)**: The industry standard for mobile push notifications. We use ONLY the push delivery service, nothing else.

## Consequences

- More infrastructure to manage (PostgreSQL, Redis, S3 — but Docker Compose handles this in dev).
- Need to implement auth ourselves (but FastAPI + JWT is well-understood).
- Team needs to learn SQLAlchemy and Alembic (investment that pays off in maintainability).
