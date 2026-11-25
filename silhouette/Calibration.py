"""Core scanner-based calibration math, metadata persistence, and marker utilities.

Uses only libraries bundled with Inkscape's Python (inkex, lxml, numpy, PIL).
"""

import json
import os
import struct
import numpy as np
from lxml import etree

CALIB_NS = "http://inkscape-silhouette.org/calibration"
CALIB_PREFIX = "silhouette"
CALIB_LAYER_ID = "silhouette-calib-markers"
CALIB_LAYER_LABEL = "Calibration Markers"
CALIB_METADATA_ID = "metadata-silhouette-calib"
CALIB_TAG = f"{{{CALIB_NS}}}calibration"
CALIB_POINT_TAG = f"{{{CALIB_NS}}}point"

DEFAULT_OFFSET_X_MM = 1.74
DEFAULT_OFFSET_Y_MM = 2.43


def get_config_path():
    """Return path to the persistent user calibration config JSON."""
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        cfg_dir = os.path.join(base, "inkscape")
    else:
        cfg_dir = os.path.join(os.path.expanduser("~"), ".config", "inkscape")
    return os.path.join(cfg_dir, "silhouette_calib_config.json")


def load_user_config():
    """Load saved hardware offset and UI preferences."""
    defaults = {
        "offset_x_mm": DEFAULT_OFFSET_X_MM,
        "offset_y_mm": DEFAULT_OFFSET_Y_MM,
        "last_scan_dir": "",
    }
    path = get_config_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                defaults.update(data)
    except Exception:
        pass
    return defaults


def save_user_config(config):
    """Persist hardware offset and UI preferences to user config JSON."""
    path = get_config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
    except Exception:
        pass


def get_document_size_mm(svg):
    """Return (width_mm, height_mm) of the Inkscape SVG document."""
    from inkex.units import convert_unit
    w_mm = convert_unit(svg.viewport_width, "mm")
    h_mm = convert_unit(svg.viewport_height, "mm")
    return float(w_mm), float(h_mm)


def mm_to_svg_units(svg, value_mm):
    """Convert millimeters into the SVG document's user/viewport units."""
    return float(svg.viewport_to_unit(f"{value_mm}mm", "px")) if not hasattr(svg, "unittouu") else float(svg.unittouu(f"{value_mm}mm"))


def svg_units_to_mm(svg, value_uu):
    """Convert SVG user/viewport units into millimeters."""
    if hasattr(svg, "uutounit"):
        return float(svg.uutounit(value_uu, "mm"))
    return float(svg.unit_to_viewport(value_uu, "mm"))


def create_marker_group(svg, marker_id, cx_mm, cy_mm, diameter_mm=2.5):
    """Build a crisp bullseye calibration marker <g> centered at (cx_mm, cy_mm)."""
    from inkex import Group, Circle, PathElement, TextElement

    cx = mm_to_svg_units(svg, cx_mm)
    cy = mm_to_svg_units(svg, cy_mm)
    r_outer = mm_to_svg_units(svg, diameter_mm / 2.0)
    r_inner = mm_to_svg_units(svg, diameter_mm * 0.18)
    stroke_w = mm_to_svg_units(svg, 0.25)
    cross_arm = mm_to_svg_units(svg, diameter_mm * 0.75)

    group = Group()
    group.set("id", f"calib-marker-{marker_id}")
    group.set("data-calib-marker", "true")
    group.set("data-calib-id", str(marker_id))

    ring = Circle(cx=str(cx), cy=str(cy), r=str(r_outer))
    ring.set("style", f"fill:none;stroke:#000000;stroke-width:{stroke_w};")
    group.add(ring)

    dot = Circle(cx=str(cx), cy=str(cy), r=str(r_inner))
    dot.set("style", "fill:#000000;stroke:none;")
    group.add(dot)

    cross = PathElement()
    cross.set(
        "d",
        f"M {cx - cross_arm},{cy} H {cx + cross_arm} M {cx},{cy - cross_arm} V {cy + cross_arm}",
    )
    cross.set("style", f"fill:none;stroke:#000000;stroke-width:{stroke_w * 0.6};")
    group.add(cross)

    label = TextElement()
    label.set("x", str(cx + mm_to_svg_units(svg, diameter_mm * 0.9)))
    label.set("y", str(cy - mm_to_svg_units(svg, diameter_mm * 0.4)))
    label.set("style", f"font-size:{mm_to_svg_units(svg, 1.8)}px;fill:#000000;font-family:sans-serif;")
    label.text = f"#{marker_id}"
    group.add(label)

    return group


