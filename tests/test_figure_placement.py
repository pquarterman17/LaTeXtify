from pathlib import Path

import pytest
from PIL import Image

from latextify.emit.figures_copy import _copy_figures
from latextify.figures.placement import parse_figure_placements
from latextify.model.figure import Figure, FigureSource


def _figure(path: Path, number: int) -> Figure:
    return Figure(
        number=number, caption="caption", embedded_path=path, source=FigureSource.EMBEDDED
    )


def test_parse_figure_placements_accepts_cli_and_browser_shapes():
    assert parse_figure_placements(["1=one, 2=two", "S3=auto"]) == {
        ("", 1): "one",
        ("", 2): "two",
        ("S", 3): "auto",
    }


@pytest.mark.parametrize("spec", ["0=one", "1=wide", "x=two", "1", "1=one,1=two"])
def test_parse_figure_placements_rejects_invalid_values(spec):
    with pytest.raises(ValueError):
        parse_figure_placements([spec])


def test_explicit_placement_overrides_aspect_ratio(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "figures"
    source_dir.mkdir()
    output_dir.mkdir()
    tall = source_dir / "tall.png"
    wide = source_dir / "wide.png"
    Image.new("RGB", (100, 300), "white").save(tall)
    Image.new("RGB", (300, 100), "white").save(wide)

    _files, figures, _warnings = _copy_figures(
        (_figure(tall, 1), _figure(wide, 2)),
        output_dir,
        placements={("", 1): "two", ("", 2): "one"},
    )

    assert figures[0].wide is True
    assert figures[1].wide is False
