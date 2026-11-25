#!/usr/bin/env python3
"""Inkscape extension: Interactive tkinter + PIL scanner calibration popup."""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(sys.argv[0])))
if sys.platform.lower().startswith("win"):
    sys.path.append(r"C:\Program Files\Inkscape\share\inkscape\extensions")
elif sys.platform.lower().startswith("darwin"):
    sys.path.append("/Applications/Inkscape.app/Contents/Resources/share/inkscape/extensions")
else:
    sys.path.append("/usr/share/inkscape/extensions")

import numpy as np
from inkex import EffectExtension
from silhouette import Calibration


class CalibrationApp:
    """Interactive tkinter + PIL window for aligning SVG markers to a scanned image."""

    def __init__(self, svg_markers, existing_calib, user_config, parent=None, cut_paths_mm=None):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        from PIL import Image, ImageTk, ImageOps

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.Image = Image
        self.ImageTk = ImageTk
        self.ImageOps = ImageOps

        self.svg_markers = svg_markers
        self.existing_calib = existing_calib
        self.user_config = user_config
        self.parent = parent
        self.cut_paths_mm = cut_paths_mm or []
        self.result = None

        self.pil_image = None
        self.gray_array = None
        self.tk_photo = None
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.drag_marker_idx = None
        self.pan_start = None

        # Marker positions in image pixel coordinates
        self.scan_pts_px = []

        self.root = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.root.title("Silhouette Scanner Calibration")
        self.root.geometry("1120x800")

        self._build_ui()

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk

        top = ttk.Frame(self.root, padding=6)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top, text="Load / Replace Scan...", command=self._load_scan).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="Rotate 90", command=self._rotate_90).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="Rotate 180", command=self._toggle_rotate).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="Mirror H", command=self._mirror_h).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="Mirror V", command=self._mirror_v).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="Auto-Detect", command=self._autodetect).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="Fit View", command=self._fit_view).pack(side=tk.LEFT, padx=3)

        ttk.Label(top, text="Model:").pack(side=tk.LEFT, padx=(12, 4))
        default_model = (self.existing_calib or {}).get("model", "homography")
        self.model_var = tk.StringVar(value=default_model)
        model_cb = ttk.Combobox(
            top,
            textvariable=self.model_var,
            values=("homography", "tps", "affine"),
            state="readonly",
            width=12,
        )
        model_cb.pack(side=tk.LEFT, padx=2)
        model_cb.bind("<<ComboboxSelected>>", lambda _e: (self._redraw(), self._update_status()))

        self.preview_cut_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            top,
            text="Preview Cut Layer",
            variable=self.preview_cut_var,
            command=self._redraw,
        ).pack(side=tk.LEFT, padx=(10, 4))

        # Canvas
        self.canvas = tk.Canvas(self.root, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.15))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1.0 / 1.15))
        self.canvas.bind("<ButtonPress-1>", self._on_left_down)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_up)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_drag)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan_drag)

        # Bottom controls
        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        ttk.Label(bottom, text="Scan DPI:").pack(side=tk.LEFT, padx=(4, 2))
        self.dpi_var = tk.StringVar(value=f"{(self.existing_calib or {}).get('scan_dpi_x', 300.0):.1f}")
        dpi_entry = ttk.Entry(bottom, textvariable=self.dpi_var, width=7)
        dpi_entry.pack(side=tk.LEFT, padx=2)
        dpi_entry.bind("<FocusOut>", lambda _e: self._update_status())

        ox_default = self.user_config.get("offset_x_mm", (self.existing_calib or {}).get("offset_x_mm", 1.74))
        oy_default = self.user_config.get("offset_y_mm", (self.existing_calib or {}).get("offset_y_mm", 2.43))

        ttk.Label(bottom, text="System Offset X (mm):").pack(side=tk.LEFT, padx=(10, 2))
        self.ox_var = tk.StringVar(value=f"{float(ox_default):.2f}")
        ttk.Entry(bottom, textvariable=self.ox_var, width=7).pack(side=tk.LEFT, padx=2)

        ttk.Label(bottom, text="Y (mm):").pack(side=tk.LEFT, padx=(6, 2))
        self.oy_var = tk.StringVar(value=f"{float(oy_default):.2f}")
        ttk.Entry(bottom, textvariable=self.oy_var, width=7).pack(side=tk.LEFT, padx=2)

        self.status_var = tk.StringVar(value="Load a scanned image (.png/.jpg) to begin.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT, padx=14)

        ttk.Button(bottom, text="Cancel", command=self.root.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="Save & Apply", command=self._save_and_close).pack(side=tk.RIGHT, padx=4)

    def _get_dpi(self):
        try:
            val = float(self.dpi_var.get())
            return val if val > 1.0 else 300.0
        except ValueError:
            return 300.0

    def _load_scan(self):
        initial_dir = self.user_config.get("last_scan_dir") or os.path.expanduser("~")
        path = self.filedialog.askopenfilename(
            title="Select Scanned Image",
            initialdir=initial_dir,
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"), ("All files", "*.*")],
        )
        if not path:
            return

        self.user_config["last_scan_dir"] = os.path.dirname(path)
        Calibration.save_user_config(self.user_config)

        dpi_x, _dpi_y = Calibration.read_image_dpi(path, default_dpi=300.0)
        self.dpi_var.set(f"{dpi_x:.1f}")

        img = self.Image.open(path).convert("RGB")
        self.pil_image = img
        self.rotated_180 = False
        self.gray_array = np.asarray(img.convert("L"), dtype=float)

        # Initialize marker handles at expected pixel locations (accounting for system offset)
        dpi = self._get_dpi()
        px_per_mm = dpi / 25.4
        try:
            ox = float(self.ox_var.get())
            oy = float(self.oy_var.get())
        except ValueError:
            ox, oy = 0.0, 0.0

        w, h = self.pil_image.size
        self.scan_pts_px = []
        for m in self.svg_markers:
            px = max(5.0, min(w - 5.0, (m["svg_x"] - ox) * px_per_mm))
            py = max(5.0, min(h - 5.0, (m["svg_y"] - oy) * px_per_mm))
            self.scan_pts_px.append([px, py])

        self._fit_view()
        self._update_status()

    def _rotate_90(self):
        if self.pil_image is None:
            return
        w, h = self.pil_image.size
        self.pil_image = self.pil_image.transpose(self.Image.ROTATE_270)
        self.gray_array = np.asarray(self.pil_image.convert("L"), dtype=float)
        for pt in self.scan_pts_px:
            old_x, old_y = pt[0], pt[1]
            pt[0] = h - old_y
            pt[1] = old_x
        self._fit_view()
        self._update_status()

    def _toggle_rotate(self):
        if self.pil_image is None:
            return
        self.pil_image = self.pil_image.rotate(180)
        self.gray_array = np.asarray(self.pil_image.convert("L"), dtype=float)
        w, h = self.pil_image.size
        for pt in self.scan_pts_px:
            pt[0] = w - pt[0]
            pt[1] = h - pt[1]
        self._redraw()
        self._update_status()

    def _mirror_h(self):
        if self.pil_image is None:
            return
        self.pil_image = self.ImageOps.mirror(self.pil_image)
        self.gray_array = np.asarray(self.pil_image.convert("L"), dtype=float)
        w, _h = self.pil_image.size
        for pt in self.scan_pts_px:
            pt[0] = w - pt[0]
        self._redraw()
        self._update_status()

    def _mirror_v(self):
        if self.pil_image is None:
            return
        self.pil_image = self.ImageOps.flip(self.pil_image)
        self.gray_array = np.asarray(self.pil_image.convert("L"), dtype=float)
        _w, h = self.pil_image.size
        for pt in self.scan_pts_px:
            pt[1] = h - pt[1]
        self._redraw()
        self._update_status()

    def _autodetect(self):
        if self.gray_array is None or not self.scan_pts_px:
            return
        dpi = self._get_dpi()
        search_r = max(25, int(round(dpi * 0.25)))
        marker_d_px = max(6.0, 2.5 * dpi / 25.4)
        for pt in self.scan_pts_px:
            cx, cy = Calibration.autodetect_marker_center(
                self.gray_array,
                pt[0],
                pt[1],
                search_radius_px=search_r,
                marker_diameter_px=marker_d_px,
            )
            pt[0], pt[1] = cx, cy
        self._redraw()
        self._update_status()

    def _fit_view(self):
        if self.pil_image is None:
            return
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        iw, ih = self.pil_image.size
        self.zoom = min(cw / iw, ch / ih) * 0.95
        self.pan_x = (cw - iw * self.zoom) / 2.0
        self.pan_y = (ch - ih * self.zoom) / 2.0
        self._redraw()

    def _img_to_canvas(self, ix, iy):
        return ix * self.zoom + self.pan_x, iy * self.zoom + self.pan_y

    def _canvas_to_img(self, cx, cy):
        return (cx - self.pan_x) / self.zoom, (cy - self.pan_y) / self.zoom

    def _on_mousewheel(self, event):
        factor = 1.15 if event.delta > 0 else (1.0 / 1.15)
        self._zoom_at(event.x, event.y, factor)

    def _zoom_at(self, cx, cy, factor):
        if self.pil_image is None:
            return
        ix, iy = self._canvas_to_img(cx, cy)
        self.zoom = max(0.05, min(20.0, self.zoom * factor))
        self.pan_x = cx - ix * self.zoom
        self.pan_y = cy - iy * self.zoom
        self._redraw()

    def _on_pan_start(self, event):
        self.pan_start = (event.x, event.y, self.pan_x, self.pan_y)

    def _on_pan_drag(self, event):
        if self.pan_start is None:
            return
        sx, sy, px0, py0 = self.pan_start
        self.pan_x = px0 + (event.x - sx)
        self.pan_y = py0 + (event.y - sy)
        self._redraw()

    def _on_left_down(self, event):
        if self.pil_image is None:
            return
        best_i = None
        best_d = 18.0
        for idx, (ix, iy) in enumerate(self.scan_pts_px):
            cx, cy = self._img_to_canvas(ix, iy)
            d = ((cx - event.x) ** 2 + (cy - event.y) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best_i = idx
        self.drag_marker_idx = best_i

    def _on_left_drag(self, event):
        if self.drag_marker_idx is None or self.pil_image is None:
            return
        ix, iy = self._canvas_to_img(event.x, event.y)
        w, h = self.pil_image.size
        self.scan_pts_px[self.drag_marker_idx] = [max(0.0, min(w, ix)), max(0.0, min(h, iy))]
        self._redraw()
        self._update_status()

    def _on_left_up(self, _event):
        self.drag_marker_idx = None

    def _redraw(self):
        self.canvas.delete("all")
        if self.pil_image is None:
            return

        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        iw, ih = self.pil_image.size

        # Crop only visible image region for smooth high-zoom performance
        x0 = max(0, int(np.floor((0 - self.pan_x) / self.zoom)))
        y0 = max(0, int(np.floor((0 - self.pan_y) / self.zoom)))
        x1 = min(iw, int(np.ceil((cw - self.pan_x) / self.zoom)))
        y1 = min(ih, int(np.ceil((ch - self.pan_y) / self.zoom)))
        if x1 > x0 and y1 > y0:
            crop = self.pil_image.crop((x0, y0, x1, y1))
            disp_w = max(1, int(round((x1 - x0) * self.zoom)))
            disp_h = max(1, int(round((y1 - y0) * self.zoom)))
            resized = crop.resize((disp_w, disp_h), self.Image.NEAREST if self.zoom > 3.0 else self.Image.BILINEAR)
            self.tk_photo = self.ImageTk.PhotoImage(resized)
            dx, dy = self._img_to_canvas(x0, y0)
            self.canvas.create_image(dx, dy, anchor="nw", image=self.tk_photo)

        # Draw live warped cut-layer overlay on top of the scanned image
        if self.preview_cut_var.get() and self.cut_paths_mm and len(self.scan_pts_px) >= 3:
            try:
                pairs = self._build_point_pairs()
                model_name, model_data = Calibration.build_transform(pairs, model=self.model_var.get())
                px_per_mm = self._get_dpi() / 25.4
                for path in self.cut_paths_mm:
                    arr = np.asarray(path, dtype=float)
                    mapped_mm = Calibration.evaluate_transform(arr, model_name, model_data, 0.0, 0.0)
                    coords = []
                    for sx_mm, sy_mm in mapped_mm:
                        cx, cy = self._img_to_canvas(sx_mm * px_per_mm, sy_mm * px_per_mm)
                        coords.extend((cx, cy))
                    if len(coords) >= 4:
                        self.canvas.create_line(*coords, fill="#00ff44", width=2)
            except Exception:
                pass

        # Draw numbered crosshairs
        for m, (ix, iy) in zip(self.svg_markers, self.scan_pts_px):
            cx, cy = self._img_to_canvas(ix, iy)
            r = 12
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#00ffff", width=2)
            self.canvas.create_line(cx - r - 6, cy, cx + r + 6, cy, fill="#ff00ff", width=1)
            self.canvas.create_line(cx, cy - r - 6, cx, cy + r + 6, fill="#ff00ff", width=1)
            self.canvas.create_text(cx + r + 8, cy - r, text=f"#{m['id']}", fill="#00ffff", anchor="w", font=("Arial", 11, "bold"))

    def _build_point_pairs(self):
        dpi = self._get_dpi()
        px_to_mm = 25.4 / dpi
        pairs = []
        for m, (ix, iy) in zip(self.svg_markers, self.scan_pts_px):
            pairs.append({
                "id": m["id"],
                "svg_x": m["svg_x"],
                "svg_y": m["svg_y"],
                "scan_x": ix * px_to_mm,
                "scan_y": iy * px_to_mm,
            })
        return pairs

    def _update_status(self):
        if self.pil_image is None or len(self.scan_pts_px) < 3:
            return
        try:
            pairs = self._build_point_pairs()
            rms, max_e = Calibration.compute_rms_error(pairs, model=self.model_var.get())
            self.status_var.set(f"Markers: {len(pairs)} | Fit RMS: {rms:.3f} mm (Max: {max_e:.3f} mm) | Left-drag markers, Right-drag pan, Wheel zoom")
        except Exception as exc:
            self.status_var.set(f"Fit error: {exc}")

    def _save_and_close(self):
        if self.pil_image is None:
            self.messagebox.showwarning("No Scan Loaded", "Please load a scanned image before saving.")
            return
        try:
            dpi = self._get_dpi()
            ox = float(self.ox_var.get())
            oy = float(self.oy_var.get())
            pairs = self._build_point_pairs()
            rms, _ = Calibration.compute_rms_error(pairs, model=self.model_var.get())
        except Exception as exc:
            self.messagebox.showerror("Invalid Calibration", str(exc))
            return

        self.user_config["offset_x_mm"] = ox
        self.user_config["offset_y_mm"] = oy
        Calibration.save_user_config(self.user_config)

        self.result = {
            "points": pairs,
            "model": self.model_var.get(),
            "offset_x_mm": ox,
            "offset_y_mm": oy,
            "dpi_x": dpi,
            "dpi_y": dpi,
            "rms_error_mm": rms,
        }
        self.root.destroy()

    def run(self):
        if self.parent is not None:
            self.root.transient(self.parent)
            self.root.grab_set()
            self.parent.wait_window(self.root)
        else:
            self.root.mainloop()
        return self.result


class CalibrateFromScan(EffectExtension):
    def effect(self):
        markers = Calibration.get_svg_markers(self.svg)
        if len(markers) < 3:
            self.msg(
                "At least 3 calibration markers are required.\n"
                "Run 'Place Calibration Markers' first, print the sheet, scan it, and then run 'Calibrate from Scan...'."
            )
            return

        existing = Calibration.load_calibration(self.svg)
        user_cfg = Calibration.load_user_config()
        cut_paths_mm = Calibration.extract_preview_cut_paths_mm(self.svg)

        app = CalibrationApp(markers, existing, user_cfg, cut_paths_mm=cut_paths_mm)
        result = app.run()
        if result is None:
            return

        Calibration.save_calibration(
            self.svg,
            points=result["points"],
            model=result["model"],
            offset_x_mm=result["offset_x_mm"],
            offset_y_mm=result["offset_y_mm"],
            dpi_x=result["dpi_x"],
            dpi_y=result["dpi_y"],
            rms_error_mm=result["rms_error_mm"],
            active=True,
        )


if __name__ == "__main__":
    CalibrateFromScan().run()
