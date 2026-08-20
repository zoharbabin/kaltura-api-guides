# Transparent (Alpha-Channel) AI Avatars on Kaltura

Turn a VOD Avatar API render into a real, per-pixel-transparent WebM/VP9 video — one you can composite over any background in a browser, with no green box around the presenter.

**Status:** advanced / unsupported technique. It relies on a raw ffmpeg command-line injection point (`conversionEnginesExtraParams` on `KalturaFlavorParams`) that Kaltura exposes for custom flavor tuning but does not officially document or guarantee for this purpose. Test on a non-production account first. See [Caveats](#8-caveats--limitations).

<!-- Sections: 1. Why this works 2. Prerequisites 3. Architecture 4. Step 1 — Create a green-screen avatar video 5. Step 2 — Create the alpha-output flavor 6. Step 3 — Convert and verify 7. Tuning the chroma key 8. Caveats & limitations 9. Reusing the pipeline -->

## 1. Why this works

The VOD Avatar API (`KALTURA_VOD_AVATAR_API.md`) and the Avatar Catalog microservice render a talking avatar onto a **flat color background** — there is no native transparent-output mode in the avatar rendering pipeline itself, and Kaltura's Video Editing composition API (`KalturaReplaceBackgroundAttributes`, see `KALTURA_VIDEO_EDITING_API.md` §8) always composites the keyed subject onto a *replacement* background, never onto transparency.

The workaround: render the avatar onto a solid, saturated green background, then use Kaltura's **Flavors & Transcoding** system to run that source video through a **custom flavor** that:

1. Chroma-keys out the green with ffmpeg's `chromakey` filter.
2. Removes green spill/tint at the edges with the `despill` filter.
3. Encodes to **VP9/WebM with a real alpha channel** using libvpx's WebM alpha side-channel (`alpha_mode=1`), which Chrome and Firefox decode and composite natively in `<video>` and `<canvas>`.

Everything here uses standard, customer-reachable API v3 actions (`flavorParams.add`, `conversionProfile.add`, `flavorAsset.convert`) — no internal Kaltura admin console access is required.

## 2. Prerequisites

- A Kaltura account KS with standard admin privileges (`disableentitlement` recommended) — the same KS used for `flavorParams.add` and `conversionProfile.add` elsewhere in this repo's guides.
- VOD Avatar API and Avatar Catalog access (see `KALTURA_VOD_AVATAR_API.md` §2 for setup).
- `ffmpeg` and `ffprobe` installed locally, for local testing/tuning before pushing changes to the platform (optional but strongly recommended — see §7).
- A browser that supports WebM alpha (`Chrome`, `Firefox`). Playback compatibility is not universal — see [Caveats](#8-caveats--limitations).

```bash
export KALTURA_SERVICE_URL="https://www.kaltura.com/api_v3"
export KALTURA_KS="<your admin KS>"
export KALTURA_PARTNER_ID="<your partner id>"
export KALTURA_VOD_AVATAR_URL="https://video-avatar.nvp1.ovp.kaltura.com/api/v1"
export KALTURA_AVATAR_CATALOG_URL="https://api.avatar.us.kaltura.ai/v1"
export KALTURA_CHROMA_KEY_COLOR="6FED48"
```

`KALTURA_CHROMA_KEY_COLOR` is the exact green used both as the avatar's background color and as the ffmpeg `chromakey` target. Keep them identical — a mismatch is the most common cause of residual green fringe.

## 3. Architecture

```
Avatar Catalog (avatar/create, background=color #6FED48)
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
        │  Kaltura's transcoding engine runs the custom ffmpeg filter chain
        ▼
Real alpha-channel VP9/WebM flavor asset, playable via flavorAsset.getUrl
```

The custom flavor and conversion profile are created **once** per chroma-key color. Every subsequent avatar video that uses the same green background reuses the same `flavorParamsId` — just call `flavorAsset.convert` on its entry.

## 4. Step 1 — Create a green-screen avatar video

Pick an avatar template and background color. The background **must** be `#RRGGBB` format (a `0x` prefix is rejected with `AVATAR_INVALID_BACKGROUND_ID`).

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

# poll every 15s until status is "ready"
curl -s -X POST "$KALTURA_VOD_AVATAR_URL/video/get" \
  -H "Authorization: Bearer $KALTURA_KS" \
  -H "Content-Type: application/json" \
  -d "{\"id\": \"$VIDEO_ID\"}"
```

The ready response includes `entryId` — the source media entry, still opaque green-background MP4 at this point. Save it as `$KALTURA_ENTRY_ID`.

## 5. Step 2 — Create the alpha-output flavor

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
  -d "conversionProfile[type]=2" \
  -d "conversionProfile[flavorParamsIds]=$KALTURA_ALPHA_FLAVOR_PARAMS_ID"
```

`type=2` is `MEDIA_ENTRY` (on-demand conversion, not the account's default ingest profile) — it does not affect any other content on the account.

## 6. Step 3 — Convert and verify

Trigger conversion of the green-screen source entry through the custom flavor:

```bash
curl -X POST "$KALTURA_SERVICE_URL/service/flavorAsset/action/convert" \
  -d "ks=$KALTURA_KS" \
  -d "format=1" \
  -d "entryId=$KALTURA_ENTRY_ID" \
  -d "flavorParamsId=$KALTURA_ALPHA_FLAVOR_PARAMS_ID"
```

This returns `null` on success — that is normal. Poll `flavorAsset.getflavorassetswithparams` for `entryId=$KALTURA_ENTRY_ID` until the entry matching `flavorParamsId=$KALTURA_ALPHA_FLAVOR_PARAMS_ID` reaches `status=2` (READY), then get its playback URL:

```bash
curl -X POST "$KALTURA_SERVICE_URL/service/flavorAsset/action/getUrl" \
  -d "ks=$KALTURA_KS" \
  -d "format=1" \
  -d "id=$KALTURA_ALPHA_FLAVOR_ASSET_ID"
```

**Verifying real transparency does not work with `ffprobe`/`ffmpeg` frame extraction.** WebM VP9 alpha is stored as a separate bitstream in a Matroska `BlockAdditional`, flagged by an `alpha_mode` stream tag — plain CLI tools decode only the primary opaque picture and will show the green background still present. Two ways to verify correctly:

1. **Check the tag directly:**
   ```bash
   ffprobe -v error -show_entries stream_tags=alpha_mode -of default=noprint_wrappers=0 output.webm
   # expect: TAG:alpha_mode=1
   ```
2. **Render it in a real browser** over a colorful background and confirm the background shows through where the green was. This is the only conclusive test — see the live showcase at [kaltura.md/assets/experiments/alpha-channel-avatars/showcase.html](https://kaltura.md/assets/experiments/alpha-channel-avatars/showcase.html) for a working example, or embed the video in any page:
   ```html
   <body style="background: repeating-conic-gradient(#ff00ff 0% 25%, #00ffff 0% 50%) 0 0/40px 40px;">
     <video src="output.webm" autoplay loop muted controls></video>
   </body>
   ```

## 7. Tuning the chroma key

Tune locally against a downloaded copy of the source MP4 before touching the live flavor — each round-trip through Kaltura's conversion pipeline costs an API call and a ~1 minute poll cycle; local ffmpeg iteration is instant.

```bash
ffmpeg -y -ss 1.5 -i source.mp4 \
  -vf "format=yuv444p,chromakey=0x6FED48:SIMILARITY:BLEND,despill=type=green:mix=MIX:expand=EXPAND:green=-1.5,format=yuva420p" \
  -frames:v 1 test_frame.png
```

| Parameter | What it controls | Failure mode if wrong |
|---|---|---|
| `chromakey` color | Must exactly match the avatar's rendered background color (sample a frame with a color picker — H.264 compression drifts it slightly from the exact hex you requested) | Residual green ring around the whole subject |
| `similarity` | Hard-cutoff distance from the key color treated as fully transparent | Too high: eats into skin/hair with similar hue. Too low: green edge pixels stay opaque |
| `blend` | Width of the soft transition ramp beyond `similarity` | **The critical trap:** `similarity + blend` is the total color-distance range affected. Push either value up without checking the sum, and the ramp reaches deep enough into skin tones to make the subject's face and hair semi-transparent. Keep the sum tight (≈0.15–0.18 total) |
| `despill` `mix`/`green` | Removes residual green tint from semi-transparent edge pixels, without changing their alpha | Too weak: green fringe visible against non-green backgrounds. Too strong: rarely a problem — push it hard |
| `format=yuv444p` (pre-filter) | Upsamples the H.264 source's 4:2:0 chroma before keying, avoiding blocky transitions | Skip it and edges look stair-stepped/blocky at chroma block boundaries |

**Validate a candidate with a quick pixel check**, not just eyeballing:

```python
from PIL import Image
import numpy as np
img = np.array(Image.open("test_frame.png").convert("RGBA")).astype(int)
r, g, b, a = img[...,0], img[...,1], img[...,2], img[...,3]
edge = (a > 5) & (a < 250)
fringe = edge & (g > r + 15) & (g > b + 15)
print("fringe % of edge pixels:", 100 * fringe.sum() / max(edge.sum(), 1))
print("face-center alpha (should be 255):", img[620, 960, 3])  # adjust to your frame
```

Target: `fringe % == 0` and full-opacity sample points on the subject stay at `255`. The values in §5 (`0.10:0.06` similarity/blend, `mix=1.0:expand=1:green=-1.5` despill) were reached this way against a real 1080p avatar render and hold up across multiple timestamps in the clip — start there and re-tune only if your background color or lighting differs.

## 8. Caveats & limitations

- **Browser support is not universal.** Chrome and Firefox decode WebM VP9 alpha natively in `<video>`/`<canvas>`. Safari does not support this WebM alpha mechanism. Plan a fallback (e.g., serve the still-opaque flavor, or composite server-side) for Safari users.
- **No Opus audio.** Only `vorbis` audio codec is supporteed.
- **This is one-way.** The custom flavor produces an *additional* flavor asset on the entry; it does not replace or affect the entry's original MP4/H.264 flavors, which remain fully opaque as before.
- **Color drift.** H.264 compression on the avatar source shifts the exact background hex slightly from what you requested. Sample an actual decoded frame before finalizing your `chromakey` color rather than assuming it matches the avatar's configured background value exactly.

## 9. Reusing the pipeline

Once `$KALTURA_ALPHA_FLAVOR_PARAMS_ID` exists for your chosen green, it works for **any** avatar entry rendered on that same background color — different templates, scripts, voices, all reuse the identical flavor. Producing a new transparent persona is just:

1. `avatar/create` with the same background color.
2. `video/add` + `video/generate` for the new script.
3. `flavorAsset.convert` on the resulting entry with the existing `$KALTURA_ALPHA_FLAVOR_PARAMS_ID`.

No new flavor or conversion profile needed per persona — see the [live showcase](https://kaltura.md/assets/experiments/alpha-channel-avatars/showcase.html), which was built from three separate personas run through this exact same flavor and streams each one directly from its Kaltura CDN URL.
