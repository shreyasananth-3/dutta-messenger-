# Flutter WebSocket Integration — Execution Spec

> **Who this is for:** the Flutter team, or the AI coding assistant working on the Flutter repo. Read top-to-bottom. Do not skip sections. Do not improvise. When you finish, every box under **§7 Acceptance Gates** must pass — if any fails, the task is not done.

---

## 1. Problem statement

The Flutter chat screen currently has a "Refresh" button that calls `GET /api/v1/chat/conversations/{id}/messages` on tap. This is wrong. Chat is supposed to be real-time. The backend has a working WebSocket endpoint that pushes every new message to every connected member within ~2 ms. We must replace the refresh button with a persistent WebSocket connection.

**In scope:**
- Add a `ChatService` singleton that owns one WebSocket connection per logged-in user.
- Replace the chat screen's refresh-on-tap list with a `StreamBuilder` that auto-updates.
- Remove all polling / `Timer.periodic` / "refresh" code.
- Auto-reconnect on disconnect.

**Out of scope (do not touch):**
- Auth / login flow
- REST endpoints for login, groups, membership
- Message send endpoint — the existing REST `POST /messages` call stays as-is (server broadcasts it over WS automatically)

---

## 2. Backend protocol (authoritative — do not deviate)

### Endpoint
```
wss://<backend-host>/ws/chat
```

The URL has **no query parameters**. The JWT token is sent in the first frame, not the URL.

### Frame sequence

