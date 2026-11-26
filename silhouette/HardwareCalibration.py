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
    """Populate the SVG with corner bullseyes, a larger printed target circle, and a smaller cut circle."""
    from inkex import Layer, Circle, PathElement

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
    cut_r_mm = square_size_mm / 2.0
    print_r_mm = cut_r_mm + 3.0

    cx_uu = Calibration.mm_to_svg_units(svg, cx_mm)
    cy_uu = Calibration.mm_to_svg_units(svg, cy_mm)
    cut_r_uu = Calibration.mm_to_svg_units(svg, cut_r_mm)
    print_r_uu = Calibration.mm_to_svg_units(svg, print_r_mm)
    cross_ext = Calibration.mm_to_svg_units(svg, print_r_mm + 5.0)
    stroke_print = Calibration.mm_to_svg_units(svg, 0.35)
    stroke_cut = Calibration.mm_to_svg_units(svg, 0.15)

    # Larger printed target circle and crosshair (stays intact around the smaller cut hole)
    target_circle = Circle(cx=str(cx_uu), cy=str(cy_uu), r=str(print_r_uu))
    target_circle.set("id", "hw-print-target-circle")
    target_circle.set("style", f"fill:none;stroke:#000000;stroke-width:{stroke_print};")
    print_layer.add(target_circle)

    target_cross = PathElement()
    target_cross.set("id", "hw-print-target-cross")
    target_cross.set(
        "d",
        f"M {cx_uu - cross_ext},{cy_uu} H {cx_uu + cross_ext} M {cx_uu},{cy_uu - cross_ext} V {cy_uu + cross_ext}",
    )
    target_cross.set("style", f"fill:none;stroke:#000000;stroke-width:{stroke_print * 0.7};")
    print_layer.add(target_cross)

    # Smaller concentric cut-out circle on the cut layer
    cut_circle = Circle(cx=str(cx_uu), cy=str(cy_uu), r=str(cut_r_uu))
    cut_circle.set("id", "hw-cut-target-circle")
    cut_circle.set("style", f"fill:none;stroke:#ff0000;stroke-width:{stroke_cut};")
    cut_layer.add(cut_circle)

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
