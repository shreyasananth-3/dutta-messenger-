# Pointing the Flutter app at the right backend

If video / audio / any feature is still hitting `ngrok-free.dev` — or any URL that isn't the AWS one — the Flutter app is pointed at the wrong backend. That's a config change, not a code change. This doc walks through the fix and how to verify.

---

## TL;DR

**The one and only production URL:**
```
https://dattamessenger.duckdns.org
```

Set `API_BASE` to that in the Flutter `.env`. Kill any hardcoded `ngrok-free.dev` references. Restart the app. Done.

---

## Why your app is hitting ngrok

When the backend was being developed locally, the laptop was exposed to the internet via an ngrok tunnel like `https://earthquaking-charlena-phonily.ngrok-free.dev`. That tunnel:

- Points at a **laptop** — it dies when the laptop sleeps, reboots, loses wifi, or ngrok rotates the subdomain
- Is **not** for real use — it's a debugging tool
- Is **unstable** — the URL changes every time someone restarts ngrok on the free tier

Somewhere in your Flutter app, that ngrok URL got written down (in `.env`, in a `const` string, in a provider's default value, or directly in a widget). Now every API request goes there, and when the tunnel is down, your app looks broken.

**The fix is to replace it with the AWS URL, which is stable and actually production-grade.**

---

## The switch — 3 minutes

### 1. Update your Flutter `.env`

Whatever you put as `API_BASE` in [websocket-integration.md §3.3](websocket-integration.md), replace it with:

```
API_BASE=https://dattamessenger.duckdns.org
```

That's the only line that needs to change.

### 2. Grep the codebase for stray ngrok URLs

Someone probably hardcoded it somewhere it shouldn't be. Run this in your Flutter repo:

```bash
grep -rn "ngrok" lib/ test/ integration_test/ 2>/dev/null
grep -rn "earthquaking-charlena-phonily" lib/ test/ 2>/dev/null
```

**Every match is a bug.** The only place the URL should live is `.env` / `dotenv.env['API_BASE']`. If you find it in a widget, a service, a constants file — rip it out and replace with the env lookup.

### 3. Hot restart (not hot reload)

Dotenv values are loaded once at app startup. Hot reload won't pick up `.env` changes — you need a full restart:
- In VS Code / Android Studio: press the restart button (looks like a circular arrow), not the lightning bolt
- In terminal: stop the session with `q` / `Ctrl-C`, then `flutter run` again

---

## How to verify you switched correctly

### Check 1 — base URL resolves
On app startup, log it:
```dart
print('API_BASE = ${dotenv.env['API_BASE']}');
```
Expect to see `https://dattamessenger.duckdns.org`. If you see anything else, `.env` didn't load or you hardcoded somewhere.

### Check 2 — login works
```bash
curl https://dattamessenger.duckdns.org/health
# expect: {"status":"healthy"}
```
And in the app, log in with any seeded user (`admin@demo.school` / `DemoPass123!`). If login works, REST is wired correctly.

### Check 3 — WebSocket connects
After login, open a chat screen. In your backend's perspective, a `101 Switching Protocols` should appear in logs. You can also check on the client side — the `connection.established` frame should arrive within a second.

### Check 4 — media download works
Upload a video (or use an existing one) and tap on it. The Flutter app should:
1. Call `GET https://dattamessenger.duckdns.org/api/v1/media/{id}/download` — gets a presigned URL
2. Stream the bytes from the S3 URL returned in the response (this URL points at `*.s3.ap-south-1.amazonaws.com` — **that's correct**, not a bug)

If the first request still goes to `ngrok-free.dev`, you missed a hardcoded URL somewhere.

---

## About the video / media URL specifically

This is the thing that tripped you up, so let's be explicit:

When you tap a video in the chat, two different things happen at two different domains:

```
1. Flutter calls your BACKEND:
     GET https://dattamessenger.duckdns.org/api/v1/media/{media_id}/download
   Backend responds with:
     { "download_url": "https://duttamessenger-media.s3.ap-south-1.amazonaws.com/...signed..." }

2. Flutter streams the video from S3 directly:
     GET https://duttamessenger-media.s3.ap-south-1.amazonaws.com/...signed...
```

**Step 2 is NOT through our backend.** It hits S3 directly. This is correct — it's how the whole upload / download design works (see [media.md](media.md)).

So:
- If step 1 goes to `ngrok-free.dev` → your `API_BASE` is wrong → **this doc fixes it**
- If step 1 goes to `dattamessenger.duckdns.org` and step 2 goes to `s3.ap-south-1.amazonaws.com` → **everything is correct**, even though two domains are involved

---

## What the team should do right now

1. **Stop the app.**
2. **Update `.env`** → `API_BASE=https://dattamessenger.duckdns.org`
3. **Grep for `ngrok`** in `lib/` — delete or replace every match with `dotenv.env['API_BASE']`.
4. **Hot restart** (full restart, not hot reload).
5. **Log in and upload a video** — if you can see the video play, you're done.

If step 5 still fails, send me:
- The exact URL your app is calling for `/download` (from network logs / devtools)
- The response body
- The domain the video player is trying to hit when it fails

That's enough to debug without needing your repo.

---

## Keeping multiple environments cleanly

As you add staging, production, CI etc., you'll want to switch environments without editing `.env` every time. The clean pattern:

**In `.env.example` (committed):**
```
API_BASE=http://localhost:8000
```

**In `.env.local` (gitignored):**
```
API_BASE=https://dattamessenger.duckdns.org
```

**Per-build flavor (for real prod/staging later):**
```bash
flutter run --dart-define=API_BASE=https://dattamessenger.duckdns.org
flutter run --dart-define=API_BASE=http://10.0.2.2:8000    # backend on same laptop, Android emulator
```

Then in Dart:
```dart
final apiBase = const String.fromEnvironment('API_BASE',
    defaultValue: '').ifEmpty(dotenv.env['API_BASE']!);
```

For now though, just the `.env` change is enough. Get off ngrok first, worry about build flavors later.

---

## Quick reference

| Purpose | URL |
|---------|-----|
| REST base | `https://dattamessenger.duckdns.org` |
| WebSocket | `wss://dattamessenger.duckdns.org/api/v1/ws/chat` |
| Swagger (interactive docs) | `https://dattamessenger.duckdns.org/docs` |
| Health | `https://dattamessenger.duckdns.org/health` |

**S3 for media** is a separate domain returned by the API at request time — don't hardcode it, don't configure it, just follow the `download_url` the backend gives you.
