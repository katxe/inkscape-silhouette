"""Hardware calibration test-pattern generator and closed-loop offset math.

Generates printable target outlines paired with matching cut-out squares so the
scanner can measure the exact combined scanner-bezel + Silhouette-mat offset.
"""

from silhouette import Calibration

HW_PRINT_LAYER_ID = "silhouette-hw-print-targets"
HW_PRINT_LAYER_LABEL = "Hardware Test Print Targets"
HW_CUT_LAYER_ID = "silhouette-hw-cut-squares"
HW_CUT_LAYER_LABEL = "Hardware Test Cut Squares"


def generate_test_pattern(svg, margin_mm=6.5, square_size_mm=15.0, marker_diameter_mm=2.5):
    """Populate the SVG with corner bullseyes, printed target outlines, and cut squares."""
    from inkex import Layer, Rectangle, PathElement

    width_mm, height_mm = Calibration.get_document_size_mm(svg)

    # Corner bullseye markers (skipped during cutting)
    Calibration.place_default_markers(
        svg,
        margin_mm=margin_mm,
        diameter_mm=marker_diameter_mm,
        clear_existing=True,
    )

    # Remove any previous hardware test layers before regenerating
    for lid in (HW_PRINT_LAYER_ID, HW_CUT_LAYER_ID):
        existing = svg.getElementById(lid)
        if existing is not None and existing.getparent() is not None:
            existing.getparent().remove(existing)

    # Layer name contains "print" so sendto_silhouette.py skips cutting it
    print_layer = Layer.new(HW_PRINT_LAYER_LABEL)
    print_layer.set("id", HW_PRINT_LAYER_ID)
    svg.add(print_layer)

    cut_layer = Layer.new(HW_CUT_LAYER_LABEL)
    cut_layer.set("id", HW_CUT_LAYER_ID)
    svg.add(cut_layer)

    cx_mm = width_mm / 2.0
    cy_mm = height_mm / 2.0
    half_mm = square_size_mm / 2.0

    x_uu = Calibration.mm_to_svg_units(svg, cx_mm - half_mm)
    y_uu = Calibration.mm_to_svg_units(svg, cy_mm - half_mm)
    size_uu = Calibration.mm_to_svg_units(svg, square_size_mm)
    cx_uu = Calibration.mm_to_svg_units(svg, cx_mm)
    cy_uu = Calibration.mm_to_svg_units(svg, cy_mm)
    cross_ext = Calibration.mm_to_svg_units(svg, half_mm + 6.0)
    stroke_print = Calibration.mm_to_svg_units(svg, 0.35)
    stroke_cut = Calibration.mm_to_svg_units(svg, 0.15)

    # Printed target outline and crosshair
    target_box = Rectangle(x=str(x_uu), y=str(y_uu), width=str(size_uu), height=str(size_uu))
    target_box.set("id", "hw-print-target-box")
    target_box.set("style", f"fill:none;stroke:#000000;stroke-width:{stroke_print};")
    print_layer.add(target_box)

    target_cross = PathElement()
    target_cross.set("id", "hw-print-target-cross")
    target_cross.set(
        "d",
        f"M {cx_uu - cross_ext},{cy_uu} H {cx_uu + cross_ext} M {cx_uu},{cy_uu - cross_ext} V {cy_uu + cross_ext}",
    )
    target_cross.set("style", f"fill:none;stroke:#000000;stroke-width:{stroke_print * 0.7};")
    print_layer.add(target_cross)

    # Matching cut-out square on the cut layer
    cut_box = Rectangle(x=str(x_uu), y=str(y_uu), width=str(size_uu), height=str(size_uu))
    cut_box.set("id", "hw-cut-target-box")
    cut_box.set("style", f"fill:none;stroke:#ff0000;stroke-width:{stroke_cut};")
    cut_layer.add(cut_box)

    return {"center_x_mm": cx_mm, "center_y_mm": cy_mm, "square_size_mm": square_size_mm}


def compute_hardware_offset(
    printed_center_px,
    cut_center_px,
    dpi_x,
    dpi_y,
    current_offset_x_mm,
    current_offset_y_mm,
):
    """Compute updated (offset_x_mm, offset_y_mm, delta_x_mm, delta_y_mm) from post-cut scan."""
    px_to_mm_x = 25.4 / float(dpi_x)
    px_to_mm_y = 25.4 / float(dpi_y)

    # If the cut landed left of the printed target (cut_x < printed_x), increase offset_x
    delta_x_mm = (float(printed_center_px[0]) - float(cut_center_px[0])) * px_to_mm_x
    delta_y_mm = (float(printed_center_px[1]) - float(cut_center_px[1])) * px_to_mm_y

    new_x = float(current_offset_x_mm) + delta_x_mm
    new_y = float(current_offset_y_mm) + delta_y_mm
    return new_x, new_y, delta_x_mm, delta_y_mm
