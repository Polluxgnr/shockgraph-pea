"""Draw the graph schema twice from one description: draw.io XML (editable) and PNG (for the README).

Usage: python -m scripts.dev.draw_schema   -> docs/schema.drawio and docs/img/a_schema.png
Edge colours follow the truth levels of docs/schema.md.
"""

from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # pylint: disable=wrong-import-position
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # pylint: disable=wrong-import-position

NODES = {
    "Fund": (60, 40, "isin* · name · ter\nreplication · pea_eligible"),
    "Index": (420, 40, "code* · name · provider"),
    "Claim": (780, 40, "claim_id* · kind · status\nconfidence · sources"),
    "Article": (1140, 40, "url* · title · source\npublished_at"),
    "Country": (60, 330, "code* · name · eu_eea"),
    "Company": (420, 330, "isin* · name · ticker\npea_eligible · market_cap_eur"),
    "Factor": (1140, 330, "code* · name · kind\n:Commodity | :Currency | :Rate"),
    "Sector": (60, 600, "code* · name"),
    "Industry": (420, 600, "code* · name"),
}
TRUTH_COLOURS = {"fact": "#333333", "estimate": "#1f6feb", "prediction": "#8250df", "hypothesis": "#d1662b"}
# (source, target, label, truth, bend of the curve, label offset x, label offset y)
EDGES = [
    ("Fund", "Index", "TRACKS", "fact", 0.0, 0, 12),
    ("Fund", "Company", "HOLDS {weight}", "fact", 0.0, -40, 0),
    ("Index", "Company", "CONSTITUENT {weight, as_of}", "fact", 0.0, 0, 0),
    ("Company", "Country", "HEADQUARTERED_IN", "fact", 0.0, 0, 12),
    ("Company", "Industry", "IN_INDUSTRY", "fact", 0.0, 0, 0),
    ("Industry", "Sector", "PART_OF", "fact", 0.0, 0, 12),
    ("Industry", "Factor", "PRODUCES / CONSUMES {rationale}", "fact", 0.0, 60, -40),
    ("Company", "Factor", "PRODUCES {source: wikidata}", "fact", -0.25, 0, 34),
    ("Company", "Factor", "SENSITIVE_TO {beta, r2, window}", "estimate", 0.0, 0, 12),
    ("Company", "Factor", "PREDICTED_SENSITIVITY {prob}", "prediction", 0.25, 0, -30),
    ("Company", "Company", "CORRELATED_WITH {rho}", "estimate", 0.0, 0, 0),
    ("Claim", "Company", "SUBJECT / OBJECT", "hypothesis", 0.0, 30, 0),
    ("Claim", "Article", "EXTRACTED_FROM {confidence}", "hypothesis", 0.0, 0, 34),
    ("Article", "Company", "MENTIONS", "fact", 0.0, 60, 30),
]
WIDTH, HEIGHT = 240, 80


def drawio_xml() -> str:
    """The schema as a draw.io (diagrams.net) document."""
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for name, (left, top, props) in NODES.items():
        value = escape(f"<b>{name}</b><br><font style='font-size:10px'>{props.replace(chr(10), '<br>')}</font>")
        style = "rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f7fa;strokeColor=#57606a;"
        cells.append(
            f'<mxCell id="{name}" value="{value}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{left}" y="{top}" width="{WIDTH}" height="{HEIGHT}" as="geometry"/></mxCell>'
        )
    for number, (source, target, label, truth, bend, *_) in enumerate(EDGES):
        colour = TRUTH_COLOURS[truth]
        dashed = "dashed=1;" if truth != "fact" else ""
        style = f"endArrow=classic;html=1;strokeColor={colour};fontColor={colour};{dashed}fontSize=10;"
        cells.append(
            f'<mxCell id="e{number}" value="{escape(label)} ({truth})" style="{style}" edge="1" '
            f'source="{source}" target="{target}" parent="1"><mxGeometry relative="1" as="geometry">'
            f"{waypoint(source, target, bend)}</mxGeometry></mxCell>"
        )
    body = "".join(cells)
    return (
        '<mxfile host="shockgraph-pea"><diagram name="ShockGraph PEA schema">'
        f'<mxGraphModel dx="1400" dy="760" grid="1" gridSize="10" page="1"><root>{body}</root></mxGraphModel>'
        "</diagram></mxfile>\n"
    )


