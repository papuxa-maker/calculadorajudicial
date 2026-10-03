"""Conversão das entradas e da memória do motor para JSON (persistência em calculos.db).

Decimal vira texto (nunca float) e datas viram ``aaaa-mm-dd``.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from decimal import Decimal
from typing import Any

from calculo_judicial.datas import parse_data
from calculo_judicial.motor.memoria import Memoria
from calculo_judicial.motor.parcela import Abatimento, Parametros, Parcela, Periodo

CAMPOS_DATA = {
    "de", "ate", "data_base", "data_evento", "inicio_juros", "data", "data_calculo", "data_citacao",
    "data_ajuizamento", "data_transito", "data_intimacao", "data_decurso_523",
}
CAMPOS_DECIMAL = {"valor_original", "valor", "taxa_juros_am", "acrescimo_final_pct", "multa_contratual_pct", "honorarios_pct"}


def para_json(obj: Any) -> Any:
    """Converte dataclasses, Decimal e date em estruturas JSON (texto para números)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: para_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [para_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): para_json(v) for k, v in obj.items()}
    if isinstance(obj, float):
        raise TypeError(f"float não é serializado (use Decimal): {obj!r}")
    return obj


def _ler_valor(campo: str, valor: Any) -> Any:
    if valor is None:
        return None
    if campo in CAMPOS_DATA:
        return parse_data(valor)
    if campo in CAMPOS_DECIMAL:
        return Decimal(str(valor))
    return valor


def periodo_de_json(d: dict) -> Periodo:
    return Periodo(**{k: _ler_valor(k, v) for k, v in d.items()})


def parcela_de_json(d: dict) -> Parcela:
    dados = {k: _ler_valor(k, v) for k, v in d.items() if k != "periodos"}
    return Parcela(**dados, periodos=[periodo_de_json(p) for p in d.get("periodos", [])])


def parametros_de_json(d: dict) -> Parametros:
    dados = {}
    for k, v in d.items():
        if k == "abatimentos":
            dados[k] = [Abatimento(**{kk: _ler_valor(kk, vv) for kk, vv in a.items()}) for a in v]
        elif k == "honorarios_fixos":
            dados[k] = (Decimal(str(v[0])), parse_data(v[1])) if v else None
        else:
            dados[k] = _ler_valor(k, v)
    campos = {f.name for f in dataclasses.fields(Parametros)}
    return Parametros(**{k: v for k, v in dados.items() if k in campos})


def entrada_para_json(processo: dict, parcelas: list[Parcela], params: Parametros) -> str:
    return json.dumps(
        {"processo": processo, "parcelas": para_json(parcelas), "parametros": para_json(params)},
        ensure_ascii=False,
    )


def entrada_de_json(texto: str) -> tuple[dict, list[Parcela], Parametros]:
    dados = json.loads(texto)
    return dados["processo"], [parcela_de_json(p) for p in dados["parcelas"]], parametros_de_json(dados["parametros"])


def memoria_para_json(memoria: Memoria) -> str:
    """Resultado completo (resumo, parcelas, linhas, acessórios, critérios, fontes, avisos)."""
    return json.dumps(para_json(memoria), ensure_ascii=False)