def ensure_marker_layer(svg, clear_existing=False):
    """Return the dedicated Calibration Markers layer, creating it if needed."""
    from inkex import Layer

    layer = svg.getElementById(CALIB_LAYER_ID)
    if layer is not None and clear_existing:
        for child in list(layer):
            layer.remove(child)
    if layer is None:
        layer = Layer.new(CALIB_LAYER_LABEL)
        layer.set("id", CALIB_LAYER_ID)
        svg.add(layer)
    return layer


def place_default_markers(svg, margin_mm=6.5, diameter_mm=2.5, clear_existing=True):
    """Insert 4 corner calibration bullseyes into the Calibration Markers layer."""
    width_mm, height_mm = get_document_size_mm(svg)
    layer = ensure_marker_layer(svg, clear_existing=clear_existing)

    corners = [
        ("1", margin_mm, margin_mm),
        ("2", width_mm - margin_mm, margin_mm),
        ("3", margin_mm, height_mm - margin_mm),
        ("4", width_mm - margin_mm, height_mm - margin_mm),
    ]
    for marker_id, cx_mm, cy_mm in corners:
        layer.add(create_marker_group(svg, marker_id, cx_mm, cy_mm, diameter_mm=diameter_mm))
    return corners


def get_svg_markers(svg):
    """Discover all calibration markers in the SVG and return their live (mm) centers."""
    from inkex import Circle

    layer = svg.getElementById(CALIB_LAYER_ID)
    candidates = []
    if layer is not None:
        for child in layer:
            if child.get("data-calib-marker") == "true" or isinstance(child, Circle):
                candidates.append(child)
    else:
        for elem in svg.iter():
            if elem.get("data-calib-marker") == "true":
                candidates.append(elem)

    markers = []
    seen_ids = set()
    auto_idx = 1
    for node in candidates:
        # Prefer the outer ring circle inside a marker group for an exact center
        target_geom = None
        for sub in node.iter():
            if isinstance(sub, Circle):
                target_geom = sub
                break
        bbox = (target_geom if target_geom is not None else node).bounding_box(transform=True)
        if bbox is None:
            continue
        cx_mm = svg_units_to_mm(svg, bbox.center_x)
        cy_mm = svg_units_to_mm(svg, bbox.center_y)

        raw_id = node.get("data-calib-id") or ""
        if not raw_id or raw_id in seen_ids:
            while str(auto_idx) in seen_ids:
                auto_idx += 1
            raw_id = str(auto_idx)
        seen_ids.add(raw_id)
        markers.append({"id": raw_id, "svg_x": cx_mm, "svg_y": cy_mm})

    def _sort_key(item):
        try:
            return (0, int(item["id"]))
        except ValueError:
            return (1, item["id"])

    markers.sort(key=_sort_key)
    return markers