def waypoint(source: str, target: str, bend: float) -> str:
    """draw.io waypoint that curves an edge like the PNG (parallel edges would otherwise overlap); '' when straight."""
    if not bend or source == target:
        return ""
    (start_x, start_y), (end_x, end_y) = centre(source), centre(target)
    middle_x, middle_y = (start_x + end_x) / 2, -(start_y + end_y) / 2  # back to draw.io coordinates (y down)
    length = ((end_x - start_x) ** 2 + (end_y - start_y) ** 2) ** 0.5
    normal_x, normal_y = (end_y - start_y) / length, (end_x - start_x) / length  # perpendicular, y down
    offset = bend * length
    return (
        f'<Array as="points"><mxPoint x="{middle_x + normal_x * offset:.0f}" '
        f'y="{middle_y + normal_y * offset:.0f}"/></Array>'
    )


def centre(name: str) -> tuple[float, float]:
    """Centre of a node box in plot coordinates (y grows downwards in draw.io, upwards in matplotlib)."""
    left, top, _ = NODES[name]
    return left + WIDTH / 2, -(top + HEIGHT / 2)


def border(name: str, towards: tuple[float, float], gap: float = 6) -> tuple[float, float]:
    """Point where the segment from the box centre towards `towards` leaves the box (plus a small gap)."""
    centre_x, centre_y = centre(name)
    delta_x, delta_y = towards[0] - centre_x, towards[1] - centre_y
    scale = min(
        (WIDTH / 2 + gap) / abs(delta_x) if delta_x else float("inf"),
        (HEIGHT / 2 + gap) / abs(delta_y) if delta_y else float("inf"),
    )
    return centre_x + delta_x * scale, centre_y + delta_y * scale


def draw_png(path: Path) -> None:
    """The same schema as a PNG."""
    figure, axis = plt.subplots(figsize=(16, 8.4), dpi=130)
    _draw_edges(axis)
    for name, (left, top, props) in NODES.items():
        box = FancyBboxPatch(
            (left, -(top + HEIGHT)),
            WIDTH,
            HEIGHT,
            boxstyle="round,pad=0,rounding_size=10",
            facecolor="#f5f7fa",
            edgecolor="#57606a",
            zorder=2,
        )
        axis.add_patch(box)
        axis.text(left + WIDTH / 2, -(top + 24), name, ha="center", fontsize=11, fontweight="bold", zorder=5)
        axis.text(
            left + WIDTH / 2, -(top + 54), props, ha="center", va="center", fontsize=7.5, color="#444444", zorder=5
        )
    for row, (truth, colour) in enumerate(TRUTH_COLOURS.items()):
        axis.plot([1180, 1220], [-600 - 26 * row] * 2, color=colour, linestyle="-" if truth == "fact" else "--")
        axis.text(1230, -600 - 26 * row, truth, color=colour, va="center", fontsize=9)
    axis.text(1180, -578, "Truth level", fontsize=9, fontweight="bold")
    axis.text(
        60,
        -735,
        "* unique constraint   ·   full-text indexes: company_name_ft, fund_name_ft, factor_name_ft",
        fontsize=8,
        color="#555555",
    )
    axis.set_xlim(20, 1420)
    axis.set_ylim(-750, 20)
    axis.axis("off")
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path)
    plt.close(figure)


def main() -> None:
    """Write both files."""
    Path("docs/schema.drawio").write_text(drawio_xml(), encoding="utf-8")
    draw_png(Path("docs/img/a_schema.png"))
    print("docs/schema.drawio and docs/img/a_schema.png written")


def _draw_edges(axis) -> None:
    """Arrows and labels, coloured and dashed by truth level."""
    for source, target, label, truth, bend, offset_x, offset_y in EDGES:
        colour = TRUTH_COLOURS[truth]
        style = "-" if truth == "fact" else "--"
        text_style = {
            "color": colour,
            "fontsize": 8,
            "ha": "center",
            "zorder": 4,
            "bbox": {"facecolor": "white", "edgecolor": "none", "pad": 1},
        }
        if source == target:
            left, top, _ = NODES[source]
            loop_start, loop_end = (left + 40, -top + 6), (left - 6, -(top + 30))
            axis.add_patch(
                FancyArrowPatch(
                    loop_start,
                    loop_end,
                    connectionstyle="arc3,rad=0.9",
                    arrowstyle="-|>",
                    color=colour,
                    linestyle=style,
                    mutation_scale=12,
                    zorder=3,
                )
            )
            axis.text(left - 70, -top + 22, label, **text_style)
            continue
        start, end = border(source, centre(target)), border(target, centre(source))
        axis.add_patch(
            FancyArrowPatch(
                start,
                end,
                connectionstyle=f"arc3,rad={bend}",
                arrowstyle="-|>",
                color=colour,
                linestyle=style,
                mutation_scale=13,
                zorder=3,
            )
        )
        axis.text((start[0] + end[0]) / 2 + offset_x, (start[1] + end[1]) / 2 + offset_y, label, **text_style)


if __name__ == "__main__":
    main()
