"""Regras do tributário federal (SPEC, seção 6): SELIC + 1% no mês da restituição.

Lei 9.250/1995, art. 39, §4º: a partir de 01/01/1996, a restituição/compensação
de tributo pago indevidamente é "acrescida de juros equivalentes à taxa referencial
do Sistema Especial de Liquidação e de Custódia - SELIC para títulos federais,
acumulada mensalmente, calculados a partir da data do pagamento indevido ou a
maior até o mês anterior ao da compensação ou restituição e de 1% relativamente
ao mês em que estiver sendo efetuada". O Manual de Cálculos da JF 2026 (4.4) e a
Receita aplicam a SELIC do MÊS SEGUINTE ao recolhimento, em soma simples, sem
outro índice (CTN, art. 167, p.ú.; STJ, Tema 905, item 3.3). Nesta calculadora o
"mês da restituição" é o mês da data do cálculo (na atualização para outra data
o 1% é refeito).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from calculo_judicial.datas import primeiro_dia, proximo_mes
from calculo_judicial.motor.parcela import Parametros, Parcela, Periodo
from calculo_judicial.motor.regras.comum import ErroRegra, periodo

ACRESCIMO_MES_RESTITUICAO = Decimal("1")
INICIO_SELIC_TRIBUTARIA = date(1996, 1, 1)

BASE_RESTITUICAO = (
    "Lei 9.250/1995, art. 39, §4º: SELIC acumulada mensalmente do mês seguinte ao pagamento indevido "
    "até o mês anterior ao da restituição + 1% no mês da restituição (Manual CJF 2026, 4.4; soma simples)"
)


def inicio_selic_restituicao(data_pagamento: date) -> date:
    """Primeiro dia do mês seguinte ao do pagamento indevido (Manual CJF 4.4; prática da Receita)."""
    return proximo_mes(data_pagamento)


def periodo_selic_mais_um_por_cento(de: date, params: Parametros) -> Periodo:
    """SELIC embutida (soma simples) até o mês anterior ao cálculo + 1% no mês do cálculo."""
    return periodo(
        de, params.data_calculo, params.data_calculo, "SELIC", "embutido",
        capitalizacao_correcao=params.capitalizacao_selic, acrescimo_final_pct=ACRESCIMO_MES_RESTITUICAO,
        base_legal=BASE_RESTITUICAO, descricao="SELIC do mês seguinte ao pagamento + 1% no mês do cálculo",
    )


def montar_periodos(parcela: Parcela, params: Parametros) -> list[Periodo]:
    """Indébito (natureza principal): do mês seguinte ao pagamento até o cálculo, com o 1% final.

    O 1% do mês da restituição é próprio do indébito tributário. Custas e honorários
    devidos pela Fazenda seguem o regime das condenações em geral (regra da Fazenda
    Pública: IPCA-E até 11/2021 e SELIC da EC 113 depois; honorários com juros desde
    o trânsito — CPC, art. 85, §16).
    """
    if parcela.natureza != "principal":
        from calculo_judicial.motor.regras import fazenda

        return fazenda.montar_periodos(parcela, params)
    if parcela.data_base < INICIO_SELIC_TRIBUTARIA:
        raise ErroRegra(
            f"{parcela.descricao}: pagamento antes de 01/01/1996 (regime anterior à SELIC — UFIR e juros de 1% "
            "desde o trânsito, CTN art. 167, p.ú.); edite os períodos manualmente"
        )
    inicio = inicio_selic_restituicao(parcela.data_base)
    if inicio >= primeiro_dia(params.data_calculo):
        # pagamento no mês do cálculo, ou cálculo no próprio mês seguinte: nenhum mês de SELIC, só o 1%.
        # Com cálculo no dia 1º, o período tem duração zero (começa e termina na data do cálculo).
        de = max(parcela.data_base, primeiro_dia(params.data_calculo))
        return [
            Periodo(
                de=de, ate=params.data_calculo, indice_correcao="SELIC", tipo_juros="embutido",
                capitalizacao_correcao=params.capitalizacao_selic, acrescimo_final_pct=ACRESCIMO_MES_RESTITUICAO,
                base_legal=BASE_RESTITUICAO, descricao="sem mês de SELIC entre o pagamento e o cálculo: só o 1%",
            )
        ]
    return [periodo_selic_mais_um_por_cento(inicio, params)]
