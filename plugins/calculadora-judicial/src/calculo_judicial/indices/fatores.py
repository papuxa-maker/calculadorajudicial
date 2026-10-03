"""Recálculo do fator acumulado de cada índice.

Convenção do campo ``fator_acumulado`` (SPEC: "base 1 em 1994-07-01"):
o fator gravado no mês M é o produto de ``(1 + taxa/100)`` de todos os meses
desde o primeiro mês da série até M, inclusive — ou seja, o nível do índice
no fim do mês M, com nível 1 no dia 01/07/1994 (ou no primeiro dia do primeiro
mês da série, se ela começar depois). Assim, o fator entre o início do mês A
e o início do mês B é ``fator(B-1) / fator(A-1)``.

O motor de cálculo não depende deste campo (multiplica as taxas do período
diretamente); ele serve para conferência e para a memória de cálculo.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal, localcontext

from calculo_judicial.datas import parse_data, somar_meses
from calculo_judicial.decimais import CONTEXTO, UM, fator_de_taxa, q10


def recalcular_fatores(conn: sqlite3.Connection, indice: str) -> int:
    """Recalcula ``fator_acumulado`` de todas as linhas do índice.

    Se houver lacuna na série, os meses depois da lacuna ficam com fator NULL
    (não se inventa taxa para atravessar a lacuna). Devolve o número de linhas
    com fator calculado.
    """
    linhas = conn.execute(
        "SELECT mes_referencia, taxa_pct FROM taxas_mensais WHERE indice_codigo = ? ORDER BY mes_referencia",
        (indice,),
    ).fetchall()
    fator = UM
    esperado: date | None = None
    atualizacoes: list[tuple[str | None, str, str]] = []
    quebrou = False
    calculados = 0
    with localcontext(CONTEXTO):
        for mes_txt, taxa_txt in linhas:
            mes = parse_data(mes_txt)
            if esperado is not None and mes != esperado:
                quebrou = True
            esperado = somar_meses(mes, 1)
            if quebrou:
                atualizacoes.append((None, indice, mes_txt))
                continue
            fator = q10(fator * fator_de_taxa(Decimal(taxa_txt)))
            atualizacoes.append((format(fator, "f"), indice, mes_txt))
            calculados += 1
    conn.executemany(
        "UPDATE taxas_mensais SET fator_acumulado = ? WHERE indice_codigo = ? AND mes_referencia = ?",
        atualizacoes,
    )
    conn.commit()
    return calculados


def fator_entre(conn: sqlite3.Connection, indice: str, mes_inicial: date, mes_final: date) -> Decimal:
    """Fator acumulado dos meses ``mes_inicial`` a ``mes_final`` (inclusive), multiplicando as taxas.

    Função de conferência; levanta ``LookupError`` se faltar mês.
    """
    linhas = conn.execute(
        "SELECT mes_referencia, taxa_pct FROM taxas_mensais WHERE indice_codigo = ? "
        "AND mes_referencia >= ? AND mes_referencia <= ? ORDER BY mes_referencia",
        (indice, mes_inicial.isoformat(), mes_final.isoformat()),
    ).fetchall()
    fator = UM
    esperado = mes_inicial
    with localcontext(CONTEXTO):
        for mes_txt, taxa_txt in linhas:
            mes = parse_data(mes_txt)
            if mes != esperado:
                raise LookupError(f"{indice}: falta o mês {esperado.strftime('%m/%Y')}")
            fator = q10(fator * fator_de_taxa(Decimal(taxa_txt)))
            esperado = somar_meses(mes, 1)
    if esperado != somar_meses(mes_final, 1):
        raise LookupError(f"{indice}: falta o mês {esperado.strftime('%m/%Y')}")
    return fator
