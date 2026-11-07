"""PowerPoint and PNG writers for the shared figure layout."""

from __future__ import annotations

from io import BytesIO
import json
import math
import os
from pathlib import Path
import tempfile
import unicodedata
import warnings

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from wb.layout import compute_layout
from wb.model import Project
from wb import rendering

FONT_ROOTS = (Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts",
              Path("/usr/share/fonts"), Path("/usr/local/share/fonts"))
FONT_NAME = "Hiragino Sans"


def _atomic_write(path: Path, writer) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def _adoption(project: Project) -> dict:
    return {
        "project_id": project.project_id, "project_revision": project.project_revision,
        "panels": [{"panel_id": p.panel_id, "capture_id": p.selected_exposure_id,
                    "geometry_revision": p.display_geometry.geometry_revision if p.display_geometry else None}
                   for p in project.included_panels()],
        "display_mappings": {s.source_asset_id: s.display_mapping for s in project.sources
                             if getattr(s, "display_mapping", None)},
    }


def _image(project: Project, project_dir: Path, element: dict):
    if element.get("marker"):
        image = rendering.render_marker(
            project, project_dir, element["panel_id"],
            part=element.get("part"), rect=element.get("rect"),
        )
        # The existing renderer wraps markers with their registration status.
        if isinstance(image, dict):
            image = image["image"]
        if image is None:
            raise ValueError(f"marker image is unavailable: {element['panel_id']}")
        return image
    return rendering.render_panel(project, project_dir, element["panel_id"], element["capture_id"])


def _text_frame(frame, element: dict) -> None:
    frame.clear()
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                           "right": PP_ALIGN.RIGHT}[element.get("align", "left")]
    run = paragraph.add_run()
    run.text = element["text"]
    run.font.name = FONT_NAME
    run.font.size = Pt(element.get("font_size", 14))
    run.font.bold = element.get("bold", False)
    run.font.italic = element.get("italic", False)
    run.font.color.rgb = RGBColor.from_string(element.get("color", "000000"))
    # DrawingML needs the East Asian face as well as the Latin font name.
    east_asian = OxmlElement("a:ea")
    east_asian.set("typeface", FONT_NAME)
    run._r.get_or_add_rPr().append(east_asian)


def _table(slide, element: dict) -> None:
    table = slide.shapes.add_table(
        1, element["columns"] + 1,
        *(Inches(element[k]) for k in ("x", "y", "w", "h")),
    ).table
    table.first_row = False
    table.horz_banding = False
    table.columns[0].width = Inches(element["label_w"])
    widths = element.get("column_widths") or [element["column_w"]] * element["columns"]
    for i, name in enumerate([""] + element["names"]):
        if i:
            table.columns[i].width = Inches(widths[i - 1])
        cell = table.cell(0, i)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor(255, 255, 255)
        _text_frame(cell.text_frame, {"text": name, "font_size": element["font_size"], "align": "center"})
        properties = cell._tc.get_or_add_tcPr()
        for side in ("L", "R", "T", "B"):
            line = OxmlElement(f"a:ln{side}")
            line.set("w", "9525")  # 0.75 pt in EMUs, matching the PNG table rule.
            sample = i and i - 1 not in element.get("gap_columns", [])
            fill = OxmlElement("a:solidFill" if sample else "a:noFill")
            if sample:
                color = OxmlElement("a:srgbClr")
                color.set("val", "808080")
                fill.append(color)
            line.append(fill)
            properties.append(line)
    table.rows[0].height = Inches(element["h"])


def export_pptx(project: Project, project_dir, out_path) -> Path:
    """Write editable labels/tables and exact rendered PNG picture parts atomically."""
    layout = compute_layout(project, pptx_markers=True)
    presentation = Presentation()
    presentation.slide_width = Inches(layout["slide"]["w"])
    presentation.slide_height = Inches(layout["slide"]["h"])
    presentation.core_properties.title = "ウェスタンブロット図"
    adoption = _adoption(project)
    presentation.core_properties.subject = json.dumps({"project_revision": project.project_revision})
    for page in layout["pages"]:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        for element in page["elements"]:
            bounds = [Inches(element[k]) for k in ("x", "y", "w", "h")]
            if element["kind"] == "image":
                image = _image(project, Path(project_dir), element)
                slide.shapes.add_picture(BytesIO(image.png), *bounds)
            elif element["kind"] == "table":
                _table(slide, element)
            else:
                if element["kind"] == "placeholder":
                    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, *bounds)
                    shape.fill.background()
                    shape.line.color.rgb = RGBColor.from_string("999999")
                    shape.line.width = Pt(1)
                else:
                    shape = slide.shapes.add_textbox(*bounds)
                _text_frame(shape.text_frame, element)
        notes = [f"project_id: {project.project_id}", f"project_revision: {project.project_revision}"]
        for panel in adoption["panels"]:
            notes.append(f"{panel['panel_id']}: exposure={panel['capture_id']} "
                         f"geometry_revision={panel['geometry_revision']}")
        if adoption["display_mappings"]:
            notes.append("raw16 display mappings: " + json.dumps(adoption["display_mappings"], sort_keys=True))
        slide.notes_slide.notes_text_frame.text = "\n".join(notes)
    return _atomic_write(Path(out_path), presentation.save)


