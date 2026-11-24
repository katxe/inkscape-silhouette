#!/usr/bin/env python3
"""Inkscape extension: Guided Hardware Calibration Wizard (Scanner + Silhouette Cutter)."""

import copy
import os
import subprocess
import sys
import tempfile

sys.path.append(os.path.dirname(os.path.abspath(sys.argv[0])))
if sys.platform.lower().startswith("win"):
    sys.path.append(r"C:\Program Files\Inkscape\share\inkscape\extensions")
elif sys.platform.lower().startswith("darwin"):
    sys.path.append("/Applications/Inkscape.app/Contents/Resources/share/inkscape/extensions")
else:
    sys.path.append("/usr/share/inkscape/extensions")

import numpy as np
from lxml import etree
from inkex import EffectExtension, Group
from silhouette import Calibration, HardwareCalibration
from silhouette_calibrate import CalibrationApp


class HardwareOffsetMeasureApp:
    """Interactive tkinter + PIL window with square alignment markers and fine rotation."""

    def __init__(self, user_config, square_size_mm=15.0, initial_rotation_deg=0.0, parent=None):
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

        self.user_config = user_config
        self.square_size_mm = float(square_size_mm)
        self.initial_rotation_deg = float(initial_rotation_deg)
        self.parent = parent

        self.orig_image = None
        self.pil_image = None
        self.tk_photo = None
        self.rotation_deg = 0.0
        self.mirror_h = False
        self.mirror_v = False

        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.pan_start = None
        self.drag_idx = None

        # Two draggable square centers: index 0 = Printed Target, index 1 = Cut-Out Hole
        self.points_px = []

        self.root = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.root.title("Post-Cut Calibration: Align Squares")
        self.root.geometry("1120x800")
        self._build_ui()

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk

        top = ttk.Frame(self.root, padding=6)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top, text="Load Post-Cut Scan...", command=self._load_scan).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="-1.0 deg", command=lambda: self._adjust_rotation(-1.0)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="-0.1 deg", command=lambda: self._adjust_rotation(-0.1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="+0.1 deg", command=lambda: self._adjust_rotation(0.1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="+1.0 deg", command=lambda: self._adjust_rotation(1.0)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Rotate 90", command=lambda: self._adjust_rotation(90.0)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Rotate 180", command=lambda: self._adjust_rotation(180.0)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Mirror H", command=self._toggle_mirror_h).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Mirror V", command=self._toggle_mirror_v).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Fit View", command=self._fit_view).pack(side=tk.LEFT, padx=3)

        self.angle_var = tk.StringVar(value=f"Rot: {self.rotation_deg:+.2f} deg")
        ttk.Label(top, textvariable=self.angle_var).pack(side=tk.LEFT, padx=8)

        hint = ttk.Label(
            self.root,
            text="Align the CYAN square to the PRINTED target edges, and the RED square to the CUT-OUT hole edges. Scroll to zoom, right-drag to pan.",
            padding=(8, 2),
        )
        hint.pack(side=tk.TOP, fill=tk.X)

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

        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        ttk.Label(bottom, text="Scan DPI:").pack(side=tk.LEFT, padx=(4, 2))
        self.dpi_var = tk.StringVar(value="300.0")
        dpi_entry = ttk.Entry(bottom, textvariable=self.dpi_var, width=7)
        dpi_entry.pack(side=tk.LEFT, padx=2)
        dpi_entry.bind("<FocusOut>", lambda _e: (self._redraw(), self._update_status()))

        self.status_var = tk.StringVar(value="Load your post-cut scan (with dark backing sheet behind cardstock).")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT, padx=12)

        ttk.Button(bottom, text="Close", command=self.root.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="Save New System Offset", command=self._save_offset).pack(side=tk.RIGHT, padx=4)

    def _get_dpi(self):
        try:
            v = float(self.dpi_var.get())
            return v if v > 1.0 else 300.0
        except ValueError:
            return 300.0

    def _apply_image_transforms(self):
        if self.orig_image is None:
            return
        img = self.orig_image
        if self.mirror_h:
            img = self.ImageOps.mirror(img)
        if self.mirror_v:
            img = self.ImageOps.flip(img)
        if abs(self.rotation_deg) > 1e-4:
            # PIL rotates counter-clockwise for positive angles
            img = img.rotate(-self.rotation_deg, resample=self.Image.BICUBIC, expand=True, fillcolor=(43, 43, 43))
        self.pil_image = img
        self.angle_var.set(f"Rot: {self.rotation_deg:+.2f} deg")
        self._redraw()
        self._update_status()

    def _load_scan(self):
        initial_dir = self.user_config.get("last_scan_dir") or os.path.expanduser("~")
        path = self.filedialog.askopenfilename(
            title="Select Post-Cut Scanned Image",
            initialdir=initial_dir,
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"), ("All files", "*.*")],
        )
        if not path:
            return

        self.user_config["last_scan_dir"] = os.path.dirname(path)
        dpi_x, _ = Calibration.read_image_dpi(path, default_dpi=300.0)
        self.dpi_var.set(f"{dpi_x:.1f}")

        self.orig_image = self.Image.open(path).convert("RGB")
        self.mirror_h = False
        self.mirror_v = False
        # Auto-apply counter-rotation from the previous scanner calibration step
        self.rotation_deg = -self.initial_rotation_deg
        self._apply_image_transforms()

        w, h = self.pil_image.size
        self.points_px = [[w * 0.5 - 25.0, h * 0.5], [w * 0.5 + 25.0, h * 0.5]]
        self._fit_view()
        self._update_status()

    def _adjust_rotation(self, delta_deg):
        if self.orig_image is None:
            return
        self.rotation_deg = (self.rotation_deg + delta_deg) % 360.0
        if self.rotation_deg > 180.0:
            self.rotation_deg -= 360.0
        self._apply_image_transforms()

    def _toggle_mirror_h(self):
        if self.orig_image is None:
            return
        self.mirror_h = not self.mirror_h
        self._apply_image_transforms()

    def _toggle_mirror_v(self):
        if self.orig_image is None:
            return
        self.mirror_v = not self.mirror_v
        self._apply_image_transforms()

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
        dpi = self._get_dpi()
        half_side_canvas = (self.square_size_mm * dpi / 25.4) * 0.5 * self.zoom
        hit_r = max(24.0, half_side_canvas)
        best_i = None
        best_d = hit_r
        for idx, (ix, iy) in enumerate(self.points_px):
            cx, cy = self._img_to_canvas(ix, iy)
            d = ((cx - event.x) ** 2 + (cy - event.y) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best_i = idx
        self.drag_idx = best_i

    def _on_left_drag(self, event):
        if self.drag_idx is None or self.pil_image is None:
            return
        ix, iy = self._canvas_to_img(event.x, event.y)
        w, h = self.pil_image.size
        self.points_px[self.drag_idx] = [max(0.0, min(w, ix)), max(0.0, min(h, iy))]
        self._redraw()
        self._update_status()

    def _on_left_up(self, _event):
        self.drag_idx = None

    def _redraw(self):
        self.canvas.delete("all")
        if self.pil_image is None:
            return
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        iw, ih = self.pil_image.size
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

        # Draw exact-scale square alignment boxes matching the physical target square size
        dpi = self._get_dpi()
        half_side_canvas = (self.square_size_mm * dpi / 25.4) * 0.5 * self.zoom
        specs = [("#00ffff", "PRINTED"), ("#ff3333", "CUT HOLE")]
        for (color, label), (ix, iy) in zip(specs, self.points_px):
            cx, cy = self._img_to_canvas(ix, iy)
            hs = max(8.0, half_side_canvas)
            self.canvas.create_rectangle(cx - hs, cy - hs, cx + hs, cy + hs, outline=color, width=2)
            self.canvas.create_line(cx - 8, cy, cx + 8, cy, fill=color, width=1)
            self.canvas.create_line(cx, cy - 8, cx, cy + 8, fill=color, width=1)
            self.canvas.create_text(cx + hs + 6, cy - hs, text=label, fill=color, anchor="nw", font=("Arial", 10, "bold"))

    def _compute_offsets(self):
        dpi = self._get_dpi()
        cur_x = float(self.user_config.get("offset_x_mm", Calibration.DEFAULT_OFFSET_X_MM))
        cur_y = float(self.user_config.get("offset_y_mm", Calibration.DEFAULT_OFFSET_Y_MM))
        return HardwareCalibration.compute_hardware_offset(
            self.points_px[0], self.points_px[1], dpi, dpi, cur_x, cur_y
        )

    def _update_status(self):
        if len(self.points_px) < 2:
            return
        new_x, new_y, dx, dy = self._compute_offsets()
        self.status_var.set(f"Delta: dX={dx:+.3f} mm, dY={dy:+.3f} mm  ->  New System Offset: X={new_x:.3f} mm, Y={new_y:.3f} mm")

    def _save_offset(self):
        if len(self.points_px) < 2:
            self.messagebox.showwarning("No Scan", "Please load a post-cut scan first.")
            return
        new_x, new_y, dx, dy = self._compute_offsets()
        self.user_config["offset_x_mm"] = round(new_x, 4)
        self.user_config["offset_y_mm"] = round(new_y, 4)
        Calibration.save_user_config(self.user_config)
        self.messagebox.showinfo(
            "System Offset Saved",
            f"Saved new System Offset:\nX = {new_x:.3f} mm (delta {dx:+.3f} mm)\nY = {new_y:.3f} mm (delta {dy:+.3f} mm)",
        )
        self.root.destroy()

    def run(self):
        if self.parent is not None:
            self.root.transient(self.parent)
            self.root.grab_set()
            self.parent.wait_window(self.root)
        else:
            self.root.mainloop()


class HardwareCalibrationWizard:
    """Installer-style step-by-step wizard with Back / Next and direct step selection."""

    def __init__(self, svg, margin_mm=6.5, square_size_mm=15.0):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox

        self.svg = svg
        self.margin_mm = float(margin_mm)
        self.square_size_mm = float(square_size_mm)
        self.user_config = Calibration.load_user_config()

        existing = Calibration.load_calibration(self.svg)
        self.calib_rotation_deg = Calibration.compute_rotation_deg(existing["points"]) if existing else 0.0

        self.current_page = 0
        self.page_titles = [
            "Generate Pattern",
            "Scanner Calibration",
            "Silhouette Cut",
            "Post-Cut Offset",
        ]

        self.root = tk.Tk()
        self.root.title("Silhouette Hardware Calibration Wizard")
        self.root.geometry("700x460")
        self._build_ui()
        self._show_page(0)

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk

        # Top step navigation bar (allows jumping directly to any stage)
        nav_bar = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        nav_bar.pack(side=tk.TOP, fill=tk.X)
        self.nav_buttons = []
        for idx, title in enumerate(self.page_titles):
            btn = ttk.Button(nav_bar, text=title, command=lambda i=idx: self._show_page(i))
            btn.pack(side=tk.LEFT, padx=4, expand=True, fill=tk.X)
            self.nav_buttons.append(btn)

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(side=tk.TOP, fill=tk.X)

        # Page content area
        self.page_container = ttk.Frame(self.root, padding=16)
        self.page_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.pages = []

        # Page: Generate Test Pattern
        p_gen = ttk.Frame(self.page_container)
        ttk.Label(p_gen, text="Generate Test Calibration Pattern", font=("Arial", 13, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            p_gen,
            text=(
                "Generates 4 corner bullseye markers, a printed target square, and a matching cut square "
                "in the current SVG document. You can also save a standalone printable copy."
            ),
            wraplength=640,
        ).pack(anchor="w", pady=(0, 14))
        btn_row = ttk.Frame(p_gen)
        btn_row.pack(anchor="w")
        ttk.Button(btn_row, text="Generate Test Pattern in SVG", command=self._generate_pattern).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="Save Printable SVG Copy...", command=self._save_svg_copy).pack(side=tk.LEFT)
        self.pages.append(p_gen)

        # Page: Print, Scan & Calibrate Scanner
        p_scan = ttk.Frame(self.page_container)
        ttk.Label(p_scan, text="Print, Scan & Calibrate Scanner", font=("Arial", 13, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            p_scan,
            text=(
                "Print the test sheet on scrap cardstock, place it flush against your scanner's alignment arrow, "
                "and scan it at 150-300 DPI. Then open the Scanner Calibration UI to align the 4 corner bullseyes."
            ),
            wraplength=640,
        ).pack(anchor="w", pady=(0, 14))
        ttk.Button(p_scan, text="Open Scanner Calibration UI...", command=self._open_scanner_calib).pack(anchor="w")
        self.pages.append(p_scan)

        # Page: Cut Test Square on Silhouette
        p_cut = ttk.Frame(self.page_container)
        ttk.Label(p_cut, text="Cut Test Square on Silhouette", font=("Arial", 13, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            p_cut,
            text=(
                "Stick the printed cardstock into the top-left (0, 0) corner of your Silhouette cutting mat "
                "and load the mat. Clicking below hides all layers except the cut square and triggers the "
                "Silhouette cut automatically."
            ),
            wraplength=640,
        ).pack(anchor="w", pady=(0, 14))
        cut_opts = ttk.Frame(p_cut)
        cut_opts.pack(anchor="w")
        ttk.Label(cut_opts, text="Cutting Mat:").pack(side=tk.LEFT, padx=(0, 6))
        self.mat_var = tk.StringVar(value="cameo_12x12")
        ttk.Combobox(
            cut_opts,
            textvariable=self.mat_var,
            values=("cameo_12x12", "cameo_12x24", "portrait_8x12", "portrait_8_5x12", "no_mat"),
            state="readonly",
            width=16,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(cut_opts, text="Cut Test Square on Silhouette", command=self._run_silhouette_cut).pack(side=tk.LEFT)
        self.pages.append(p_cut)

        # Page: Post-Cut Scan & Offset Calibration
        p_post = ttk.Frame(self.page_container)
        ttk.Label(p_post, text="Post-Cut Scan & Offset Calibration", font=("Arial", 13, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            p_post,
            text=(
                "Pop out the cut paper square, place a dark backing sheet behind the cardstock, and scan it. "
                "Then open the Post-Cut Calibration UI to align the CYAN printed square and RED cut-out square."
            ),
            wraplength=640,
        ).pack(anchor="w", pady=(0, 14))
        ttk.Button(p_post, text="Open Post-Cut Calibration UI...", command=self._open_post_cut_calib).pack(anchor="w")
        self.pages.append(p_post)

        # Status + Back / Next footer
        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(side=tk.BOTTOM, fill=tk.X)
        footer = ttk.Frame(self.root, padding=10)
        footer.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_var = tk.StringVar(
            value=f"Saved System Offset: X={self.user_config.get('offset_x_mm', 1.74):.2f} mm, Y={self.user_config.get('offset_y_mm', 2.43):.2f} mm"
        )
        ttk.Label(footer, textvariable=self.status_var).pack(side=tk.LEFT, padx=(4, 10))

        self.next_btn = ttk.Button(footer, text="Next >", command=self._go_next)
        self.next_btn.pack(side=tk.RIGHT, padx=4)
        self.back_btn = ttk.Button(footer, text="< Back", command=self._go_back)
        self.back_btn.pack(side=tk.RIGHT, padx=4)

    def _show_page(self, idx):
        self.current_page = max(0, min(len(self.pages) - 1, idx))
        for page in self.pages:
            page.pack_forget()
        self.pages[self.current_page].pack(fill=self.tk.BOTH, expand=True)

        for i, btn in enumerate(self.nav_buttons):
            label = self.page_titles[i]
            btn.configure(text=f"[ {label} ]" if i == self.current_page else label)

        self.back_btn.configure(state=self.tk.NORMAL if self.current_page > 0 else self.tk.DISABLED)
        self.next_btn.configure(text="Finish" if self.current_page == len(self.pages) - 1 else "Next >")

    def _go_back(self):
        if self.current_page > 0:
            self._show_page(self.current_page - 1)

    def _go_next(self):
        if self.current_page < len(self.pages) - 1:
            self._show_page(self.current_page + 1)
        else:
            self.root.destroy()

    def _generate_pattern(self):
        HardwareCalibration.generate_test_pattern(
            self.svg,
            margin_mm=self.margin_mm,
            square_size_mm=self.square_size_mm,
        )
        self.status_var.set("Test calibration pattern generated in SVG. Ready to print (or save a copy).")

    def _save_svg_copy(self):
        path = self.filedialog.asksaveasfilename(
            title="Save Printable Calibration SVG",
            defaultextension=".svg",
            filetypes=[("SVG files", "*.svg")],
        )
        if not path:
            return
        with open(path, "wb") as handle:
            handle.write(etree.tostring(self.svg, xml_declaration=True, encoding="utf-8", pretty_print=True))
        self.status_var.set(f"Saved printable SVG to: {os.path.basename(path)}")

    def _open_scanner_calib(self):
        markers = Calibration.get_svg_markers(self.svg)
        if len(markers) < 3:
            self.messagebox.showwarning("No Markers", "Generate the test pattern first so markers exist in the SVG.")
            return
        existing = Calibration.load_calibration(self.svg)
        self.user_config = Calibration.load_user_config()
        app = CalibrationApp(markers, existing, self.user_config, parent=self.root)
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
        self.calib_rotation_deg = Calibration.compute_rotation_deg(result["points"])
        self.status_var.set(
            f"Scanner calibrated (RMS={result['rms_error_mm']:.3f} mm, rot={self.calib_rotation_deg:+.2f} deg). Ready to cut!"
        )

    def _run_silhouette_cut(self):
        # Build a temporary copy of the SVG with all layers hidden except the cut layer
        temp_svg = copy.deepcopy(self.svg)
        cut_layer_found = False
        for child in temp_svg:
            if isinstance(child, Group) or child.tag.endswith("}g"):
                if child.get("id") == HardwareCalibration.HW_CUT_LAYER_ID:
                    child.set("style", "display:inline")
                    cut_layer_found = True
                else:
                    child.set("style", "display:none")

        if not cut_layer_found:
            self.messagebox.showwarning("Missing Cut Layer", "Generate the test pattern first before cutting.")
            return

        sendto_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sendto_silhouette.py")
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(etree.tostring(temp_svg, xml_declaration=True, encoding="utf-8"))

        try:
            cmd = [
                sys.executable,
                sendto_script,
                "--preview=false",
                "--regmark=false",
                f"--cuttingmat={self.mat_var.get()}",
                tmp_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                self.status_var.set("Silhouette test cut completed! Scan the cut sheet with dark backing next.")
            else:
                err_msg = (proc.stderr or proc.stdout or "Unknown error").strip()
                self.messagebox.showerror("Silhouette Cut Failed", err_msg[:800])
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def _open_post_cut_calib(self):
        self.user_config = Calibration.load_user_config()
        app = HardwareOffsetMeasureApp(
            self.user_config,
            square_size_mm=self.square_size_mm,
            initial_rotation_deg=self.calib_rotation_deg,
            parent=self.root,
        )
        app.run()
        self.user_config = Calibration.load_user_config()
        self.status_var.set(
            f"Current saved System Offset: X={self.user_config.get('offset_x_mm', 1.74):.2f} mm, Y={self.user_config.get('offset_y_mm', 2.43):.2f} mm"
        )

    def run(self):
        self.root.mainloop()


class HardwareCalibrationExtension(EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--margin", type=float, dest="margin", default=6.5,
                          help="Corner marker margin [mm]")
        pars.add_argument("--square_size", "--square-size", type=float, dest="square_size", default=15.0,
                          help="Target cut-out square size [mm]")

    def effect(self):
        wizard = HardwareCalibrationWizard(
            self.svg,
            margin_mm=self.options.margin,
            square_size_mm=self.options.square_size,
        )
        wizard.run()


if __name__ == "__main__":
    HardwareCalibrationExtension().run()
