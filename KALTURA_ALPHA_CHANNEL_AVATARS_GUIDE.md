# Kaltura Transparent Alpha-Channel Avatars Guide

Turn a VOD Avatar API render into a real, per-pixel-transparent WebM/VP9 video — one you can composite over any background in a browser, with no green box around the presenter.

**Base URL:** `https://www.kaltura.com/api_v3` (flavors & conversion) · `https://video-avatar.$REGION.ovp.kaltura.com/api/v1` (avatar video) · avatar catalog service (avatar selection, see [VOD Avatar Studio API](KALTURA_VOD_AVATAR_API.md) §5)  
**Auth:** API v3 actions use `ks` as a form parameter; VOD Avatar API and avatar catalog actions use an `Authorization: Bearer $KALTURA_KS` header  
**Format:** JSON request/response (`format=1` on API v3 calls)  

**Status:** advanced technique. It combines standard, fully-supported Kaltura flavor customization (`conversionEnginesExtraParams` on `KalturaFlavorParams`, the same mechanism used for any custom transcoding flavor) with a specific filter-chain parameter to reach a real alpha-channel output. See [Best Practices](#10-best-practices) for browser support and other considerations.

<!-- Sections: 1. Why this works 2. Prerequisites 3. Architecture 4. Step 1 — Create a green-screen avatar video 5. Step 2 — Create the alpha-output flavor 6. Step 3 — Convert and verify 7. Tuning the chroma key 8. Reusing the pipeline 9. Error Handling 10. Best Practices 11. Related Guides -->

# 1. Why this works

The VOD Avatar API (`KALTURA_VOD_AVATAR_API.md`) and the avatar catalog service render a talking avatar onto a **flat color background** — there is no native transparent-output mode in the avatar rendering pipeline itself, and Kaltura's Video Editing composition API (`KalturaReplaceBackgroundAttributes`, see `KALTURA_VIDEO_EDITING_API.md` §8) always composites the keyed subject onto a *replacement* background, never onto transparency.

The workaround: render the avatar onto a solid, saturated green background, then use Kaltura's **Flavors & Transcoding** system to run that source video through a **custom flavor** that:

1. Chroma-keys out the green.
2. Removes green spill/tint at the edges.
3. Encodes to **VP9/WebM with a real alpha channel**, which Chrome and Firefox decode and composite natively in `<video>` and `<canvas>`.

Everything here uses standard, customer-reachable API v3 actions (`flavorParams.add`, `conversionProfile.add`, `flavorAsset.convert`) — no internal Kaltura admin console access is required.

# 2. Prerequisites

- A Kaltura account KS with standard admin privileges (`disableentitlement` recommended) — the same KS used for `flavorParams.add` and `conversionProfile.add` elsewhere in this repo's guides.
- VOD Avatar API and avatar catalog access (see `KALTURA_VOD_AVATAR_API.md` §2 for setup).
- A browser that supports WebM alpha (`Chrome`, `Firefox`). Playback compatibility is not universal — see [Best Practices](#10-best-practices).

```bash
export KALTURA_SERVICE_URL="https://www.kaltura.com/api_v3"
export KALTURA_KS="<your admin KS>"
export KALTURA_PARTNER_ID="<your partner id>"
export KALTURA_VOD_AVATAR_URL="https://video-avatar.nvp1.ovp.kaltura.com/api/v1"
export KALTURA_AVATAR_CATALOG_URL="<your avatar catalog base URL>"
export KALTURA_CHROMA_KEY_COLOR="6FED48"
```

`KALTURA_CHROMA_KEY_COLOR` is the exact green used both as the avatar's background color and as the chroma-key target in the flavor parameters below (section 5). Keep them identical — a mismatch is the most common cause of residual green fringe.

# 3. Architecture

```
Avatar catalog (avatar/create, background=color #6FED48)
        │
        ▼
VOD Avatar API (video/add, video/generate)
        │  produces a READY entry with an opaque MP4 source flavor
        ▼
Custom KalturaFlavorParams (chromakey + despill + yuva420p, VP9/WebM)
        │
        ▼
KalturaConversionProfile (wraps the custom flavorParams)
        │
        ▼
flavorAsset.convert (entryId, flavorParamsId)
        │  Kaltura's transcoding engine runs the custom filter chain
        ▼
Real alpha-channel VP9/WebM flavor asset, playable via flavorAsset.getUrl
```

The custom flavor and conversion profile are created **once** per chroma-key color. Every subsequent avatar video that uses the same green background reuses the same `flavorParamsId` — just call `flavorAsset.convert` on its entry.

# 4. Step 1 — Create a green-screen avatar video

Pick an avatar template and background color through your avatar catalog service integration (see `KALTURA_VOD_AVATAR_API.md` §5). The background **must** be `#RRGGBB` format (a `0x` prefix is rejected with `AVATAR_INVALID_BACKGROUND_ID`).

```bash
curl -s -X POST "$KALTURA_AVATAR_CATALOG_URL/avatar-template/list" \
  -H "Authorization: Bearer $KALTURA_KS" \
  -H "Content-Type: application/json" \
  -d '{"pager": {"offset": 0, "limit": 500}}'
```

Pick a `templateId` from the response, then create the avatar with the green background:

```bash
curl -s -X POST "$KALTURA_AVATAR_CATALOG_URL/avatar/create" \
  -H "Authorization: Bearer $KALTURA_KS" \
  -H "Content-Type: application/json" \
  -d "{
    \"templateId\": \"$AVATAR_TEMPLATE_ID\",
    \"background\": {\"type\": \"color\", \"value\": \"#$KALTURA_CHROMA_KEY_COLOR\"},
    \"adminTags\": [\"alpha-channel-avatar\"]
  }"
```

Save the returned `id` as `$AVATAR_ID`, then create and generate the talking video:

```bash
curl -s -X POST "$KALTURA_VOD_AVATAR_URL/video/add" \
  -H "Authorization: Bearer $KALTURA_KS" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"Alpha Avatar Demo\",
    \"avatarId\": \"$AVATAR_ID\",
    \"scenes\": [
      {\"layoutType\": \"full-screen\", \"narration\": {\"text\": \"Your narration text here.\"}}
    ]
  }"
```

Save the returned `id` as `$VIDEO_ID`, then trigger generation and poll:

```bash
curl -s -X POST "$KALTURA_VOD_AVATAR_URL/video/generate" \
  -H "Authorization: Bearer $KALTURA_KS" \
  -H "Content-Type: application/json" \
  -d "{\"id\": \"$VIDEO_ID\"}"

curl -s -X POST "$KALTURA_VOD_AVATAR_URL/video/get" \
  -H "Authorization: Bearer $KALTURA_KS" \
  -H "Content-Type: application/json" \
  -d "{\"id\": \"$VIDEO_ID\"}"
```

Poll `video/get` every 15 seconds above until the response `status` field reaches `ready`.

The ready response includes `entryId` — the source media entry, still opaque green-background MP4 at this point. Save it as `$KALTURA_ENTRY_ID`.

# 5. Step 2 — Create the alpha-output flavor

This is the one-time setup step. Create a `KalturaFlavorParams` with VP9/vorbis/WebM output and the tuned chroma-key + despill filter chain in `conversionEnginesExtraParams`:

```bash
curl -X POST "$KALTURA_SERVICE_URL/service/flavorParams/action/add" \
  -d "ks=$KALTURA_KS" \
  -d "format=1" \
  -d "flavorParams[objectType]=KalturaFlavorParams" \
  -d "flavorParams[name]=alpha-chromakey-vp9" \
  -d "flavorParams[description]=VP9/WebM chromakey-to-alpha output" \
  -d "flavorParams[partnerId]=$KALTURA_PARTNER_ID" \
  -d "flavorParams[videoCodec]=vp9" \
  -d "flavorParams[audioCodec]=vorbis" \
  -d "flavorParams[format]=webm" \
  -d "flavorParams[conversionEngines]=2,99,3" \
  -d "flavorParams[conversionEnginesExtraParams]=-vf \"format=yuv444p,chromakey=0x$KALTURA_CHROMA_KEY_COLOR:0.10:0.06,despill=type=green:mix=1.0:expand=1:green=-1.5,format=yuva420p\" -pix_fmt yuva420p"
```

Save the returned `id` as `$KALTURA_ALPHA_FLAVOR_PARAMS_ID`.

Wrap it in a conversion profile:

```bash
curl -X POST "$KALTURA_SERVICE_URL/service/conversionProfile/action/add" \
  -d "ks=$KALTURA_KS" \
  -d "format=1" \
  -d "conversionProfile[objectType]=KalturaConversionProfile" \
  -d "conversionProfile[name]=Alpha Channel Avatars" \
  -d "conversionProfile[type]=1" \
  -d "conversionProfile[flavorParamsIds]=$KALTURA_ALPHA_FLAVOR_PARAMS_ID"
```

`type=1` is `MEDIA` (on-demand conversion, not the account's default ingest profile) — it does not affect any other content on the account. See `KALTURA_UPLOAD_AND_INGESTION_API.md` for the full flavor and conversion profile object reference.

# 6. Step 3 — Convert and verify

Trigger conversion of the green-screen source entry through the custom flavor:

```bash
curl -X POST "$KALTURA_SERVICE_URL/service/flavorAsset/action/convert" \
  -d "ks=$KALTURA_KS" \
  -d "format=1" \
  -d "entryId=$KALTURA_ENTRY_ID" \
  -d "flavorParamsId=$KALTURA_ALPHA_FLAVOR_PARAMS_ID"
```

This returns `null` on success — that is normal. Poll `flavorAsset.getflavorassetswithparams` for `entryId=$KALTURA_ENTRY_ID` until the entry matching `flavorParamsId=$KALTURA_ALPHA_FLAVOR_PARAMS_ID` reaches `status=2` (READY). Save its `id` as `$KALTURA_ALPHA_FLAVOR_ASSET_ID`, then get its playback URL:

```bash
curl -X POST "$KALTURA_SERVICE_URL/service/flavorAsset/action/getUrl" \
  -d "ks=$KALTURA_KS" \
  -d "format=1" \
  -d "id=$KALTURA_ALPHA_FLAVOR_ASSET_ID"
```

Save the returned URL as `$KALTURA_ALPHA_FLAVOR_URL`. Verify the result by rendering it in a real browser over a colorful background and confirming the background shows through where the green was — this is the only conclusive test. See the live showcase at [kaltura.md/assets/experiments/alpha-channel-avatars/showcase.html](https://kaltura.md/assets/experiments/alpha-channel-avatars/showcase.html) for a working example, or embed the flavor's URL in any page:

```html
<body style="background: repeating-conic-gradient(#ff00ff 0% 25%, #00ffff 0% 50%) 0 0/40px 40px;">
  <video src="$KALTURA_ALPHA_FLAVOR_URL" autoplay loop muted controls></video>
</body>
```

# 7. Tuning the chroma key

The filter-chain string in `flavorParams[conversionEnginesExtraParams]` (section 5) has a few numeric knobs. If the default values (`0.10:0.06` similarity/blend, `mix=1.0:expand=1:green=-1.5` despill) don't produce a clean result for your background or lighting, adjust them using this reference:

| Parameter | What it controls | Failure mode if wrong |
|---|---|---|
| `chromakey` color | Must exactly match the avatar's rendered background color (color compression can drift it slightly from the exact hex you requested) | Residual green ring around the whole subject |
| `similarity` | Hard-cutoff distance from the key color treated as fully transparent | Too high: eats into skin/hair with similar hue. Too low: green edge pixels stay opaque |
| `blend` | Width of the soft transition ramp beyond `similarity` | **The critical trap:** `similarity + blend` is the total color-distance range affected. Push either value up without checking the sum, and the ramp reaches deep enough into skin tones to make the subject's face and hair semi-transparent. Keep the sum tight (≈0.15–0.18 total) |
| `despill` `mix`/`green` | Removes residual green tint from semi-transparent edge pixels, without changing their alpha | Too weak: green fringe visible against non-green backgrounds. Too strong: rarely a problem — push it hard |

To try new values, create a new `flavorParams` (section 5) with the updated `conversionEnginesExtraParams` string, run `flavorAsset.convert` again (section 6), and check the result in a browser. The values shown in section 5 were validated against a real 1080p avatar render and hold up across multiple templates — start there and re-tune only if your background color or lighting differs.

# 8. Reusing the pipeline

Once `$KALTURA_ALPHA_FLAVOR_PARAMS_ID` exists for your chosen green, it works for **any** avatar entry rendered on that same background color — different templates, scripts, voices, all reuse the identical flavor. Producing a new transparent persona is just:

1. `avatar/create` with the same background color.
2. `video/add` + `video/generate` for the new script.
3. `flavorAsset.convert` on the resulting entry with the existing `$KALTURA_ALPHA_FLAVOR_PARAMS_ID`.

No new flavor or conversion profile needed per persona — see the [live showcase](https://kaltura.md/assets/experiments/alpha-channel-avatars/showcase.html), which was built from three separate personas run through this exact same flavor and streams each one directly from its Kaltura CDN URL.

# 9. Error Handling

| Error | Cause | Resolution |
|---|---|---|
| `AVATAR_INVALID_BACKGROUND_ID` | Background color passed to `avatar/create` used a `0x` prefix or another non-`#RRGGBB` format | Pass the color as `#RRGGBB`, for example `#6FED48` |
| `VIDEO_GENERATION_ALREADY_IN_PROGRESS` | `video/generate` called while another generation job is still running for the account | Poll `video/get` for the in-progress job until it reaches `ready` or `failed` before generating another video |
| Conversion never reaches `status=2` on `flavorAsset.getflavorassetswithparams` | The `conversionEnginesExtraParams` filter-chain string has a syntax error, or a value outside the ranges in section 7 | Double-check the exact string against the example in section 5 and the parameter reference in section 7, then create a new `flavorParams` and convert again |
| Output plays back with the green background still visible | Verification didn't load the actual flavor URL in a browser over a non-green background | Verify using the method in section 6 — check the video in a real browser, not by inspecting the file with another tool |
| Residual green ring or fringe around the subject | `chromakey` color doesn't exactly match the avatar's rendered background, or `despill` is too weak | Confirm `chromakey` matches `KALTURA_CHROMA_KEY_COLOR` exactly; increase `despill` `mix`/`expand` (section 7) |
| Face or hair partially transparent | `similarity + blend` range is too wide and reaches into skin-tone color distance | Reduce `similarity` and/or `blend` — keep the sum near 0.15–0.18 total (section 7) |

# 10. Best Practices

- **Create the custom flavor and conversion profile once per chroma-key color**, not once per persona. Reuse the same `flavorParamsId` across every avatar video rendered on that background — this avoids redundant `flavorParams.add`/`conversionProfile.add` calls and keeps the account's flavor list clean.
- **Start with the values in section 5** rather than guessing new ones — they're validated against a real avatar render. Only adjust the `chromakey`/`despill` parameters (section 7) if your background color or lighting genuinely differs.
- **Serve a fallback flavor for Safari.** Chrome and Firefox decode WebM VP9 alpha natively in `<video>`/`<canvas>`; Safari does not support this WebM alpha mechanism. Detect Safari and serve the still-opaque source flavor, or composite the alpha video against a background server-side, for those users.
- **Use `vorbis` for audio**, not Opus — the `conversionEnginesExtraParams` filter chain in this guide targets a VP9/vorbis/WebM container. The custom flavor is additive: it does not replace or affect the entry's original MP4/H.264 flavors, which remain available and fully opaque.

## Caveats & Limitations

- **Browser support is not universal.** Safari does not decode WebM VP9 alpha — plan the fallback described above.
- **Conversion time.** Each `flavorAsset.convert` call runs a full transcode through the custom filter chain — budget the same processing time as any other flavor conversion for a source of that length and resolution.

# 11. Related Guides

- **[VOD Avatar Studio API](KALTURA_VOD_AVATAR_API.md)** — Create and generate the source avatar video this guide starts from.
- **[Upload & Ingestion API](KALTURA_UPLOAD_AND_INGESTION_API.md)** — Full reference for flavors, conversion profiles, and flavor asset management.
- **[Video Editing API](KALTURA_VIDEO_EDITING_API.md)** — Composite a keyed subject onto a replacement background instead of transparency.
- **[Conversational Avatar API](KALTURA_CONVERSATIONAL_AVATAR_API.md)** — Real-time conversational AI avatars, as opposed to the pre-recorded avatars this guide covers.
