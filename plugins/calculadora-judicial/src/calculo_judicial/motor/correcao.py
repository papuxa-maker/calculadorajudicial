"""Correção monetária: acumulação de índice entre datas (SPEC, seção 6).

Convenção do projeto (CLAUDE.md): corrigir de A até B acumula as taxas dos
meses de A (inclusive) até o mês anterior a B. É a mesma convenção da Tabela
Prática do TJSP ("o fator do mês M embute o índice de M−1") e dos coeficientes
do CJF; a Calculadora do Cidadão, que inclui os dois meses extremos, equivale a
``acumular(indice, jan, mês seguinte ao último)``.

Pró-rata (``pro_rata=True``): nos meses das pontas aplica-se a fração de dias
corridos do mês (dias no intervalo / dias do mês). Acumulação composta usa
``(1 + taxa)^fração``; acumulação simples usa ``taxa × fração``.

O motor só lê o banco local por meio do repositório; mês ausente levanta
``IndiceAusente(indice, mes)`` (ou vira projeção, se o usuário permitir).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from calculo_judicial.datas import dias_no_mes, meses_competencia, primeiro_dia, proximo_mes
from calculo_judicial.decimais import CEM, CONTEXTO, UM, ZERO, q10
from calculo_judicial.indices.modelo import IndiceAusente, RepositorioIndices

__all__ = ["IndiceAusente", "ItemMes", "fracoes_meses", "detalhar", "fator_de_itens", "acumular"]


@dataclass(frozen=True)
class ItemMes:
    """Taxa de um mês usada num cálculo, com a fração aplicada."""

    mes: date
    indice: str
    taxa_pct: Decimal
    fracao: Decimal = UM
    projecao: bool = False
    indice_origem: str | None = None  # índice real quando ``indice`` é tabela composta
    data_coleta: str = ""


def fracoes_meses(de: date, ate: date, pro_rata: bool = False) -> list[tuple[date, Decimal]]:
    """Meses (dia 01) e frações entre ``de`` (inclusive) e ``ate`` (exclusivo, "até B").

    Sem pró-rata: meses de ``de`` até o mês anterior a ``ate``, fração 1.
    Com pró-rata: cada mês que tem dias no intervalo ``[de, ate)``, com a fração
    de dias corridos; meses sem dias no intervalo não aparecem.
    """
    if ate <= de:
        return []
    if not pro_rata:
        return [(m, UM) for m in meses_competencia(de, ate)]
    saida: list[tuple[date, Decimal]] = []
    mes = primeiro_dia(de)
    while mes < ate:
        seguinte = proximo_mes(mes)
        dias = (min(ate, seguinte) - max(de, mes)).days
        if dias > 0:
            with localcontext(CONTEXTO):
                fracao = UM if dias == dias_no_mes(mes) else Decimal(dias) / Decimal(dias_no_mes(mes))
            saida.append((mes, fracao))
        mes = seguinte
    return saida


def detalhar(
    indice: str,
    de: date,
    ate: date,
    repo: RepositorioIndices,
    pro_rata: bool = False,
    permitir_projecao: bool = False,
) -> list[ItemMes]:
    """Taxas mês a mês de ``indice`` entre ``de`` e ``ate`` (convenção "até B").

    Levanta ``IndiceAusente`` no primeiro mês que faltar no banco (a não ser que
    ``permitir_projecao`` e o mês seja posterior ao último disponível).
    """
    itens = []
    for mes, fracao in fracoes_meses(de, ate, pro_rata):
        # fração de mês (pró-rata): numa tabela composta vale o índice vigente no primeiro dia do trecho
        dia = max(de, mes) if fracao != UM else None
        t = repo.taxa(indice, mes, permitir_projecao=permitir_projecao, dia=dia)
        itens.append(ItemMes(mes, indice, t.taxa_pct, fracao, t.projecao, t.indice_origem, t.data_coleta))
    return itens


def fator_de_itens(itens: list[ItemMes], capitalizacao: str = "composta") -> Decimal:
    """Fator acumulado de uma lista de meses.

    - ``composta``: Π (1 + taxa/100)^fração (produto, como a Calculadora do Cidadão);
    - ``simples``: 1 + Σ taxa/100 × fração ("acumulada mensalmente" no sentido do CJF/Receita).
    """
    with localcontext(CONTEXTO):
        if capitalizacao == "simples":
            soma = ZERO
            for item in itens:
                soma += item.taxa_pct * item.fracao / CEM
            return q10(UM + soma)
        if capitalizacao != "composta":
            raise ValueError(f"capitalização inválida: {capitalizacao}")
        fator = UM
        for item in itens:
            base = UM + item.taxa_pct / CEM
            if item.fracao == UM:
                fator = q10(fator * base)
            else:
                fator = q10(fator * (item.fracao * base.ln()).exp())
        return fator


def acumular(
    indice: str,
    de: date,
    ate: date,
    repo: RepositorioIndices,
    pro_rata: bool = False,
    permitir_projecao: bool = False,
    capitalizacao: str = "composta",
) -> Decimal:
    """Fator de correção de ``indice`` de ``de`` até ``ate``.

    Produto de ``(1 + taxa/100)`` dos meses de ``de`` (inclusive) até o mês
    anterior a ``ate`` (SPEC, seção 6). Se ``pro_rata``, aplica fração de dias
    nos meses das pontas. Se faltar mês, levanta ``IndiceAusente(indice, mes)``.
    """
    return fator_de_itens(detalhar(indice, de, ate, repo, pro_rata, permitir_projecao), capitalizacao)
