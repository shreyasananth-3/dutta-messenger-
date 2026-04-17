# Flutter App Architecture Guide

> **This document is for the Flutter development team.** It defines the app structure, state management approach, and how the Flutter app interacts with the backend.

---

## Architecture Pattern: Feature-First + Clean Architecture

```
lib/
├── main.dart
├── app.dart                          ← MaterialApp, routing, theme
│
├── core/
│   ├── config/
│   │   ├── app_config.dart           ← Base URL, API version, env config
│   │   └── constants.dart            ← App-wide constants
│   ├── network/
│   │   ├── api_client.dart           ← HTTP client (Dio) with interceptors
│   │   ├── websocket_client.dart     ← WebSocket connection manager
│   │   └── interceptors/
│   │       ├── auth_interceptor.dart ← Attach JWT, handle 401 + auto-refresh
│   │       └── logging_interceptor.dart
│   ├── storage/
│   │   ├── secure_storage.dart       ← JWT tokens (flutter_secure_storage)
│   │   └── local_db.dart             ← SQLite for offline cache (drift)
│   ├── errors/
│   │   ├── app_exception.dart        ← Base exception class
│   │   ├── api_error.dart            ← Parse backend error responses
│   │   └── error_handler.dart        ← Global error handler
│   └── utils/
│       ├── date_formatter.dart
│       ├── file_picker_helper.dart
│       └── validators.dart
│
├── features/
│   ├── auth/
│   │   ├── data/
│   │   │   ├── auth_repository.dart
│   │   │   └── auth_api.dart
│   │   ├── domain/
│   │   │   └── auth_models.dart
│   │   └── presentation/
│   │       ├── login_screen.dart
│   │       ├── register_screen.dart
│   │       └── widgets/
│   │
│   ├── conversations/
│   │   ├── data/
│   │   │   ├── conversation_repository.dart
│   │   │   └── conversation_api.dart
│   │   ├── domain/
│   │   │   └── conversation_models.dart
│   │   └── presentation/
│   │       ├── conversation_list_screen.dart
│   │       └── widgets/
│   │           ├── conversation_tile.dart
│   │           └── unread_badge.dart
│   │
│   ├── chat/
│   │   ├── data/
│   │   │   ├── message_repository.dart
│   │   │   ├── message_api.dart
│   │   │   └── message_cache.dart     ← SQLite cache for offline
│   │   ├── domain/
│   │   │   ├── message_models.dart
│   │   │   └── websocket_events.dart
│   │   └── presentation/
│   │       ├── chat_screen.dart
│   │       └── widgets/
│   │           ├── message_bubble.dart
│   │           ├── reply_preview.dart
│   │           ├── typing_indicator.dart
│   │           ├── media_attachment.dart
│   │           └── message_input.dart
│   │
│   ├── groups/
│   │   ├── data/
│   │   ├── domain/
│   │   └── presentation/
│   │       ├── create_group_screen.dart
│   │       ├── group_settings_screen.dart
│   │       ├── topic_list_screen.dart    ← Topics view (like Telegram screenshot)
│   │       └── member_list_screen.dart
│   │
│   ├── media/
│   │   ├── data/
│   │   │   ├── media_repository.dart
│   │   │   └── upload_service.dart    ← Presigned URL upload to S3
│   │   ├── domain/
│   │   │   └── media_models.dart
│   │   └── presentation/
│   │       └── widgets/
│   │           ├── image_viewer.dart
│   │           ├── video_player.dart
│   │           └── file_download.dart
│   │
│   └── profile/
│       ├── data/
│       ├── domain/
│       └── presentation/
│           ├── profile_screen.dart
│           └── settings_screen.dart
│
└── shared/
    ├── widgets/
    │   ├── app_bar.dart
    │   ├── avatar.dart
    │   ├── loading_indicator.dart
    │   └── error_widget.dart
    └── theme/
        ├── app_theme.dart
        ├── colors.dart
        └── typography.dart
```

---

## Group UI Flow (Dual Mode)

### Simple Mode Group
```
Group List → tap group → Chat Screen (single conversation)
```

### Topic-Enabled Group
```
Group List → tap group → Topic List Screen → tap topic → Chat Screen (topic conversation)
```

### Topic List Screen Layout (matches Telegram screenshot)

```
┌─────────────────────────────────────────────┐
│  ← Back    Group Name                    ⋮  │
│            {member_count} members            │
├─────────────────────────────────────────────┤
│  [#]  Ananda Bazaar                    Fri  │
│       Vijju joined the group          (245) │
├─────────────────────────────────────────────┤
│  [📰] Jyotisa News                    Wed  │
│       Manish: Lat and Long is co...   (104) │
├─────────────────────────────────────────────┤
│  [🔒] Lessons                     Jun 19    │
│       Gurudev: Don't miss Sarb...          │
├─────────────────────────────────────────────┤
```

- `icon_emoji` from topic → displayed as the topic icon
- Lock icon (🔒) shown when `access_mode == "read_only"`
- Unread badge from per-topic unread count
- Last message preview with sender name

---

## Optimistic UI Pattern (Messages)

```dart
Future<void> sendMessage(String content, {String? replyToId}) async {
  final clientMessageId = const Uuid().v4();

  // 1. Show immediately in UI
  final optimistic = Message(
    id: clientMessageId,
    content: content,
    senderName: currentUser.displayName,
    status: MessageStatus.sending,
    createdAt: DateTime.now(),
  );
  addMessageToUI(optimistic);

  // 2. Send via WebSocket
  wsClient.send('message.send', {
    'conversation_id': conversationId,
    'content': content,
    'reply_to_message_id': replyToId,
    'client_message_id': clientMessageId,
  });

  // 3. On server confirm (message.sent) → update status to sent
  // 4. On failure → update status to failed, show retry
}
```

---

## Offline Cache Strategy

- **Messages**: Cache last 100 per conversation in SQLite
- **Conversations**: Cache full list
- **Media**: Cache thumbnails only, full files on demand
- **Reconnect**: Fetch missed messages via REST cursor pagination

---

## Key Packages

| Package | Purpose |
|---------|---------|
| `dio` | HTTP client |
| `web_socket_channel` | WebSocket |
| `drift` or `sqflite` | Local SQLite |
| `flutter_secure_storage` | Token storage |
| `image_picker` | Camera/gallery |
| `file_picker` | Document picking |
| `cached_network_image` | Image caching |
| `uuid` | Client-side UUIDs |
