"""
Merch-on-screen detector for the Fourthwall stream overlay.

Watches a webcam, compares each sampled frame against CLIP embeddings of
reference photos for each configured product, and when a product stays
above `match_threshold` for `consecutive_frames` samples (and its cooldown
has elapsed), broadcasts a trigger event over WebSocket to the browser
overlay (see ../overlay).

Usage:
    python detector.py                 # run the detector + overlay server
    python detector.py --calibrate     # print live similarity scores instead
                                        # of triggering, to help pick a threshold
    python detector.py --config path/to/config.yaml
"""

import argparse
import asyncio
import time
from pathlib import Path

import cv2
import torch
import yaml
from aiohttp import web
from PIL import Image

import open_clip

DEVICE = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")


class Product:
    def __init__(self, handle: str, embedding: torch.Tensor):
        self.handle = handle
        self.embedding = embedding
        self.consecutive_matches = 0
        self.last_triggered_at = 0.0


def load_config(path: Path) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def build_products(config: dict, model, preprocess) -> list[Product]:
    base_dir = Path(config["_config_dir"])
    products = []
    for entry in config["products"]:
        handle = entry["handle"]
        image_paths = [base_dir / p for p in entry["reference_images"]]
        embeddings = []
        for image_path in image_paths:
            if not image_path.exists():
                print(f"[warn] reference image not found, skipping: {image_path}")
                continue
            img = preprocess(Image.open(image_path).convert("RGB")).unsqueeze(0).to(DEVICE)
            with torch.no_grad():
                emb = model.encode_image(img)
                emb = emb / emb.norm(dim=-1, keepdim=True)
            embeddings.append(emb)
        if not embeddings:
            print(f"[warn] no valid reference images for '{handle}', it will never match")
            continue
        avg = torch.mean(torch.cat(embeddings, dim=0), dim=0, keepdim=True)
        avg = avg / avg.norm(dim=-1, keepdim=True)
        products.append(Product(handle, avg))
        print(f"[init] loaded '{handle}' from {len(embeddings)} reference photo(s)")
    return products


class Detector:
    def __init__(self, config: dict, model, preprocess, products: list[Product]):
        self.config = config
        self.model = model
        self.preprocess = preprocess
        self.products = products
        self.websockets: set[web.WebSocketResponse] = set()
        self.calibrate = False

    async def broadcast(self, message: dict):
        dead = set()
        for ws in self.websockets:
            try:
                await ws.send_json(message)
            except ConnectionResetError:
                dead.add(ws)
        self.websockets -= dead

    def embed_frame(self, frame_bgr) -> torch.Tensor:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        tensor = self.preprocess(pil_img).unsqueeze(0).to(DEVICE)
        with torch.no_grad():
            emb = self.model.encode_image(tensor)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        return emb

    async def run_camera_loop(self):
        cap = cv2.VideoCapture(self.config["webcam_index"])
        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open webcam index {self.config['webcam_index']}. "
                "Try a different webcam_index in config.yaml."
            )
        interval = self.config["frame_interval_seconds"]
        threshold = self.config["match_threshold"]
        needed = self.config["consecutive_frames"]
        cooldown = self.config["cooldown_seconds"]

        print(f"[detector] running on device={DEVICE}, sampling every {interval}s")
        try:
            while True:
                ok, frame = await asyncio.to_thread(cap.read)
                if not ok:
                    print("[warn] failed to read frame from webcam")
                    await asyncio.sleep(interval)
                    continue

                frame_emb = await asyncio.to_thread(self.embed_frame, frame)

                best = None
                for product in self.products:
                    sim = float((frame_emb @ product.embedding.T).item())
                    if self.calibrate:
                        print(f"  {product.handle}: {sim:.3f}")
                    if best is None or sim > best[1]:
                        best = (product, sim)

                if best is not None:
                    product, sim = best
                    if sim >= threshold:
                        product.consecutive_matches += 1
                    else:
                        product.consecutive_matches = 0

                    if not self.calibrate and product.consecutive_matches >= needed:
                        now = time.time()
                        if now - product.last_triggered_at >= cooldown:
                            product.last_triggered_at = now
                            print(f"[trigger] {product.handle} (sim={sim:.3f})")
                            await self.broadcast({
                                "event": "merch_detected",
                                "product": product.handle,
                                "confidence": round(sim, 3),
                            })
                        product.consecutive_matches = 0

                await asyncio.sleep(interval)
        finally:
            cap.release()


async def websocket_handler(request: web.Request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    detector: Detector = request.app["detector"]
    detector.websockets.add(ws)
    print(f"[overlay] client connected ({len(detector.websockets)} total)")
    try:
        async for _ in ws:
            pass
    finally:
        detector.websockets.discard(ws)
        print(f"[overlay] client disconnected ({len(detector.websockets)} total)")
    return ws


async def start_server(detector: Detector, config: dict):
    app = web.Application()
    app["detector"] = detector
    app.router.add_get("/ws", websocket_handler)
    overlay_dir = Path(__file__).parent.parent / "overlay"
    app.router.add_static("/", overlay_dir, show_index=True)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", config["http_port"])
    await site.start()
    print(f"[server] overlay served at http://localhost:{config['http_port']}/index.html")
    print(f"[server] websocket at ws://localhost:{config['http_port']}/ws")


async def main_async(config_path: Path, calibrate: bool):
    config = load_config(config_path)
    config["_config_dir"] = str(config_path.parent)

    print(f"[init] loading CLIP model on device={DEVICE} (first run downloads weights)...")
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k"
    )
    model = model.to(DEVICE).eval()

    products = build_products(config, model, preprocess)
    if not products:
        raise SystemExit("No products with valid reference images in config.yaml. Add photos and retry.")

    detector = Detector(config, model, preprocess, products)
    detector.calibrate = calibrate

    if not calibrate:
        await start_server(detector, config)
    else:
        print("[calibrate] printing live similarity scores, no triggers will fire. Ctrl+C to stop.")

    await detector.run_camera_loop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"))
    parser.add_argument("--calibrate", action="store_true", help="Print live similarity scores instead of triggering.")
    args = parser.parse_args()

    try:
        asyncio.run(main_async(Path(args.config), args.calibrate))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
