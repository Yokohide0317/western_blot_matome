"""Record the workbench on the fictional demo data as 30 fps JPEG sequences for Remotion.

Uses Chrome's screencast at device scale 2 (sharp text), a drawn cursor (screen
recordings have none), and the real app on an isolated WBM home.

    uv run python promo/record.py <project_url> <project_dir>

Writes promo/public/rec/<scene>/00000.jpg ... and promo/public/rec/scenes.json.
"""

import asyncio
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys

from playwright.async_api import async_playwright

FPS = 30
VIEW = {"width": 1440, "height": 810}
OUT = Path(__file__).parent / "public" / "rec"

CURSOR = """
(() => {
  const add = () => {
    if (document.getElementById('promo-cursor')) return;
    const c = document.createElement('div');
    c.id = 'promo-cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M3 2 L3 21 L8.5 16 L12.5 24 L15.5 22.6 L11.6 14.8 L19 14.8 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position: 'fixed', left: '-40px', top: '-40px', zIndex: 2147483647, pointerEvents: 'none',
      transform: 'translate(-3px, -2px)', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.25))'});
    const ring = document.createElement('div');
    Object.assign(ring.style, {position: 'fixed', width: '34px', height: '34px', borderRadius: '50%', zIndex: 2147483646,
      pointerEvents: 'none', border: '3px solid rgba(40,107,108,.85)', opacity: 0, transform: 'translate(-50%,-50%) scale(.4)',
      transition: 'opacity .35s ease-out, transform .35s ease-out'});
    document.documentElement.append(ring, c);
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', e => {
      ring.style.transition = 'none'; ring.style.left = e.clientX + 'px'; ring.style.top = e.clientY + 'px';
      ring.style.opacity = 1; ring.style.transform = 'translate(-50%,-50%) scale(.4)';
      requestAnimationFrame(() => { ring.style.transition = 'opacity .45s ease-out, transform .45s ease-out';
        ring.style.opacity = 0; ring.style.transform = 'translate(-50%,-50%) scale(1.3)'; });
    }, true);
  };
  document.readyState === 'loading' ? addEventListener('DOMContentLoaded', add) : add();
})();
"""


class Recorder:
    def __init__(self, page, session):
        self.page, self.session, self.frames, self.active = page, session, [], False
        session.on("Page.screencastFrame", self._frame)

    async def _frame(self, event):
        if self.active:
            self.frames.append((event["metadata"]["timestamp"], base64.b64decode(event["data"])))
        await self.session.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    async def scene(self, name, action):
        self.frames, self.active = [], True
        await self.session.send("Page.startScreencast", {"format": "jpeg", "quality": 88,
                                                         "maxWidth": VIEW["width"] * 2, "maxHeight": VIEW["height"] * 2})
        await self.page.mouse.move(*self.mouse)  # force a first frame
        start = asyncio.get_event_loop().time()
        await action()
        await self.page.wait_for_timeout(400)
        duration = asyncio.get_event_loop().time() - start
        self.active = False
        await self.session.send("Page.stopScreencast")
        return self._write(name, duration)

    def _write(self, name, duration):
        folder = OUT / name
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        frames = sorted(self.frames)
        t0 = frames[0][0]
        count = max(1, round(duration * FPS))
        j = 0
        for i in range(count):
            t = t0 + i / FPS
            while j + 1 < len(frames) and frames[j + 1][0] <= t:
                j += 1
            (folder / f"{i:05d}.jpg").write_bytes(frames[j][1])
        print(f"{name}: {count} frames from {len(frames)} captures")
        return {"name": name, "frames": count}

    mouse = (720, 400)


async def glide(page, rec, x, y, steps=28, pause=120):
    await page.mouse.move(x, y, steps=steps)
    rec.mouse = (x, y)
    await page.wait_for_timeout(pause)


async def centre(locator):
    box = await locator.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def main(url, project_dir):
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport=VIEW, device_scale_factor=2, accept_downloads=True)
        await context.add_init_script(CURSOR)
        page = await context.new_page()
        session = await context.new_cdp_session(page)
        rec = Recorder(page, session)
        await page.goto(url)
        await page.wait_for_selector("polygon.membrane")
        await page.wait_for_timeout(800)
        scenes = []

        async def membranes():
            for name in ("M1", "M2", "M1"):
                item = page.locator(".membrane-item", has_text=name).first
                x, y = await centre(item)
                await glide(page, rec, x - 60, y, pause=200)
                await page.mouse.click(x - 60, y)
                await page.wait_for_timeout(900)
        scenes.append(await rec.scene("membranes", membranes))

        async def bands():
            tab = page.get_by_role("tab", name="2. バンド選択")
            x, y = await centre(tab)
            await glide(page, rec, x, y)
            await page.mouse.click(x, y)
            await page.wait_for_selector('rect[data-side-handle="left"]')
            await page.wait_for_timeout(1000)
            handle = page.locator('rect[data-side-handle="left"]')
            hx, hy = await centre(handle)
            target = await page.evaluate("""([y]) => {
              const svg = document.querySelector('svg.canvas'); const pt = svg.createSVGPoint();
              const h = document.querySelector('rect[data-side-handle=left]');
              pt.x = 368; pt.y = +h.getAttribute('y') + (+h.getAttribute('height')) / 2;
              const m = pt.matrixTransform(svg.getScreenCTM()); return [m.x, m.y]; }""", [hy])
            await glide(page, rec, hx, hy, pause=300)
            await page.mouse.down()
            await page.mouse.move(target[0], target[1], steps=40)
            await page.wait_for_timeout(150)
            await page.mouse.up()
            await page.wait_for_timeout(1300)
            longest = page.locator(".exposure-strip article").last.locator("button.thumb")
            x, y = await centre(longest)
            await glide(page, rec, x, y, pause=250)
            await page.mouse.click(x, y)
            await page.wait_for_timeout(1500)
            adopted = page.locator(".exposure-strip article.adopted button.thumb")
            x, y = await centre(adopted)
            await glide(page, rec, x, y, pause=250)
            await page.mouse.click(x, y)
            await page.wait_for_timeout(900)
        scenes.append(await rec.scene("bands", bands))

        async def labels():
            label = page.locator("input.inline-label").first
            x, y = await centre(label)
            await glide(page, rec, x, y, pause=250)
            await page.mouse.click(x, y, click_count=3)
            await page.wait_for_timeout(250)
            await page.keyboard.type("Protein A", delay=85)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(700)
            # The agent applies the rest from the chat (shown alongside in the video).
            env_cli(project_dir, ["rename", "M2", "β-actin"])
            env_cli(project_dir, ["adjust", "M2", "--crop-left", "368"])
            env_cli(project_dir, ["lanes", "8", "--samples", "Ctrl,A1,A2,A3,Ctrl,B1,B2,B3"])
            await page.wait_for_timeout(3200)
        scenes.append(await rec.scene("labels", labels))

        async def export():
            button = page.get_by_role("link", name="PPTX").or_(page.get_by_role("button", name="PPTX")).first
            x, y = await centre(button)
            await glide(page, rec, x, y, pause=300)
            async with page.expect_download():
                await page.mouse.click(x, y)
            await page.wait_for_timeout(2200)
        scenes.append(await rec.scene("export", export))

        (OUT / "scenes.json").write_text(json.dumps({"fps": FPS, "width": VIEW["width"] * 2,
                                                     "height": VIEW["height"] * 2, "scenes": scenes}, indent=2))
        await browser.close()


def env_cli(project_dir, args):
    subprocess.run(["uv", "run", "wbm", *args, "--project", project_dir, "--json"], check=True,
                   capture_output=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
