"""Regras do contencioso trabalhista (SPEC, seção 6): ADC 58 + Lei 14.905/2024.

- Fase pré-judicial (vencimento → ajuizamento): IPCA-E + juros do art. 39, caput,
  da Lei 8.177/91 (TR acumulada) — STF, ADC 58/59 e ADIs 5.867/6.021 (18/12/2020),
  com os embargos de declaração (out/2021) que fixaram o AJUIZAMENTO como marco.
- Fase judicial até 29/08/2024: SELIC, que engloba correção e juros (sem outro
  índice) — mesma decisão.
- Desde 30/08/2024: IPCA (CC, art. 389, p.ú.) + taxa legal (CC, art. 406) — TST,
  SBDI-1, E-ED-RR-713-03.2010.5.04.0029, rel. Min. Agra Belmonte, j. 17/10/2024
  (Informativo TST 294).

Ajuizamento depois de 30/08/2024: o SPEC não previu. Padrão
(``trabalhista_pre_judicial_ate_corte=True``): a fase pré-judicial com IPCA-E + TR
vai só até 29/08/2024, e daí em diante vale IPCA + taxa legal em qualquer fase
(leitura literal do item "c" da SBDI-1). Com ``False``: IPCA-E + TR até o
ajuizamento, qualquer que seja a data.

Juros pré-judiciais (TR) e SELIC: no PJe-Calc a SELIC da fase judicial não incide
sobre os juros pré-judiciais (``selic_sobre_juros=False``) — a confirmar com o
caso-ouro do PJe-Calc (etapa 5).
"""

from __future__ import annotations

from datetime import date

from calculo_judicial.indices.modelo import nome_curto
from calculo_judicial.motor.parcela import Parametros, Parcela, Periodo
from calculo_judicial.motor.regras.comum import ErroRegra, corte_lei_14905, fatiar, juntar_iguais, periodo

BASE_PRE_JUDICIAL = (
    "STF, ADC 58/59 e ADIs 5.867/6.021 (j. 18/12/2020; ED out/2021): fase pré-judicial com IPCA-E "
    "e juros do art. 39, caput, da Lei 8.177/91 (TR acumulada)"
)
BASE_SELIC_JUDICIAL = (
    "STF, ADC 58/59 (ED out/2021): a partir do ajuizamento, SELIC (CC, art. 406), que engloba "
    "correção e juros, sem cumulação com outro índice"
)
BASE_LEI_14905 = (
    "TST, SBDI-1, E-ED-RR-713-03.2010.5.04.0029 (j. 17/10/2024): desde 30/08/2024, IPCA (CC, art. 389, "
    "p.ú.) e juros pela taxa legal (CC, art. 406; Res. CMN 5.171/2024)"
)


def fim_fase_pre_judicial(params: Parametros) -> date:
    """Primeiro dia após a fase pré-judicial: o ajuizamento (ADC 58, ED) ou, se posterior e
    ``trabalhista_pre_judicial_ate_corte``, 30/08/2024 (TST, SBDI-1)."""
    if params.data_ajuizamento is None:
        raise ErroRegra("trabalhista: informe a data do ajuizamento (marco da ADC 58)")
    if params.trabalhista_pre_judicial_ate_corte:
        return min(params.data_ajuizamento, corte_lei_14905())
    return params.data_ajuizamento


def periodo_pre_judicial(de: date, fim: date, params: Parametros) -> Periodo:
    """IPCA-E + juros pela TR acumulada (ADC 58; Lei 8.177/91, art. 39, caput)."""
    return periodo(
        de, fim, params.data_calculo, "IPCA_E", "indice", indice_juros="TR",
        base_legal=BASE_PRE_JUDICIAL, descricao="IPCA-E + juros pela TR (fase pré-judicial)",
    )


def periodo_selic_judicial(de: date, fim: date, params: Parametros) -> Periodo:
    """SELIC embutida do ajuizamento a 29/08/2024 (ADC 58)."""
    return periodo(
        de, fim, params.data_calculo, "SELIC", "embutido", capitalizacao_correcao=params.capitalizacao_selic,
        selic_sobre_juros=False, base_legal=BASE_SELIC_JUDICIAL, descricao="SELIC (correção + juros), fase judicial",
    )


def periodo_lei_14905(de: date, fim: date, params: Parametros) -> Periodo:
    """IPCA + taxa legal desde 30/08/2024 (TST, SBDI-1, E-ED-RR-713-03.2010.5.04.0029)."""
    return periodo(
        de, fim, params.data_calculo, params.indice_correcao_pos_14905, "taxa_legal", indice_juros="TAXA_LEGAL",
        base_legal=BASE_LEI_14905, descricao=f"{nome_curto(params.indice_correcao_pos_14905)} + taxa legal (juros simples)",
    )


def montar_periodos(parcela: Parcela, params: Parametros) -> list[Periodo]:
    """Três períodos: vencimento → ajuizamento; ajuizamento → 29/08/2024; 30/08/2024 → cálculo (SPEC).

    A data-base é o vencimento da parcela. Salários: Súmula 381/TST — índice do mês
    subsequente ao da prestação, a partir do dia 1º (data-base = dia 1º do mês seguinte).
    """
    inicio = parcela.data_base
    if inicio >= params.data_calculo:
        return []
    corte = corte_lei_14905()
    fim_pre = fim_fase_pre_judicial(params)
    periodos = []
    for a, b in fatiar(inicio, params.data_calculo, [fim_pre, corte]):
        if a < fim_pre:
            periodos.append(periodo_pre_judicial(a, b, params))
        elif a < corte:
            periodos.append(periodo_selic_judicial(a, b, params))
        else:
            periodos.append(periodo_lei_14905(a, b, params))
    return juntar_iguais(periodos)
