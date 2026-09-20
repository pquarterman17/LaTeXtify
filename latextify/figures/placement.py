"""Parse and apply explicit per-figure column choices."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

FigurePlacements = Mapping[tuple[str, int], str]


def parse_figure_placements(specs: Iterable[str]) -> dict[tuple[str, int], str]:
    """Parse ``1=one`` / ``S2=two`` specifications.

    Comma-separated values are accepted for the browser form; repeatable values
    are convenient for the CLI. ``auto`` is accepted explicitly and means the
    normal aspect-ratio decision.
    """
    result: dict[tuple[str, int], str] = {}
    for raw_group in specs:
        for raw in raw_group.split(","):
            spec = raw.strip()
            if not spec:
                continue
            try:
                key, mode = (part.strip() for part in spec.split("=", 1))
            except ValueError as exc:
                raise ValueError(
                    f"invalid figure column choice '{spec}'; use N=one|two|auto"
                ) from exc
            prefix = "S" if key[:1].upper() == "S" else ""
            number_text = key[1:] if prefix else key
            if not number_text.isdigit() or int(number_text) <= 0:
                raise ValueError(f"invalid figure number '{key}'; use a positive number")
            mode = mode.lower()
            if mode not in {"auto", "one", "two"}:
                raise ValueError(f"invalid figure column mode '{mode}'; use one, two, or auto")
            placement_key = (prefix, int(number_text))
            if placement_key in result:
                raise ValueError(f"figure column choice for {key} was supplied more than once")
            result[placement_key] = mode
    return result


def placement_for(placements: FigurePlacements | None, prefix: str, number: int) -> str:
    return "auto" if placements is None else placements.get((prefix, number), "auto")
