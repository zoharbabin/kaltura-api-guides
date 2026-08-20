# Kaltura Agentic Avatars

Agentic Avatars are AI-powered video avatars that hold real-time conversations with users, built on the `@kaltura/intelligent-agents` SDK (this capability was previously documented here as the "Conversational Avatar" embed). An avatar speaks, listens, and responds using AI — enabling training simulations, coaching, interview practice, and customer-facing conversational agents.

**Base URL:** No fixed REST endpoint. Server-side provisioning uses the `@kaltura/intelligent-agents` Node SDK (git-hosted, pin to a release tag — not yet published to the public npm registry); the browser runtime loads from a pinned CDN URL, e.g. `https://cdn.jsdelivr.net/gh/kaltura/intelligent-agents-sdk@v1.2.0/src/experience/index.js`  
**Auth:** `AGENTIC_PARTNER_ID` + `AGENTIC_ADMIN_SECRET` server-side → short-lived, scoped KS tokens minted per session for the browser  
**Format:** Node.js SDK (server) + ES module JavaScript (browser)  

<!-- Sections: 1.When to Use | 2.Prerequisites | 3.Auth Model | 4.Quick Start — Server-Side Provisioning | 5.Quick Start — Browser Experience | 6.Capabilities | 7.Error Handling | 8.Best Practices | 9.Related Guides -->


# 1. When to Use

- **HR interview simulation** — Candidates practice with an AI interviewer that evaluates responses
- **Sales and product training** — Employees rehearse scenarios with an AI coach that adapts to their answers
- **Customer onboarding and support** — Guide users through setup steps or answer product questions with a conversational avatar
- **Presenter-guided walkthroughs** — Narrate a slide deck or product demo with per-slide context and deterministic, speech-free navigation
- **Structured data collection** — Collect contact details or other structured input through an avatar-guided form
- **Live context injection** — Feed the avatar real-time data (code, metrics, session state) so its responses stay current with what the user is doing
- **Customer-facing conversational agents** — Embed an avatar that answers questions about your products or services


# 2. Prerequisites

- **`AGENTIC_PARTNER_ID` and `AGENTIC_ADMIN_SECRET`** — Obtain from Rich Media CMS (`kmc.kaltura.com`) → Settings → Integration Settings (partner ID + Administrator Secret). Use these server-side only.
- **A Node.js server** to run the Management SDK, provision agents, and mint scoped conversation tokens for the browser.
- **`@kaltura/intelligent-agents` as a dependency** — The package is git-hosted and pinned to a release tag (for example `v1.2.0`), not published to the public npm registry. Add it as a git dependency pinned to that tag.
- **HTTPS and microphone access** — The browser experience requires a secure context for microphone and camera access.
- **A `socket.io-client` in your browser bundle** — The SDK injects its transport via `socketFactory` rather than bundling one, so supply your own Socket.IO client.


# 3. Auth Model

Every call authenticates with a Kaltura Session (KS) passed as a bearer credential. The SDK mints four distinct KS types, each scoped to a different privilege. Scoping is enforced at mint time: `createConversationToken()` and `createAgentToken()` reject a request for a token carrying `disableentitlement` before the token is ever issued, so a browser session only ever receives an entitlement-scoped Conversation or Agent token — never an admin-privileged one:

| KS type | `privileges` | Use | Notes |
|---|---|---|---|
| Admin | `disableentitlement` | Management CRUD — provisioning, catalog | Server-side use only |
| Conversation | `geniegpcid:<configId>` | Talking to the AI | Entitlement enforced, short TTL |
| Agent | `agentid:<agentId>` | Agent-scoped calls | — |
| Widget | Derived from `widgetId` | End-user embed | No admin secret needed client-side |

Scripted-video sessions (server-driven narration without a live conversation loop) use a different pattern: the initial `create` call takes an admin KS, and every subsequent call (`init-client`, `say-audio`, `interrupt`, `keep-alive`, `end`) takes a Bearer JWT returned by `create` instead of a KS.

