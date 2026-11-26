import argparse
import json
from pathlib import Path

from .render import VIS, inspect_header, render_cached, select_layer
from .transport import Transport, atomic_write


def main():
    parser = argparse.ArgumentParser(
        description="Protótipo isolado de leitura COG do BDC"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--visparams-file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    header = commands.add_parser("header")
    header.add_argument("url")
    render = commands.add_parser("render")
    render.add_argument("--source", required=True)
    render.add_argument("--visparam", required=True)
    render.add_argument(
        "--xyz", type=int, nargs=3, metavar=("X", "Y", "Z"), required=True
    )
    render.add_argument("--items", type=Path)
    render.add_argument("--period", choices=["WET", "DRY", "MONTH"], default="MONTH")
    render.add_argument("--year", type=int, default=2024)
    render.add_argument("--month", type=int, default=8)
    render.add_argument(
        "--mode", choices=["SCENE", "BEST_IMAGE", "MOSAIC"], default="SCENE"
    )
    render.add_argument("--policy", choices=["legacy-desc", "least-cloud"])
    render.add_argument("--s2-offset", type=float)
    render.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.visparams_file:
        for name, value in json.loads(args.visparams_file.read_text()).items():
            if name in VIS:
                parser.error("Parâmetros existentes não podem ser substituídos")
            VIS[name] = (value["bands"], value["min"], value["max"], value["gamma"])
    transport = Transport(args.root / "cache/http")
    if args.command == "header":
        print(json.dumps(inspect_header(transport, args.url), indent=2))
    else:
        if args.visparam not in VIS:
            parser.error("Visparam desconhecido")
        x, y, z = args.xyz
        if args.items:
            items = json.loads(args.items.read_text())
            items = items if isinstance(items, list) else [items]
        else:
            if args.mode == "SCENE":
                parser.error("SCENE exige --items")
            items = select_layer(
                transport,
                args.source,
                x,
                y,
                z,
                args.period,
                args.year,
                args.month,
                args.mode,
                args.policy,
            )
        data, stats = render_cached(
            transport,
            args.root / "cache/png",
            items,
            args.source,
            args.visparam,
            x,
            y,
            z,
            args.mode,
            args.s2_offset,
        )
        atomic_write(args.output, data)
        atomic_write(
            args.output.with_suffix(".json"),
            json.dumps(dict(stats, items=[i["id"] for i in items]), indent=2).encode(),
        )
        print(json.dumps(stats))


if __name__ == "__main__":
    main()