| Step | Direction | Frame |
|------|-----------|-------|
| 1 | client → server | `{"type":"auth","token":"<jwt_access_token>"}` — send immediately after socket opens |
| 2 | server → client | `{"type":"connection.established","user_id":"<uuid>"}` — **wait for this before sending anything else** |
| 3 | client → server | `{"type":"subscribe","conversation_id":"<uuid>"}` — one frame per conversation the user is viewing |
| 4 | server → client | `{"type":"subscribed","conversation_id":"<uuid>"}` — ack |
| 5 | server → client | `{"type":"message.new","message":{…}}` — pushed whenever anyone in that conversation sends a message (including the caller's own sends via REST) |

### Sending a message — two options, pick one

**Option A (recommended):** keep the existing REST call
```
POST /api/v1/chat/conversations/{cid}/messages
Body: {"content":"Hi"}
```
The backend persists it and broadcasts a `message.new` frame over WebSocket to every subscriber, including the sender. The sender sees their own message arrive via WS exactly like a remote message — same code path, no split state.

**Option B:** send via WS frame
```json
{"type":"message.send","conversation_id":"<uuid>","content":"Hi"}
```
Same result. Use if you want to avoid HTTP entirely.

### `message.new` payload shape
```json
{
  "type": "message.new",
  "message": {
    "id": "uuid",
    "conversation_id": "uuid",
    "sender_id": "uuid",
    "content": "string",
    "reply_to_message_id": "uuid | null",
    "created_at": "ISO-8601 string",
    "updated_at": "ISO-8601 string"
  }
}
```

### Errors
If authentication fails or the frame is malformed, the server closes the socket with code `4001`. Treat as permanent — log the user out.

---

## 3. Prerequisites

### 3.1 Backend access

The Flutter dev needs a backend instance reachable from their emulator. **Do not hardcode any URL in the Dart code.** Read it from `--dart-define` or `.env`.

Three options, in order of preference:

| Option | When to use | Base URL shape |
|--------|-------------|----------------|
| **Their own ngrok** | They want to run the backend locally on their own laptop | `https://<subdomain>.ngrok-free.dev` |
| **Shared team ngrok** | Someone else is running the backend and shared a URL | whatever URL the team lead sends |
| **Android emulator localhost** | Backend runs on the same machine as the emulator | `http://10.0.2.2:8000` (Android) / `http://localhost:8000` (iOS) |

#### Setting up their own ngrok — 3 commands

```bash
# 1. install
brew install ngrok                                    # macOS
# or: sudo snap install ngrok                         # Linux
# or download from ngrok.com/download                 # Windows

# 2. authenticate (one time) — signup at ngrok.com for a free token
ngrok config add-authtoken <TOKEN_FROM_NGROK_DASHBOARD>

# 3. start tunnel — backend must be running on port 8000 first
ngrok http 8000
```

Copy the `https://...-ngrok-free.dev` line from the terminal. That's the `API_BASE`.

### 3.2 Flutter packages

Add to `pubspec.yaml`:
```yaml
dependencies:
  web_socket_channel: ^2.4.0
  flutter_dotenv: ^5.1.0   # only if not already there
```

Run `flutter pub get`.

### 3.3 Environment file

Create `/.env` in the Flutter repo root (add it to `.gitignore`):
```
API_BASE=https://earthquaking-charlena-phonily.ngrok-free.dev
```

> Replace the example URL above with the dev's **own** ngrok URL from §3.1. The example URL is not persistent — it will be dead by the time you read this.

Add to `pubspec.yaml` assets:
```yaml
flutter:
  assets:
    - .env
```

Load it in `main()`:
```dart
import 'package:flutter_dotenv/flutter_dotenv.dart';
Future<void> main() async {
  await dotenv.load();
  runApp(const MyApp());
}
```

---

## 4. The drop-in `chat_service.dart`

Create this file at `lib/services/chat_service.dart`. This is the **complete, final** implementation — do not add features, do not simplify.

```dart
import 'dart:async';
import 'dart:convert';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

/// Real-time chat connection. One instance per logged-in user.
///
/// Usage:
///   ChatService.instance.connect(jwtAccessToken);     // on login
///   ChatService.instance.subscribe(conversationId);   // when opening a chat screen
///   ChatService.instance.messages(conversationId)     // returns Stream<Map>
///   ChatService.instance.disconnect();                // on logout
class ChatService {
  ChatService._();
  static final ChatService instance = ChatService._();

  WebSocketChannel? _channel;
  String? _token;
  final Set<String> _subscribed = <String>{};
  final Map<String, StreamController<Map<String, dynamic>>> _streams = {};
  bool _authed = false;
  bool _disposed = false;
  Timer? _reconnectTimer;

  /// Returns a broadcast stream of `message` objects for the given conversation.
  /// Safe to call before `connect` — the stream starts emitting once the socket
  /// is connected and subscribed.
  Stream<Map<String, dynamic>> messages(String conversationId) {
    final controller = _streams.putIfAbsent(
      conversationId,
      () => StreamController<Map<String, dynamic>>.broadcast(),
    );
    return controller.stream;
  }

  /// Open the WebSocket. Idempotent — calling twice with the same token is a no-op.
  void connect(String jwtAccessToken) {
    if (_token == jwtAccessToken && _channel != null) return;
    _token = jwtAccessToken;
    _disposed = false;
    _openSocket();
  }

  /// Subscribe to real-time messages for a conversation. Safe to call multiple
  /// times — duplicate subscribes are deduped server-side.
  void subscribe(String conversationId) {
    _subscribed.add(conversationId);
    if (_authed) {
      _send({'type': 'subscribe', 'conversation_id': conversationId});
    }
  }

  /// Close the socket permanently. Call on logout.
  void disconnect() {
    _disposed = true;
    _reconnectTimer?.cancel();
    _channel?.sink.close(ws_status.normalClosure);
    _channel = null;
    _authed = false;
    _subscribed.clear();
    for (final c in _streams.values) {
      c.close();
    }
    _streams.clear();
    _token = null;
  }

  // ---- internals -------------------------------------------------------

  void _openSocket() {
    final apiBase = dotenv.env['API_BASE'] ?? '';
    if (apiBase.isEmpty) {
      throw StateError('API_BASE missing from .env');
    }
    final wsUrl = apiBase
            .replaceFirst('https://', 'wss://')
            .replaceFirst('http://', 'ws://') +
        '/ws/chat';

    try {
      _channel = WebSocketChannel.connect(Uri.parse(wsUrl));
    } catch (_) {
      _scheduleReconnect();
      return;
    }

    _authed = false;
    _send({'type': 'auth', 'token': _token});

    _channel!.stream.listen(
      _onFrame,
      onError: (_) => _scheduleReconnect(),
      onDone: _scheduleReconnect,
      cancelOnError: true,
    );
  }

  void _onFrame(dynamic raw) {
    final Map<String, dynamic> frame;
    try {
      frame = json.decode(raw as String) as Map<String, dynamic>;
    } catch (_) {
      return;
    }

    switch (frame['type']) {
      case 'connection.established':
        _authed = true;
        // Re-subscribe to everything (covers first-connect + reconnect)
        for (final cid in _subscribed) {
          _send({'type': 'subscribe', 'conversation_id': cid});
        }
        break;
      case 'message.new':
        final msg = frame['message'] as Map<String, dynamic>?;
        if (msg == null) return;
        final cid = msg['conversation_id'] as String?;
        if (cid == null) return;
        final controller = _streams[cid];
        controller?.add(msg);
        break;
      default:
        // ignore subscribed, pong, etc.
        break;
    }
  }

  void _send(Map<String, dynamic> frame) {
    final ch = _channel;
    if (ch == null) return;
    try {
      ch.sink.add(json.encode(frame));
    } catch (_) {
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    if (_disposed) return;
    _authed = false;
    _channel = null;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 2), _openSocket);
  }
}
```

**Do not** add logging frameworks, state-management bindings, generics, message history cache, typing indicators, or any other feature. Ship this exact file.

---

## 5. Wiring into the existing app

### 5.1 On login success

Wherever the login success handler is (likely `login_screen.dart` or an auth bloc), after receiving the `access_token` from `/api/v1/auth/login`:

```dart
ChatService.instance.connect(response.accessToken);
```

### 5.2 On logout

```dart
ChatService.instance.disconnect();
```

### 5.3 In `chat_screen.dart`

**Delete** (with surgical precision — do not delete unrelated code):
- The `Refresh` `IconButton` / `ElevatedButton` in the app bar or body
- Any `Timer.periodic` that polls messages
- Any method named `_refresh()`, `_refreshMessages()`, `_pollMessages()` or similar
- The `onPressed` handler that fetches messages

**Keep**:
- The **one-time** `GET /api/v1/chat/conversations/{cid}/messages?limit=50` in `initState` — this is history load, not polling. It runs once when the screen opens.
- The `POST /api/v1/chat/conversations/{cid}/messages` call when the user taps Send.

**Add** in `initState` right after the history load:
```dart
ChatService.instance.subscribe(widget.conversationId);
_messageSubscription = ChatService.instance
    .messages(widget.conversationId)
    .listen((msg) {
  setState(() {
    _messages.add(msg);                         // or whatever your message list is
    // optional: deduplicate by msg['id']       // server may echo your own sends
  });
  // optional: auto-scroll to bottom
  _scrollController.animateTo(
    _scrollController.position.maxScrollExtent,
    duration: const Duration(milliseconds: 200),
    curve: Curves.easeOut,
  );
});
```

**Add** in `dispose()`:
```dart
_messageSubscription?.cancel();
```

(Do **not** call `ChatService.instance.disconnect()` here — the socket must survive screen changes. Only disconnect on logout.)

### 5.4 Deduplication note

When the sender posts via REST, the server echoes it back over WS. Without dedup, the sender sees their own message twice (once from optimistic local append, once from WS). Easiest fix: don't optimistically append — wait for the WS echo. Round-trip is ~2 ms, the user won't notice. If you already optimistically append, dedupe by `message.id`:

```dart
if (_messages.any((m) => m['id'] == msg['id'])) return;
_messages.add(msg);
```

---

## 6. Smoke testing — use before declaring done

### 6.1 Fake-peer script (fastest feedback loop)

The backend repo has `scripts/fake_peer.py` (see that file for usage). It logs in as a second user and posts a message every N seconds. Run it in one terminal, run Flutter in another. Every 5 s a new message should appear on the Flutter screen without any tap, without any timer firing in the client.

```bash
# in the BACKEND repo
python scripts/fake_peer.py \
  --base "$API_BASE" \
  --conversation-id <cid> \
  --email user1@demo.school \
  --password DemoPass123! \
  --interval 5
```

If messages don't appear on the Flutter screen: the WebSocket isn't connected. Check §8.

### 6.2 Two-emulator smoke

1. Launch two emulators. Log in as two different users in the same group.
2. On emulator A, type "ping" and send.
3. On emulator B, it must appear within 1 second — **no tap, no button, no refresh, no pull-to-refresh**.
4. On emulator B, type "pong" and send.
5. On emulator A, it must appear within 1 second.
6. On emulator A, toggle airplane mode on for 10 seconds, then off. Send a new message from B. It should arrive on A within ~3 seconds of airplane mode off (reconnect window).

### 6.3 Server log inspection

Run this on the backend machine while Flutter is open:
```bash
grep "ws/chat" <path-to-server-log>
```
You should see a WebSocket connection logged when the Flutter app logs in. If this returns **zero hits**, Flutter is not using WebSocket at all and the integration has failed silently. Go back to §4.

### 6.4 Integration test (optional but recommended)

If the Flutter repo has `integration_test/` set up, add `integration_test/websocket_chat_test.dart`:

```dart
testWidgets('receives message without refresh', (tester) async {
  app.main();
  await tester.pumpAndSettle();
  // login as user A, open conversation X
  // ...
  // trigger an external message send (via HTTP from the test itself to /messages as user B)
  await http.post(Uri.parse('$base/api/v1/chat/conversations/$cid/messages'),
      headers: {'Authorization': 'Bearer $bToken', 'Content-Type': 'application/json'},
      body: jsonEncode({'content': 'auto-test ${DateTime.now()}'}));
  // do NOT tap refresh, do NOT pump for 10s — just pumpAndSettle briefly
  await tester.pump(const Duration(seconds: 2));
  expect(find.textContaining('auto-test'), findsOneWidget);
});
```

This test fails hard if polling is re-introduced.

---

## 7. Acceptance Gates

All four must pass. No partial credit.

- [ ] **Gate 1 — no UI timer.** `grep -R "Timer.periodic" lib/` finds zero matches in any chat-related file.
- [ ] **Gate 2 — no refresh button.** Every `IconButton` / `ElevatedButton` with `Icons.refresh` in chat screens is deleted.
- [ ] **Gate 3 — WS connection visible in server log.** `grep "ws/chat" <server.log>` shows a 101 Switching Protocols entry within 2 s of the user logging in.
- [ ] **Gate 4 — sub-second delivery.** Two-emulator smoke (§6.2): messages arrive in under 1 second with no user action on the receiving side.

If any gate fails, the task is not done. Report what's failing and why before asking for review.

---

## 8. Failure diagnostics

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| No connection in server log | `API_BASE` wrong or `.env` not loaded | Verify `dotenv.load()` runs before `runApp()`. Print `dotenv.env['API_BASE']` in `main`. |
| `Connection refused` in Flutter console | Backend not running, or ngrok tunnel died | Check backend is on :8000 and ngrok shows `online` status |
| Socket opens then closes with code 4001 | Token rejected | Token expired or wrong format. Re-login, grab a fresh `access_token`. |
| Socket opens, `message.new` never arrives | Never sent `subscribe` frame | Check `_authed` flag flips to true. Verify `subscribe` is called after `connection.established`. |
| Sender sees their own message twice | No dedup | See §5.4 |
| Works on iOS sim, fails on Android emulator with localhost | Android emulator can't reach `localhost` | Use `10.0.2.2:8000` or ngrok |
| WS disconnects every 2 hours | ngrok free tier cap | Normal. Auto-reconnect in `chat_service.dart` handles it. |

---

## 9. Commit message

When done, commit with:

```
feat(chat): replace manual refresh with persistent WebSocket

- Add ChatService singleton (lib/services/chat_service.dart)
- Wire subscribe/stream into chat_screen.dart
- Remove refresh button, polling timers
- Auto-reconnect on disconnect

Verified:
- ws/chat shows 101 in backend log on login
- Two-emulator: <500ms delivery, no taps
- fake_peer.py script drives screen updates without refresh
```

---

## 10. Questions?

If any step is ambiguous, stop and ask before deviating. Do not add features not listed here. Do not change the handshake. Do not hardcode the URL.