def _find_font() -> tuple[str | None, bool]:
    candidates = []
    fallback = []
    for root in FONT_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in (".ttf", ".ttc", ".otf"):
                continue
            name = unicodedata.normalize("NFC", path.name)
            if "ヒラギノ角ゴシック W3" in name or "Hiragino Sans." in name:
                candidates.append((0, str(path)))
            elif "ヒラギノ" in name or "Hiragino" in name:
                candidates.append((1, str(path)))
            elif any(n in name for n in ("NotoSansCJK", "NotoSansJP", "NotoSerifCJK", "YuGoth", "Meiryo")):
                candidates.append((2, str(path)))
            elif any(n in name for n in ("DejaVuSans", "Helvetica", "Arial")):
                fallback.append(str(path))
    for _, path in sorted(candidates):
        try:
            ImageFont.truetype(path, 14)
            return path, True
        except OSError:
            continue
    for path in sorted(fallback):
        try:
            ImageFont.truetype(path, 14)
            return path, False
        except OSError:
            continue
    return None, False


def _draw_text(canvas: Image.Image, element: dict, dpi: float, font_path: str | None) -> None:
    size = max(1, round(element.get("font_size", 14) * dpi / 72))
    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size=size)
    width = max(1, round(element["w"] * dpi))
    height = max(1, round(element["h"] * dpi))
    layer = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(layer)
    align = element.get("align", "left")
    x, anchor = {"left": (0, "lm"), "center": (width / 2, "mm"), "right": (width, "rm")}[align]
    draw.text((x, height / 2), element["text"], font=font, fill="#" + element.get("color", "000000"),
              anchor=anchor, stroke_width=max(1, round(dpi / 300)) if element.get("bold") else 0)
    canvas.paste(layer, (round(element["x"] * dpi), round(element["y"] * dpi)), layer)


def _raster_page(project, project_dir, layout, page, dpi, font_path) -> Image.Image:
    width = round(layout["slide"]["w"] * dpi)
    height = round(layout["slide"]["h"] * dpi)
    pixels = np.full((height, width, 3), 255, dtype=np.uint8)
    text = []
    for element in page["elements"]:
        x, y, w, h = [round(element[k] * dpi) for k in ("x", "y", "w", "h")]
        if element["kind"] == "image":
            image = _image(project, project_dir, element)
            decoded = cv2.imdecode(np.frombuffer(image.png, dtype=np.uint8), cv2.IMREAD_COLOR)
            if decoded is None:
                raise ValueError(f"invalid rendered PNG: {element['panel_id']}")
            rgb = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
            pixels[y:y + h, x:x + w] = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_LINEAR)
        elif element["kind"] == "table":
            widths = element.get("column_widths") or [element["column_w"]] * element["columns"]
            edges = [element["sample_x"]]
            for w in widths:
                edges.append(edges[-1] + w)
            for i, name in enumerate(element["names"]):
                if i in element.get("gap_columns", []):
                    continue
                x0, x1 = round(edges[i] * dpi), round(edges[i + 1] * dpi)
                cv2.rectangle(pixels, (x0, y), (x1, y + h), (128, 128, 128), max(1, round(0.75 * dpi / 72)))
                text.append({**element, "text": name, "x": edges[i], "w": edges[i + 1] - edges[i], "align": "center"})
        else:
            if element["kind"] == "placeholder":
                cv2.rectangle(pixels, (x, y), (x + w, y + h), (153, 153, 153), max(1, round(dpi / 72)))
            text.append(element)
    canvas = Image.fromarray(pixels)
    for element in text:
        _draw_text(canvas, element, dpi, font_path)
    return canvas


def export_png(project: Project, project_dir, out_path, dpi: float = 300) -> Path:
    """Write the first page to out_path; further pages use ``<stem>-p2.png``, etc.

    Return the first page's path. PNG text metadata records adoption,
    page count and the selected font. Missing CJK fonts produce a warning, not a failure.
    """
    if not math.isfinite(dpi) or dpi <= 0:
        raise ValueError("dpi must be positive and finite")
    layout = compute_layout(project)
    if round(layout["slide"]["w"] * dpi) < 1 or round(layout["slide"]["h"] * dpi) < 1:
        raise ValueError("dpi is too small for a PNG page")
    font_path, cjk = _find_font()
    font_warning = "CJK font not found; Japanese text may be unavailable in the PNG."
    if not cjk:
        warnings.warn(font_warning, RuntimeWarning, stacklevel=2)
    path = Path(out_path)
    for page in layout["pages"]:
        image = _raster_page(project, Path(project_dir), layout, page, dpi, font_path)
        metadata = PngImagePlugin.PngInfo()
        metadata.add_itxt("wb_output", json.dumps({
            **_adoption(project), "page_index": page["index"], "page_count": len(layout["pages"]),
            "font": font_path, "font_warning": None if cjk else font_warning,
        }, ensure_ascii=False))
        destination = path if page["index"] == 0 else path.with_name(f"{path.stem}-p{page['index'] + 1}.png")
        _atomic_write(destination, lambda stream: image.save(stream, format="PNG", pnginfo=metadata, dpi=(dpi, dpi)))
    return path
