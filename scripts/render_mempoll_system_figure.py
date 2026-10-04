"""Render the closed-loop intervention diagram as a vector PDF."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]


def box(
    ax: plt.Axes,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    *,
    face: str,
    edge: str,
    linewidth: float = 0.9,
    fontsize: float = 8.2,
) -> None:
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle="round,pad=0.012,rounding_size=0.025",
            facecolor=face,
            edgecolor=edge,
            linewidth=linewidth,
        )
    )
    ax.text(
        x + width / 2,
        y + height / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
    )


def arrow(
    ax: plt.Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: str = "#4a4a4a",
    style: str = "-",
    connection: str = "arc3",
) -> None:
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=8,
            linewidth=1.0,
            linestyle=style,
            color=color,
            connectionstyle=connection,
            shrinkA=1.5,
            shrinkB=1.5,
        )
    )


def render(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.05, 2.35))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    blue_fill, blue_edge = "#eaf2f8", "#4e7896"
    amber_fill, amber_edge = "#fff2d8", "#9a6a20"
    red_fill, red_edge = "#fde9e7", "#a54a42"
    gray_fill, gray_edge = "#f1f1f1", "#606060"

    ax.text(
        0.105,
        0.77,
        "Inputs",
        ha="right",
        va="center",
        fontsize=7.8,
        color=blue_edge,
        weight="bold",
    )
    ax.text(
        0.105,
        0.29,
        "Decision\npath",
        ha="right",
        va="center",
        fontsize=7.8,
        color=amber_edge,
        weight="bold",
    )

    box(
        ax,
        0.32,
        0.64,
        0.32,
        0.25,
        "Authoritative inputs\nmarket (shared)\nportfolio + journal (arm-specific)",
        face=blue_fill,
        edge=blue_edge,
        fontsize=7.6,
    )

    box(ax, 0.13, 0.19, 0.13, 0.20, "Recall\nstore", face=amber_fill, edge=amber_edge)
    box(
        ax,
        0.30,
        0.19,
        0.14,
        0.20,
        "Read-time\ninjection",
        face=red_fill,
        edge=red_edge,
        linewidth=1.2,
    )
    box(ax, 0.49, 0.19, 0.12, 0.20, "LLM\ndecision", face=gray_fill, edge=gray_edge)
    box(ax, 0.68, 0.19, 0.12, 0.20, "Position gate\npass-through\n(main arms)", face=blue_fill, edge=blue_edge, fontsize=7.1)
    box(ax, 0.85, 0.19, 0.12, 0.20, "Execution", face=blue_fill, edge=blue_edge)

    arrow(ax, (0.26, 0.29), (0.30, 0.29), color=amber_edge)
    arrow(ax, (0.44, 0.29), (0.49, 0.29), color=red_edge)
    arrow(ax, (0.61, 0.29), (0.68, 0.29), color=gray_edge)
    arrow(ax, (0.80, 0.29), (0.85, 0.29), color=blue_edge)

    # The two downward edges expose which authoritative fields reach each
    # consumer. In particular, the journal reaches the LLM only through recall.
    arrow(ax, (0.37, 0.64), (0.195, 0.39), color=blue_edge, style="--", connection="arc3,rad=0.06")
    arrow(ax, (0.48, 0.64), (0.55, 0.39), color=blue_edge, connection="arc3,rad=-0.04")

    # Execution changes the next-step portfolio and journal, but not the shared
    # market path. Stop the arrow at the state-box boundary so it cannot obscure
    # the node label.
    arrow(ax, (0.91, 0.39), (0.64, 0.79), color=blue_edge, connection="arc3,rad=0.27")

    ax.text(0.37, 0.10, "only the recalled copy is altered", ha="center", va="center", fontsize=7.5, color=red_edge)
    edge_label = {"facecolor": "white", "edgecolor": "none", "pad": 0.5}
    ax.text(
        0.275,
        0.505,
        "journal recall",
        ha="center",
        va="center",
        fontsize=6.7,
        color=blue_edge,
        bbox=edge_label,
    )
    ax.text(
        0.505,
        0.515,
        "market + portfolio",
        ha="center",
        va="center",
        fontsize=6.7,
        color=blue_edge,
        bbox=edge_label,
    )
    ax.text(0.81, 0.86, "next-step portfolio + journal", ha="center", va="center", fontsize=7.0, color=blue_edge)

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "figures/system_isolation.pdf",
    )
    args = parser.parse_args()
    render(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
