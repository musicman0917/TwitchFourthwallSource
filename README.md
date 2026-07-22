# Stream Merch Ad Overlay

Holds up merch on your webcam &rarr; a vision detector recognizes the specific
item &rarr; a browser overlay in OBS pops up a live ad for that product,
pulling name/price/image straight from your Fourthwall store.

## How it works

1. **`detector/`** — a Python service that reads your webcam, compares each
   frame against CLIP image embeddings of reference photos you provide for
   each product, and detects when one is confidently being shown.
2. **`overlay/`** — a static web page (added to OBS as a Browser Source) that
   listens for detection events over WebSocket and fetches live product info
   from the Fourthwall Storefront API to render an ad card.

Both are served by `detector.py` from a single local server, so there's
nothing to deploy — everything runs on your streaming PC.

## Setup

### 1. Get your Fourthwall Storefront API token

In your Fourthwall dashboard: **Settings &rarr; For Developers &rarr; Headless**.
Copy the storefront token, and note your shop's `*.fourthwall.com` subdomain.

Edit `overlay/config.js` and fill in:

```js
window.OVERLAY_CONFIG = {
  storefrontToken: "ptkn_...",   // your real token
  shopDomain: "your-shop",       // your subdomain
  websocketUrl: "ws://localhost:8766/ws",
  displaySeconds: 8,
};
```

### 2. Install the detector

```
cd detector
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

If you have an NVIDIA GPU on Windows, install the CUDA build of torch first
per https://pytorch.org/get-started/locally/, then `pip install -r
requirements.txt` for the rest. On Apple Silicon Macs, the default `torch`
install already supports GPU (MPS) acceleration.

### 3. Add reference photos for each product

Drop a few clean, front-facing photos of each merch item into
`detector/reference_images/` (good lighting, item filling most of the
frame — this is what the live webcam view gets compared against). 2-3 photos
per item, from slightly different angles, works better than one.

### 4. Configure products

Edit `detector/config.yaml`:

```yaml
products:
  - handle: "classic-tee"          # must match the product's handle/slug
    reference_images:              #   in its Fourthwall storefront URL
      - "reference_images/classic-tee-1.jpg"
      - "reference_images/classic-tee-2.jpg"
```

### 5. Calibrate the detection threshold

Every setup (webcam quality, lighting, room) is different, so run:

```
python detector.py --calibrate
```

This prints a live similarity score per product, several times a second, and
does **not** trigger anything. Hold up each item and watch its score; then
put it down / show something else and watch the score drop. Pick a
`match_threshold` in `config.yaml` comfortably between the "showing it" and
"not showing it" scores (0.28 is a reasonable starting point, but tune it).
If it's too jumpy, raise `consecutive_frames`; if it misses real triggers,
lower it slightly.

### 6. Run it for real

```
python detector.py
```

This starts the detector loop and serves the overlay at
`http://localhost:8766/index.html`.

### 7. Add the overlay to OBS

In OBS: **Sources &rarr; Add &rarr; Browser Source**, set the URL to
`http://localhost:8766/index.html`, width/height to your canvas size,
and check "Shutdown source when not visible" off (so it keeps listening).

Start your stream. Hold up a configured merch item to your webcam — after a
couple seconds of a confident match, the ad card animates in for
`displaySeconds`, then fades out. Each product has its own cooldown
(`cooldown_seconds` in config.yaml) so it won't spam-retrigger while you keep
holding the item up.

## Notes / limitations

- This is classic-vision similarity matching (CLIP embeddings), not a
  trained object detector — it works best when the item is held reasonably
  centered, well-lit, and fills a good portion of the frame, and works
  better for visually distinct prints/logos than for plain solid-color
  items. Expect to spend a few minutes calibrating.
- Uses Fourthwall's Storefront API (`https://storefront-api.fourthwall.com/v1`),
  authenticated via a `storefront_token` query parameter, GET
  `/products/{handle}`. Response shape: `{ name, slug, images: [{ url, ... }],
  variants: [{ unitPrice: { value, currency }, ... }], ... }` — confirmed
  against Fourthwall's own open-source storefront example. `overlay/app.js`
  logs the raw response to the browser console (F12 in OBS's browser source
  properties, or a normal browser) if you ever need to double check a field
  for your store.
- The detector opens the webcam exclusively while running; if your
  streaming software also needs raw access to the same physical camera at
  the same time, check your OS/driver allows shared access, or point OBS at
  a second camera and this detector at a dedicated one.