Use `revoke()` to invalidate an active token and `setToken()` to rotate credentials mid-session. Use `restrictions` to scope a token to the minimum privilege a caller needs. See the [SDK Reference](https://kaltura.github.io/intelligent-agents-sdk/reference/sdk-reference/) and [API Reference](https://kaltura.github.io/intelligent-agents-sdk/reference/api-reference/) for the full session lifecycle.


# 4. Quick Start — Server-Side Provisioning

Provision an agent and mint a browser-safe conversation token from your server:

```js
import { Management } from '@kaltura/intelligent-agents/management';

const partnerId = process.env.AGENTIC_PARTNER_ID;
const adminSecret = process.env.AGENTIC_ADMIN_SECRET;
const kaltura = new Management({ partnerId, adminSecret });

const admin = await kaltura.sessions.createAdminToken();          // disableentitlement — server-only
const agent = await kaltura.provision({ brief: 'A friendly technical-support agent for a video platform', ks: admin.ks });
console.log('Provisioned:', { name: agent.name, configId: agent.configId, agentId: agent.agentId, widgetId: agent.widgetId });

// Send the browser this scoped, entitlement-ON token — keep admin.ks server-side.
const conv = await kaltura.sessions.createConversationToken({ configId: agent.configId, ttlSeconds: 3600 });
console.log('Conversation token scope:', conv.scope);             // entitlementEnforced: true

const reply = await kaltura.conversations.send({ userMessage: 'Hello, what can you help me with?' }, conv.ks);
console.log('Agent says:', reply.text);
```

Expose `conv.ks` — and the routing details your browser session needs (see section 5) — through your own `appInit`-style endpoint. Keep `admin.ks` and `AGENTIC_ADMIN_SECRET` server-side at all times.

The `quickstart/` CLI in the SDK repository (`node create-agent.mjs "<brief>"`) automates this same flow for local onboarding — it reads `.env` and prints `configId`/`agentId`/`avatarId`/`widgetId` directly. See [Getting Started — Step 1](https://kaltura.github.io/intelligent-agents-sdk/getting-started/#step-1-get-your-credentials).


# 5. Quick Start — Browser Experience

Mount the live avatar experience against the session your server minted:

```js
import { KalturaAvatarSession } from '@kaltura/intelligent-agents/experience';

const init = await fetch('/appInit').then((r) => r.json());  // your server calls Management.application.appInit(widgetKs)

const video = document.createElement('video');
video.autoplay = true;
video.playsInline = true;
document.getElementById('avatar').appendChild(video);

const session = new KalturaAvatarSession({
  token: init.ks,
  conversationManagerUrl: init.conversationManagerUrl,
  srsBaseUrl: init.srsBaseUrl,
  turnServerUrl: init.turnServerUrl,
  videoEl: video,
  socketFactory: (url, opts) => io(url, opts),   // socket.io is your dependency — injected
});

session.on('transcript', ({ text, type }) => console.log(type, text));
await session.connect();
session.speak('Hello!');
```

Load the module from a pinned CDN tag in production:

```html
<script type="module">
  import { KalturaAvatarSession } from 'https://cdn.jsdelivr.net/gh/kaltura/intelligent-agents-sdk@v1.2.0/src/experience/index.js';
</script>
```

Use `@latest` for prototyping only, and pin a specific tag (`v1.2.0` or later) for production — `@latest` can change without notice. See [Getting Started — Step 3](https://kaltura.github.io/intelligent-agents-sdk/getting-started/#step-3-create-our-own-agent-from-scratch) for the full walkthrough and [Where to Go Next](https://kaltura.github.io/intelligent-agents-sdk/getting-started/#where-to-go-next) for follow-on guides.


# 6. Capabilities

The SDK exposes several subpaths beyond the core `management` and `experience` entry points. Each is covered in full on the SDK documentation site — use it as the authoritative reference for complete API detail:

- **GenUI** (`./experience/genui`) — A dependency-free renderer for structured AI output (summaries, quizzes, carousels, code blocks, tables) driven off the live conversation stream, with a theming class contract and a progressive-enhancement seam for host libraries like Mermaid or Chart.js. See the [GenUI Reference](https://kaltura.github.io/intelligent-agents-sdk/reference/genui-reference/).
- **Presenter** (`./experience/presenter`) — Drives an avatar-guided slide-deck walkthrough end-to-end, with per-slide context injection and deterministic, speech-free navigation via a client-side command rather than parsing spoken text. See the [Client Commands guide](https://kaltura.github.io/intelligent-agents-sdk/guides/client-commands/) and the [Use-Case Catalog](https://kaltura.github.io/intelligent-agents-sdk/reference/use-cases/).
- **Live context injection** — Push runtime data (code state, form progress, session variables) into an active conversation. See the [Dynamic Data Injection guide](https://kaltura.github.io/intelligent-agents-sdk/guides/dynamic-data-injection/).
- **Structured data forms** — Collect structured input, such as contact details, through the avatar. See the [Structured Data Forms guide](https://kaltura.github.io/intelligent-agents-sdk/guides/structured-data-forms/).
- **Accessibility and AI disclosure** — Live captions satisfy WCAG 1.2.4 via a built-in caption service. The tap-to-talk control is click-to-toggle rather than press-and-hold, satisfying WCAG 2.5.2. An AI-disclosure gate (`requireDisclosureAck: true`) holds `speak()` until the app calls `acknowledgeDisclosure()` — a code-level implementation of the EU AI Act Art. 50 interaction-disclosure requirement. See the [SDK Reference](https://kaltura.github.io/intelligent-agents-sdk/reference/sdk-reference/).
- **Security posture** — A documented control matrix mapped to NIST 800-53, covering credential handling, token scoping, and revocation. See [Security](https://kaltura.github.io/intelligent-agents-sdk/reference/security/).
- **Architecture** — For a map of how the pieces fit together, see the [Architecture overview](https://kaltura.github.io/intelligent-agents-sdk/explanation/architecture/), the [Architecture Reference](https://kaltura.github.io/intelligent-agents-sdk/reference/architecture-reference/) for internals, and the [Wire Protocol](https://kaltura.github.io/intelligent-agents-sdk/reference/wire-protocol/) for the message-level contract.


# 7. Error Handling

The SDK enforces credential scoping at mint time, not at browser construction time. `createConversationToken()` and `createAgentToken()` reject any request for a token carrying `disableentitlement` before the token is issued, raising an `entitlement_violation` error — so the browser never receives an admin-scoped token to construct a session with in the first place. Provision with an admin token on the server, then mint and hand the browser only a Conversation or Agent token (see section 4).

The AI-disclosure gate fails closed in the same spirit: calling `speak()` before `acknowledgeDisclosure()`, with `requireDisclosureAck: true` set, returns a typed `disclosure_required` error instead of speaking.

```js
try {
  session.speak('Hello!');
} catch (err) {
  if (err.code === 'disclosure_required') {
    await showDisclosureConsentUi();
    session.acknowledgeDisclosure();
    session.speak('Hello!');
  }
}
```

For the full set of error types, event payloads, and recovery behavior across `management`, `experience`, and the scripted-video flow, see the [API Reference](https://kaltura.github.io/intelligent-agents-sdk/reference/api-reference/).


# 8. Best Practices

1. **Provision and mint tokens on the server.** Keep `AGENTIC_ADMIN_SECRET` and admin KS tokens server-side; expose only scoped Conversation or Widget tokens to the browser through your own `appInit`-style endpoint.
2. **Scope every token to the minimum privilege it needs.** Use Conversation tokens for talking to the AI and Widget tokens for end-user embeds. Use `revoke()` for active revocation and `setToken()` to rotate credentials mid-session.
3. **Pin the SDK to a release tag in production.** Use a specific tag (for example `v1.2.0`) for both the git dependency and the CDN import. Reserve `@latest` for prototyping.
4. **Enable the AI-disclosure gate.** Set `requireDisclosureAck: true` and call `acknowledgeDisclosure()` after your own consent UI, so users know they're talking to AI before the avatar speaks — this satisfies EU AI Act Art. 50.
5. **Use the SDK's built-in accessibility features.** Rely on its caption service and click-to-toggle tap-to-talk control to meet WCAG 2.2 AA rather than building custom equivalents.
6. **Review the Security reference before production launch.** The NIST 800-53 control matrix at [Security](https://kaltura.github.io/intelligent-agents-sdk/reference/security/) covers credential handling, token scoping, and revocation in the detail a compliance review needs.
7. **Use HTTPS.** Required for microphone and camera access in the browser experience.
8. **Supply your own Socket.IO client via `socketFactory`.** The SDK injects rather than bundles the transport, so keep it current as part of your own dependency tree.
9. **Use the intelligent-agents-sdk for new integrations.** It's the current path for Agentic Avatars, covering provisioning, the live conversation runtime, GenUI, and the Presenter walkthrough flow in one package.

## Common Integration Patterns

| Pattern | Description |
|---------|-------------|
| Interview simulation | Conversation token scoped to a dedicated interviewer `configId`; server-side transcript captured via `conversations.send()` responses |
| Presenter-guided demo | `Presenter` drives per-slide context injection and silent navigation via a client-side command |
| Live context injection | Push code state, form progress, or metrics into an active conversation via the Dynamic Data Injection pattern |
| Structured data collection | Avatar-guided form collection via the Structured Data Forms pattern |
| Accessibility-first embed | Built-in caption service and click-to-toggle tap-to-talk control, with the AI-disclosure gate enabled |

See the [Use-Case Catalog](https://kaltura.github.io/intelligent-agents-sdk/reference/use-cases/) for the full set of documented patterns.


# 9. Related Guides

- **[VOD Avatar Studio](KALTURA_VOD_AVATAR_API.md)** — Pre-recorded avatar video generation from scripts — the pre-recorded counterpart to this real-time conversational experience
- **[Experience Components Overview](KALTURA_EXPERIENCE_COMPONENTS_API.md)** — Index of all embeddable components with shared guidelines
- **[Unisphere Framework](KALTURA_UNISPHERE_FRAMEWORK_API.md)** — The micro-frontend framework behind Kaltura's other embeddable widgets, including the pre-recorded VOD Avatar Studio
- **[AI Genie API](KALTURA_AI_GENIE_API.md)** — Conversational AI search (text-based RAG, no avatar)
- **[Events Platform](KALTURA_EVENTS_PLATFORM_API.md)** — Virtual events where avatars can serve as AI moderators or assistants
