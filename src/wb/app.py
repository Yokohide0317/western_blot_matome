"""Local annotation server."""

from pathlib import Path
import inspect
import json
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from wb import api, project, rendering
from wb.geometry import fit_lane_centers, lane_crop_x
from wb.readiness import REASON_TEXT

WEB_DIR = Path(__file__).parent / "web"

# POST /api/<name> -> api function. Each takes expected_revision and returns the saved project.
OPERATIONS = {
    "start_quantification": api.start_quantification,
    "place_quantification": api.place_quantification,
    "update_quantification": api.update_quantification,
    "compute_quantification": api.compute_quantification,
    "set_panel_included": api.set_panel_included,
    "set_membrane_included": api.set_membrane_included,
    "duplicate_panel": api.duplicate_panel,
    "delete_panel": api.delete_panel,
    "add_manual_membrane": api.add_manual_membrane,
    "add_panel_selection": api.add_panel_selection,
    "reorder_panels": api.reorder_panels,
    "set_lanes": api.set_lanes,
    "set_figure_scale": api.set_figure_scale,
    "set_lane_boundaries": api.set_lane_boundaries,
    "apply_proposal": api.apply_proposal,
    "rename_panel": api.rename_panel,
    "import_metadata": api.import_metadata,
    "add_region": api.add_region,
    "set_control_for": api.set_control_for,
    "set_sample_names": api.set_sample_names,
    "add_marker_registration": api.add_marker_registration,
    "confirm_identity_alignment": api.confirm_identity_alignment,
    "update_region": api.update_region,
    "update_display_geometry": api.update_display_geometry,
    "save_panel_edit": api.save_panel_edit,
    "add_annotation": api.add_annotation,
    "confirm_annotations": api.confirm_annotations,
    "set_selected_exposure": api.set_selected_exposure,
    "set_marker_x": api.set_marker_x,
}