def save_calibration(
    svg,
    points,
    model="homography",
    offset_x_mm=0.0,
    offset_y_mm=0.0,
    dpi_x=300.0,
    dpi_y=300.0,
    rms_error_mm=0.0,
    active=True,
):
    """Write calibration metadata non-destructively into SVG <metadata>."""
    clear_calibration(svg, remove_markers=False)

    meta = svg.find("{http://www.w3.org/2000/svg}metadata")
    if meta is None:
        meta = etree.Element("{http://www.w3.org/2000/svg}metadata")
        meta.set("id", CALIB_METADATA_ID)
        svg.insert(0, meta)

    calib_el = etree.SubElement(meta, CALIB_TAG, nsmap={CALIB_PREFIX: CALIB_NS})
    calib_el.set("version", "1")
    calib_el.set("active", "true" if active else "false")
    calib_el.set("model", str(model))
    calib_el.set("offset_x_mm", f"{float(offset_x_mm):.4f}")
    calib_el.set("offset_y_mm", f"{float(offset_y_mm):.4f}")
    calib_el.set("scan_dpi_x", f"{float(dpi_x):.2f}")
    calib_el.set("scan_dpi_y", f"{float(dpi_y):.2f}")
    calib_el.set("rms_error_mm", f"{float(rms_error_mm):.4f}")

    for pt in points:
        pt_el = etree.SubElement(calib_el, CALIB_POINT_TAG)
        pt_el.set("id", str(pt["id"]))
        pt_el.set("svg_x", f"{float(pt['svg_x']):.4f}")
        pt_el.set("svg_y", f"{float(pt['svg_y']):.4f}")
        pt_el.set("scan_x", f"{float(pt['scan_x']):.4f}")
        pt_el.set("scan_y", f"{float(pt['scan_y']):.4f}")


def load_calibration(svg):
    """Read active calibration metadata from SVG <metadata>, or return None."""
    calib_el = None
    for elem in svg.iter(CALIB_TAG):
        calib_el = elem
        break
    if calib_el is None:
        return None
    if calib_el.get("active", "false").lower() != "true":
        return None

    points = []
    for pt_el in calib_el.findall(CALIB_POINT_TAG):
        try:
            points.append({
                "id": pt_el.get("id", str(len(points) + 1)),
                "svg_x": float(pt_el.get("svg_x")),
                "svg_y": float(pt_el.get("svg_y")),
                "scan_x": float(pt_el.get("scan_x")),
                "scan_y": float(pt_el.get("scan_y")),
            })
        except (TypeError, ValueError):
            continue

    if len(points) < 3:
        return None

    try:
        return {
            "active": True,
            "model": calib_el.get("model", "homography"),
            "offset_x_mm": float(calib_el.get("offset_x_mm", 0.0)),
            "offset_y_mm": float(calib_el.get("offset_y_mm", 0.0)),
            "scan_dpi_x": float(calib_el.get("scan_dpi_x", 300.0)),
            "scan_dpi_y": float(calib_el.get("scan_dpi_y", 300.0)),
            "rms_error_mm": float(calib_el.get("rms_error_mm", 0.0)),
            "points": points,
        }
    except (TypeError, ValueError):
        return None


def clear_calibration(svg, remove_markers=False):
    """Remove calibration metadata safely without touching user artwork layers."""
    for calib_el in list(svg.iter(CALIB_TAG)):
        parent = calib_el.getparent()
        if parent is not None:
            parent.remove(calib_el)
    if remove_markers:
        layer = svg.getElementById(CALIB_LAYER_ID)
        if layer is not None and layer.getparent() is not None:
            layer.getparent().remove(layer)


def _normalize_points(pts):
    """Hartley normalization for numerically stable homography estimation."""
    pts = np.asarray(pts, dtype=float)
    mean = pts.mean(axis=0)
    shifted = pts - mean
    dist = np.mean(np.sqrt(np.sum(shifted ** 2, axis=1)))
    scale = np.sqrt(2.0) / dist if dist > 1e-12 else 1.0
    T = np.array([
        [scale, 0.0, -scale * mean[0]],
        [0.0, scale, -scale * mean[1]],
        [0.0, 0.0, 1.0],
    ], dtype=float)
    norm_pts = (T @ np.column_stack([pts, np.ones(len(pts))]).T).T[:, :2]
    return norm_pts, T


def fit_affine(src, dst):
    """Fit least-squares 2D affine transform mapping src -> dst."""
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    X = np.column_stack([src, np.ones(len(src))])
    A, _, _, _ = np.linalg.lstsq(X, dst, rcond=None)
    return A


