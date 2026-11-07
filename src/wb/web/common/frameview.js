// Frame viewer shared by the local annotation app and the review.html
// (spec 11.1). One SVG per displayed frame:
//
//   svg viewBox = "0 0 <frame width> <frame height>"   (edge coordinates)
//     g.viewport  translate(tx ty) scale(k)            (zoom / pan only)
//       image     stretched to the frame box
//       g.overlays  polygons and points in frame coordinates
//
// The viewBox is the frame itself, so nothing about the CSS size, zoom, pan or
// devicePixelRatio can reach a coordinate: a screen point is mapped back with
// getScreenCTM().inverse() and is never rounded.
(function (global) {
  'use strict';

  const SVGNS = 'http://www.w3.org/2000/svg';
  const CLICK_DRAG_PX = 4;

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVGNS, tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value != null) node.setAttribute(name, value);
    }
    return node;
  }

  function pointsAttr(points) {
    return points.map((pt) => pt[0] + ',' + pt[1]).join(' ');
  }

  function identityViewport() {
    return { k: 1, tx: 0, ty: 0 };
  }

  function transformAttr(vp) {
    return 'translate(' + vp.tx + ' ' + vp.ty + ') scale(' + vp.k + ')';
  }

  // Keeps the zoomed frame covering the whole viewBox. Mutates and returns vp.
  function clampViewport(vp, width, height) {
    vp.tx = Math.min(0, Math.max(width * (1 - vp.k), vp.tx));
    vp.ty = Math.min(0, Math.max(height * (1 - vp.k), vp.ty));
    return vp;
  }

  // Zoom about p, a point in viewBox coordinates. Mutates and returns vp.
  function zoomViewportAt(vp, factor, p, limits) {
    const min = limits && limits.min != null ? limits.min : 1;
    const max = limits && limits.max != null ? limits.max : 16;
    const k = Math.min(max, Math.max(min, vp.k * factor));
    const ratio = k / vp.k;
    vp.tx = p.x - (p.x - vp.tx) * ratio;
    vp.ty = p.y - (p.y - vp.ty) * ratio;
    vp.k = k;
    return vp;
  }

  function create(opts) {
    const svg = svgEl('svg', {
      id: opts.id,
      class: 'wb-frameview',
      preserveAspectRatio: 'xMidYMid meet',
    });
    const viewportGroup = svgEl('g', { id: opts.viewportId });
    const image = svgEl('image', { id: opts.imageId, x: 0, y: 0, preserveAspectRatio: 'none' });
    const overlays = svgEl('g', { id: opts.overlaysId, class: 'overlays' });
    viewportGroup.append(image, overlays);
    svg.append(viewportGroup);

    let dragged = false;

    const view = {
      svg,
      viewport: viewportGroup,
      image,
      overlays,
      frameId: null,
      width: 0,
      height: 0,

      setFrame(frame) {
        view.frameId = frame.frameId;
        view.width = frame.width;
        view.height = frame.height;
        svg.setAttribute('viewBox', '0 0 ' + frame.width + ' ' + frame.height);
        svg.setAttribute('data-frame-id', frame.frameId);
        image.setAttribute('width', frame.width);
        image.setAttribute('height', frame.height);
        image.setAttribute('href', frame.href);
      },

      clearFrame() {
        view.frameId = null;
        svg.removeAttribute('data-frame-id');
        image.removeAttribute('href');
        view.clearOverlays();
      },

      applyViewport(vp, options) {
        if (options && options.clamp) clampViewport(vp, view.width, view.height);
        viewportGroup.setAttribute('transform', transformAttr(vp));
        return vp;
      },

      // Screen position -> viewBox coordinates (before zoom and pan).
      clientToViewBox(clientX, clientY) {
        const pt = svg.createSVGPoint();
        pt.x = clientX;
        pt.y = clientY;
        const q = pt.matrixTransform(svg.getScreenCTM().inverse());
        return { x: q.x, y: q.y };
      },

      // Screen position -> frame edge coordinates, through zoom and pan. Unrounded.
      clientToFrame(clientX, clientY) {
        const pt = svg.createSVGPoint();
        pt.x = clientX;
        pt.y = clientY;
        const q = pt.matrixTransform(viewportGroup.getScreenCTM().inverse());
        return [q.x, q.y];
      },

      frameContains(point) {
        return point[0] >= 0 && point[1] >= 0 && point[0] <= view.width && point[1] <= view.height;
      },

      clearOverlays() {
        overlays.replaceChildren();
      },

      addPolygon(points, attrs) {
        return view._add('polygon', { ...attrs, points: pointsAttr(points) });
      },

      addPolyline(points, attrs) {
        return view._add('polyline', { ...attrs, points: pointsAttr(points) });
      },

      addPoint(point, attrs) {
        const { r, ...rest } = attrs || {};
        return view._add('circle', { ...rest, cx: point[0], cy: point[1], r: r || 5 });
      },

      _add(tag, attrs) {
        const node = svgEl(tag, { 'vector-effect': 'non-scaling-stroke', ...attrs });
        overlays.append(node);
        return node;
      },

      // Wheel zoom and drag pan on a viewport object {k, tx, ty} owned by the
      // caller. onChange runs after every change. wasDragged() is true during
      // the click that ends a drag, so a drag is not taken for a selection.
      enablePanZoom(options) {
        svg.addEventListener('wheel', (e) => {
          e.preventDefault();
          const factor = e.deltaY < 0 ? 1.25 : 0.8;
          zoomViewportAt(options.getViewport(), factor, view.clientToViewBox(e.clientX, e.clientY), options);
          options.onChange();
        }, { passive: false });

        svg.addEventListener('pointerdown', (e) => {
          if (e.button !== 0) return;
          const start = { x: e.clientX, y: e.clientY };
          let last = view.clientToViewBox(e.clientX, e.clientY);
          dragged = false;
          const onMove = (m) => {
            if (Math.hypot(m.clientX - start.x, m.clientY - start.y) > CLICK_DRAG_PX) dragged = true;
            if (!dragged) return;
            const now = view.clientToViewBox(m.clientX, m.clientY);
            const vp = options.getViewport();
            vp.tx += now.x - last.x;
            vp.ty += now.y - last.y;
            options.onChange();
            last = view.clientToViewBox(m.clientX, m.clientY);
          };
          const onUp = () => {
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
            setTimeout(() => { dragged = false; }, 0);
          };
          window.addEventListener('pointermove', onMove);
          window.addEventListener('pointerup', onUp);
        });
      },

      wasDragged() {
        return dragged;
      },
    };

    if (opts.frame) view.setFrame(opts.frame);
    return view;
  }

  global.WbFrameView = {
    create,
    svgEl,
    pointsAttr,
    identityViewport,
    transformAttr,
    clampViewport,
    zoomViewportAt,
  };
})(window);
