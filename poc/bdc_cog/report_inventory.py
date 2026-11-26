import json
from pathlib import Path


def main():
    root = Path(__file__).parent
    inventory = json.loads((root / "evidence/inventory.json").read_text())
    lines = [
        "# Inventário público do BDC",
        "",
        "Consulta executada em 25/11/2026 (horário de Brasília). As 80 coleções tiveram um item consultado. Metadados declarados não equivalem à validação de todos os arquivos. Ausência de valor permanece explícita. Respostas completas: `evidence/collections.json` e `evidence/inventory.json`.",
        "",
        "Cada entrada informa classificação, formatos encontrados no item, bandas e parâmetros declarados, resolução, extensão temporal, cobertura, licença e CRS quando disponível. Produtos ambientais derivados não substituem composições ópticas do TVI.",
        "",
    ]
    for row in inventory:
        lines += [
            "## " + row["id"],
            "",
            "- Classificação: " + row["category"] + ".",
            "- Formatos do item: " + ", ".join(row.get("formats", [])) + ".",
            "- Licença declarada: " + str(row["license"]) + ".",
            "- Acesso público declarado: " + str(row["public"]) + ".",
            "- Intervalos temporais: `"
            + json.dumps(row["extent"]["temporal"]["interval"])
            + "`.",
            "- Cobertura declarada (bbox): `"
            + json.dumps(row["extent"]["spatial"]["bbox"])
            + "`.",
        ]
        sample = row.get("sample") or {}
        props = sample.get("properties", {})
        projection = {k: v for k, v in props.items() if k.startswith("proj:")}
        lines += [
            "- CRS do item: "
            + ("`" + json.dumps(projection) + "`" if projection else "não declarado")
            + ".",
            "- Grade: "
            + str(
                row["grid"]
                or "não declarada no campo consultado; conferir tileId/bdc:tiles no item completo"
            )
            + ".",
        ]
        bands = row["bands"]
        if bands:
            lines += [
                "",
                "| Banda | Escala | Offset | Nodata | Tipo | Resolução x/y |",
                "|---|---|---|---|---|---|",
            ]
            for b in bands:
                values = [
                    b.get("name"),
                    b.get("scale"),
                    b.get("scale_add"),
                    b.get("nodata"),
                    b.get("data_type"),
                    str(b.get("resolution_x")) + "/" + str(b.get("resolution_y")),
                ]
                lines.append(
                    "| "
                    + " | ".join(
                        "não declarado" if v is None else str(v) for v in values
                    )
                    + " |"
                )
        else:
            lines += [
                "- Bandas e escala: não declaradas em `properties.eo:bands`; assets disponíveis: "
                + ", ".join(row["assets_declared"])
                + "."
            ]
        header = root / "evidence" / f"header-{row['id']}.json"
        if header.exists():
            h = json.loads(header.read_text())
            lines += [
                "- Cabeçalho amostrado: `"
                + json.dumps(
                    {
                        k: h[k]
                        for k in [
                            "crs",
                            "dtype",
                            "nodata",
                            "block_shapes",
                            "overviews",
                            "count",
                        ]
                    },
                    ensure_ascii=False,
                )
                + "`."
            ]
        lines += [""]
    (root / "INVENTARIO.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