def apply_affine(pts, A):
    """Apply fitted affine matrix A (3x2) to (N, 2) points."""
    pts = np.asarray(pts, dtype=float)
    X = np.column_stack([pts, np.ones(len(pts))])
    return X @ A


def fit_homography(src, dst):
    """Fit normalized DLT 3x3 projective homography mapping src -> dst."""
    src_n, T_src = _normalize_points(src)
    dst_n, T_dst = _normalize_points(dst)
    rows = []
    for (x, y), (u, v) in zip(src_n, dst_n):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y, -u])
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y, -v])
    _, _, Vt = np.linalg.svd(np.asarray(rows, dtype=float))
    H_n = Vt[-1].reshape(3, 3)
    H = np.linalg.inv(T_dst) @ H_n @ T_src
    if abs(H[2, 2]) > 1e-12:
        H = H / H[2, 2]
    return H


def apply_homography(pts, H):
    """Apply 3x3 homography H to (N, 2) points."""
    pts = np.asarray(pts, dtype=float)
    hom = (H @ np.column_stack([pts, np.ones(len(pts))]).T).T
    return hom[:, :2] / hom[:, 2:3]


def _tps_kernel(r2):
    """Thin-Plate Spline radial basis kernel U(r) = r^2 * log(r^2)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        val = np.where(r2 > 1e-18, r2 * np.log(r2), 0.0)
    return val


def fit_tps(src, dst, reg=1e-6):
    """Fit a 2D Thin-Plate Spline warp mapping src -> dst."""
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    n = len(src)
    diff = src[:, None, :] - src[None, :, :]
    r2 = np.sum(diff ** 2, axis=2)
    K = _tps_kernel(r2) + reg * np.eye(n)
    P = np.column_stack([np.ones(n), src])
    L = np.zeros((n + 3, n + 3), dtype=float)
    L[:n, :n] = K
    L[:n, n:] = P
    L[n:, :n] = P.T
    Y = np.vstack([dst, np.zeros((3, 2), dtype=float)])
    params = np.linalg.solve(L, Y)
    return {"ctrl": src, "weights": params[:n], "affine": params[n:]}


def apply_tps(pts, tps_model):
    """Evaluate a fitted Thin-Plate Spline warp on (N, 2) points."""
    pts = np.asarray(pts, dtype=float)
    ctrl = tps_model["ctrl"]
    diff = pts[:, None, :] - ctrl[None, :, :]
    r2 = np.sum(diff ** 2, axis=2)
    K = _tps_kernel(r2)
    P = np.column_stack([np.ones(len(pts)), pts])
    return K @ tps_model["weights"] + P @ tps_model["affine"]


def build_transform(points, model="homography"):
    """Select and fit the appropriate 2D warp from calibration point pairs."""
    src = np.array([[p["svg_x"], p["svg_y"]] for p in points], dtype=float)
    dst = np.array([[p["scan_x"], p["scan_y"]] for p in points], dtype=float)
    n = len(src)
    if n < 3:
        raise ValueError("At least 3 calibration points are required.")

    chosen = model.lower()
    if n == 3 or chosen == "affine":
        return "affine", fit_affine(src, dst)
    if chosen == "tps" and n >= 4:
        return "tps", fit_tps(src, dst)
    return "homography", fit_homography(src, dst)


def evaluate_transform(pts, model_name, model_data, offset_x_mm=0.0, offset_y_mm=0.0):
    """Map (N, 2) SVG mm points to physical mat mm coordinates."""
    if model_name == "affine":
        out = apply_affine(pts, model_data)
    elif model_name == "tps":
        out = apply_tps(pts, model_data)
    else:
        out = apply_homography(pts, model_data)
    return out + np.array([float(offset_x_mm), float(offset_y_mm)], dtype=float)


def compute_rms_error(points, model="homography"):
    """Return (rms_mm, max_mm) residual error across calibration points."""
    src = np.array([[p["svg_x"], p["svg_y"]] for p in points], dtype=float)
    dst = np.array([[p["scan_x"], p["scan_y"]] for p in points], dtype=float)
    model_name, model_data = build_transform(points, model=model)
    pred = evaluate_transform(src, model_name, model_data, 0.0, 0.0)
    err = np.linalg.norm(dst - pred, axis=1)
    return float(np.sqrt(np.mean(err ** 2))), float(np.max(err))


def warp_paths(paths, calib):
    """Warp a list of cut polylines [[(x, y), ...], ...] in memory."""
    if not calib or not paths:
        return paths
    model_name, model_data = build_transform(calib["points"], model=calib.get("model", "homography"))
    ox = float(calib.get("offset_x_mm", 0.0))
    oy = float(calib.get("offset_y_mm", 0.0))

    warped_paths = []
    for path in paths:
        if not path:
            continue
        arr = np.asarray(path, dtype=float)
        mapped = evaluate_transform(arr, model_name, model_data, ox, oy)
        warped_paths.append([(float(x), float(y)) for x, y in mapped])
    return warped_paths


def extract_preview_cut_paths_mm(svg):
    """Extract flattened cut-layer polylines in mm for live overlay preview."""
    from inkex import Group, PathElement, Rectangle, Circle, Ellipse, Line, Polyline, Polygon
    from inkex.bezier import cspsubdiv

    shape_types = (PathElement, Rectangle, Circle, Ellipse, Line, Polyline, Polygon)
    paths_mm = []

    def _walk(parent, visible=True):
        for node in parent:
            style = getattr(node, "style", None)
            if style is not None:
                if style.get("display", node.get("display", "inline")) == "none":
                    continue
                try:
                    if float(style.get("opacity", node.get("opacity", 1.0))) == 0.0:
                        continue
                except (TypeError, ValueError):
                    pass
                vis = style.get("visibility", node.get("visibility", "inherit"))
                node_vis = visible if vis == "inherit" else (vis not in ("hidden", "collapse"))
            else:
                node_vis = visible

            if isinstance(node, Group):
                if node.get("id") == CALIB_LAYER_ID:
                    continue
                label = (node.label or "").lower()
                if any(skip in label for skip in ("cuttingmat", "regmark", "calibration", "print")):
                    continue
                _walk(node, node_vis)
            elif isinstance(node, shape_types) and node_vis:
                try:
                    pe = node.to_path_element()
                    csp = pe.path.transform(node.composed_transform()).to_superpath()
                    cspsubdiv(csp, 0.5)
                    for sub in csp:
                        pts = [(svg_units_to_mm(svg, pt[1][0]), svg_units_to_mm(svg, pt[1][1])) for pt in sub]
                        if len(pts) >= 2:
                            paths_mm.append(pts)
                except Exception:
                    continue

    _walk(svg, True)
    return paths_mm


def read_image_dpi(path, default_dpi=300.0):
    """Read (dpi_x, dpi_y) from image metadata or JFIF header."""
    from PIL import Image

    try:
        with Image.open(path) as img:
            dpi = img.info.get("dpi")
            if dpi and len(dpi) >= 2 and dpi[0] > 1 and dpi[1] > 1:
                return float(dpi[0]), float(dpi[1])
    except Exception:
        pass

    try:
        with open(path, "rb") as handle:
            header = handle.read(16384)
        idx = header.find(b"JFIF\x00")
        if idx != -1:
            units = header[idx + 7]
            xd, yd = struct.unpack(">HH", header[idx + 8 : idx + 12])
            if units == 1 and xd > 1 and yd > 1:
                return float(xd), float(yd)
            if units == 2 and xd > 1 and yd > 1:
                return float(xd) * 2.54, float(yd) * 2.54
    except Exception:
        pass

    return float(default_dpi), float(default_dpi)


def compute_rotation_deg(points):
    """Estimate overall sheet rotation angle in degrees (SVG -> scan) from point pairs."""
    if not points or len(points) < 3:
        return 0.0
    src = np.array([[p["svg_x"], p["svg_y"]] for p in points], dtype=float)
    dst = np.array([[p["scan_x"], p["scan_y"]] for p in points], dtype=float)
    A = fit_affine(src, dst)
    # Linear 2x2 part maps [dx, dy] -> [dx, dy] @ A[:2, :]
    M = A[:2, :].T
    angle_rad = np.arctan2(M[1, 0], M[0, 0])
    return float(np.degrees(angle_rad))


def autodetect_marker_center(gray, approx_x_px, approx_y_px, search_radius_px=45, marker_diameter_px=None):
    """Locate the sub-pixel bullseye center near (approx_x_px, approx_y_px).

    Uses a compact local darkness window around the strongest bullseye peak so
    nearby text labels or page edges do not pull the centroid off-center.
    """
    h, w = gray.shape
    cx0 = int(round(approx_x_px))
    cy0 = int(round(approx_y_px))
    x0 = max(0, cx0 - search_radius_px)
    x1 = min(w, cx0 + search_radius_px + 1)
    y0 = max(0, cy0 - search_radius_px)
    y1 = min(h, cy0 + search_radius_px + 1)

    patch = gray[y0:y1, x0:x1].astype(float)
    if patch.size == 0:
        return float(approx_x_px), float(approx_y_px)

    bg = float(np.percentile(patch, 85))
    darkness = np.clip(bg - patch, 0.0, None)
    peak = float(darkness.max())
    if peak < 25.0:
        return float(approx_x_px), float(approx_y_px)

    # Smooth slightly with a small box kernel matching the bullseye core to find the true peak
    r_core = max(2, int(round((marker_diameter_px or 12.0) * 0.25)))
    ph, pw = darkness.shape
    if ph > 2 * r_core + 1 and pw > 2 * r_core + 1:
        padded = np.pad(darkness, ((r_core, r_core), (r_core, r_core)), mode="edge")
        integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)
        k = 2 * r_core + 1
        scores = (
            integral[k:, k:]
            - integral[:-k, k:]
            - integral[k:, :-k]
            + integral[:-k, :-k]
        )
        # Prefer peaks closer to the initial guess to ignore distant text labels
        ys_s, xs_s = np.indices(scores.shape, dtype=float)
        dist2 = (xs_s - (approx_x_px - x0)) ** 2 + (ys_s - (approx_y_px - y0)) ** 2
        sigma2 = max(16.0, (search_radius_px * 0.6) ** 2)
        weighted_scores = scores * np.exp(-0.5 * dist2 / sigma2)
        best_flat = int(np.argmax(weighted_scores))
        py_peak, px_peak = divmod(best_flat, pw)
    else:
        py_peak, px_peak = divmod(int(np.argmax(darkness)), pw)

    # Iteratively recenter a tight circular window around the bullseye for sub-pixel accuracy
    tight_r = max(4, int(round((marker_diameter_px or 12.0) * 0.65)))
    cx_local = float(px_peak)
    cy_local = float(py_peak)
    for _ in range(3):
        tx0 = max(0, int(round(cx_local)) - tight_r)
        tx1 = min(pw, int(round(cx_local)) + tight_r + 1)
        ty0 = max(0, int(round(cy_local)) - tight_r)
        ty1 = min(ph, int(round(cy_local)) + tight_r + 1)
        sub = darkness[ty0:ty1, tx0:tx1]
        sub_peak = float(sub.max()) if sub.size else 0.0
        if sub_peak < 20.0:
            break
        ys, xs = np.indices(sub.shape, dtype=float)
        rad2 = (xs + tx0 - cx_local) ** 2 + (ys + ty0 - cy_local) ** 2
        mask = (sub >= sub_peak * 0.45) & (rad2 <= (tight_r ** 2))
        weights = np.where(mask, sub, 0.0)
        total = float(weights.sum())
        if total <= 0.0:
            break
        cx_local = tx0 + float((xs * weights).sum() / total)
        cy_local = ty0 + float((ys * weights).sum() / total)

    return float(x0 + cx_local), float(y0 + cy_local)