def create_app(project_dir):
    location = project.locate(project_dir)
    root = location.parent
    app = FastAPI()

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={"detail": "リクエストの形式を確認してください"})


    @app.get("/common/labels.js")
    def labels_script():
        prefix = "window.WB_REASON_TEXT = " + json.dumps(REASON_TEXT, ensure_ascii=False) + ";\n"
        return Response(prefix + (WEB_DIR / "common/labels.js").read_text(encoding="utf-8"), media_type="text/javascript")

    app.mount("/common", StaticFiles(directory=WEB_DIR / "common"), name="common")

    def current_revision():
        return api._load(location).project_revision

    def raise_http(exc):
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, project.RevisionConflict):
            raise HTTPException(
                409,
                detail={
                    "message": "版が変わりました。読み直してください。" + str(exc),
                    "current_revision": current_revision(),
                    "project_revision": current_revision(),
                },
            )
        if isinstance(exc, (ValueError, TypeError, KeyError, StopIteration)):
            raise HTTPException(422, detail="入力を確認してください: " + (str(exc) or "IDが見つかりません"))
        raise exc

    @app.get("/")
    def index():
        dist = WEB_DIR / "app/dist/index.html"
        return FileResponse(dist)

    @app.get("/legacy/")
    def legacy():
        return RedirectResponse("/")

    @app.get("/api/revision")
    def revision():
        return {"project_revision": current_revision()}

    @app.get("/api/server")
    def server_identity():
        return {"pid": os.getpid(), "project_path": str(location.resolve())}

    @app.get("/api/status")
    def status():
        return api.status(location)

    @app.get("/api/layout")
    def layout():
        try:
            return api.layout(location)
        except Exception as exc:
            raise_http(exc)

    @app.post("/api/export")
    async def export(request: Request):
        try:
            data = await request.json()
            return api.export_project(location, **data)
        except Exception as exc:
            raise_http(exc)

    @app.get("/api/download/{name}")
    def download(name: str):
        try:
            path = api.export_file(location, name)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))
        except Exception as exc:
            raise_http(exc)
        types = {".png": "image/png", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
        return FileResponse(path, filename=path.name, media_type=types[path.suffix.lower()])

    @app.get("/api/project")
    def get_project():
        return api._load(location).model_dump(mode="json")

    @app.get("/api/validate")
    def validate():
        return api.validate(location)

    def quantification_context(group_id):
        from wb import quantification as Q
        p = api._load(location)
        return Q, p, Q.find(p.quantification_groups, 'group_id', group_id)

    @app.get('/api/quantification_info/{group_id}')
    def quantification_info(group_id: str):
        try:
            Q, p, g = quantification_context(group_id)
            return Q.current(p, location.parent, g)
        except Exception as exc:
            raise_http(exc)

    @app.get('/api/quantification_source/{group_id}.png')
    def quantification_source(group_id: str):
        try:
            import io
            import numpy as np
            from PIL import Image
            Q, p, g = quantification_context(group_id)
            a = Q.load(p, location.parent, g).astype(np.float64)
            lo, hi = float(a.min()), float(a.max())
            a = ((a-lo)*255/(hi-lo)).astype('uint8') if hi > lo else np.zeros(a.shape, dtype='uint8')
            out = io.BytesIO()
            Image.fromarray(a).save(out, format='PNG')
            return Response(out.getvalue(), media_type='image/png')
        except Exception as exc:
            raise_http(exc)

    @app.get('/api/quantification_csv/{group_id}')
    def quantification_csv(group_id: str):
        try:
            Q, p, g = quantification_context(group_id)
            return Response(Q.csv_bytes(p, location.parent, g), media_type='text/csv',
                            headers={'Content-Disposition': 'attachment; filename="quantification.csv"'})
        except Exception as exc:
            raise_http(exc)

    @app.get('/api/quantification_excel/{group_id}')
    def quantification_excel(group_id: str):
        try:
            Q, p, g = quantification_context(group_id)
            return Response(Q.excel(p, location.parent, g),
                            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            headers={'Content-Disposition': 'attachment; filename="quantification.xlsx"'})
        except Exception as exc:
            raise_http(exc)

    # Read-only helpers are declared before the generic /api/{operation} route.

    @app.post("/api/fit_lanes")
    async def fit_lanes(request: Request):
        try:
            data = await request.json()
            known = {int(k): float(v) for k, v in data["known"].items()}
            centers = fit_lane_centers(data["lane_count"], known)
            return {
                "centers": centers,
                "residuals": {str(k): v - centers[k - 1] for k, v in known.items()},
                "crop_x": lane_crop_x(centers),
            }
        except Exception as exc:
            raise_http(exc)

    @app.post("/api/fit_marker")
    async def fit_marker(request: Request):
        try:
            data = await request.json()
            return api.fit_marker_landmarks(data["marker_points"], data["reference_points"])
        except Exception as exc:
            raise_http(exc)

    @app.post("/api/{operation}")
    async def mutate(operation: str, request: Request):
        function = OPERATIONS.get(operation)
        if function is None:
            raise HTTPException(404, "操作が見つかりません")
        try:
            payload = await request.json()
            if "expected_revision" not in payload:
                raise ValueError("expected_revisionが必要です")
            current = current_revision()
            if payload["expected_revision"] != current:
                raise project.RevisionConflict(f"current revision {current}")
            inspect.signature(function).bind(location, **payload)
            return function(location, **payload).model_dump(mode="json")
        except Exception as exc:
            raise_http(exc)

    @app.get("/api/image/frame/{frame_id}.png")
    def frame_image(frame_id: str, max_width: int | None = None):
        try:
            current = api._load(location)
            frame = api._get(current.frames, "frame_id", frame_id)
            image, _ = rendering.render_overview(
                current, root, frame_id, max_width=frame.width if max_width is None else max_width
            )
            return Response(image.png, media_type="image/png")
        except Exception as exc:
            raise_http(exc)

    @app.get("/api/image/overview/{frame_id}.png")
    def overview(frame_id: str, max_width: int = 1200):
        try:
            image, _ = rendering.render_overview(api._load(location), root, frame_id, max_width=max_width)
            return Response(image.png, media_type="image/png")
        except Exception as exc:
            raise_http(exc)

    @app.get("/api/image/panel/{panel_id}/{capture_id}.png")
    def panel_image(panel_id: str, capture_id: str):
        try:
            image = rendering.render_panel(api._load(location), root, panel_id, capture_id)
            return Response(image.png, media_type="image/png")
        except Exception as exc:
            raise_http(exc)

    @app.get("/api/image/marker/{panel_id}.png")
    def marker_image(panel_id: str):
        try:
            result = rendering.render_marker(api._load(location), root, panel_id)
            if not result["image"]:
                raise HTTPException(404, result["status"])
            return Response(result["image"].png, media_type="image/png")
        except Exception as exc:
            raise_http(exc)

    dist = WEB_DIR / "app/dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="app")
    return app


def main(project_dir, port=8765):
    import uvicorn

    uvicorn.run(create_app(project_dir), host="127.0.0.1", port=port)
