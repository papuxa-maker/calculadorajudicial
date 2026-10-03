"""Marcos legais e utilitários comuns às regras por área."""

from __future__ import annotations

from datetime import date, timedelta

from calculo_judicial.motor.parcela import Periodo

UM_DIA = timedelta(days=1)


class ErroRegra(ValueError):
    """A regra legal não pode montar os períodos com os dados informados (falta data, decisão aberta etc.)."""


def corte_lei_14905() -> date:
    """Primeiro dia do regime da Lei 14.905/2024: 30/08/2024.

    Lei 14.905, de 28/06/2024 (DOU 01/07/2024), art. 5º, II: as alterações dos arts.
    389 e 406 do Código Civil produzem efeitos 60 dias após a publicação. Pela LC
    95/1998, art. 8º, §1º, conta-se o dia da publicação e o último dia: 01/07 a
    29/08/2024 são os 60 dias, e o novo regime vale desde 30/08/2024. A Res. CMN
    5.171/2024, art. 8º, p.ú., aplica a taxa legal de ago/2024 aos dias 30 e 31/08.
    """
    return date(2024, 8, 30)


def corte_lei_14905_no_calculo(pro_rata: bool) -> date:
    """Onde o cálculo cível troca de regime na Lei 14.905/2024.

    Com pró-rata, na data legal (30/08/2024): agosto/2024 é dividido por dias — 29 dias na regra anterior e
    2 na nova (Res. CMN 5.171/2024, art. 8º, p.ú.). Sem pró-rata (meses cheios), agosto/2024 fica inteiro na
    regra anterior e a nova começa na competência 09/2024, como nas tabelas dos tribunais (ENCOGE, TJMG, TJDFT,
    TJMA, TJRO, TJRR, TJTO...): decisão do escritório de 03/10/2026 (PROGRESSO, D13a).
    """
    return corte_lei_14905() if pro_rata else date(2024, 9, 1)


def inicio_cc_2002() -> date:
    """Vigência do Código Civil de 2002: 11/01/2003 (Lei 10.406/2002, art. 2.044 — um ano após a publicação)."""
    return date(2003, 1, 11)


def inicio_ec_113() -> date:
    """Início da SELIC da EC 113/2021 (publicada no DOU de 09/12/2021), art. 3º."""
    return date(2021, 12, 9)


def inicio_pos_ec_136() -> date:
    """Primeiro mês após a SELIC da redação original da EC 113 (Manual CJF 2026: SELIC até a competência ago/2025).

    EC 136/2025 (DOU 10/09/2025) reescreveu o art. 3º da EC 113; o STF (Tema 1.419,
    ED julgados em maio/2026) limitou a SELIC ao período da redação original.
    """
    return date(2025, 9, 1)


def inicio_lei_11960() -> date:
    """Lei 11.960/2009 (DOU 30/06/2009) deu ao art. 1º-F da Lei 9.494/97 os juros da poupança."""
    return date(2009, 6, 30)


def fatiar(inicio: date, fim_exclusivo: date, cortes: list[date | None]) -> list[tuple[date, date]]:
    """Divide ``[inicio, fim_exclusivo)`` nos ``cortes`` (datas de início de cada novo trecho)."""
    pontos = sorted({c for c in cortes if c is not None and inicio < c < fim_exclusivo} | {inicio, fim_exclusivo})
    return [(a, b) for a, b in zip(pontos, pontos[1:]) if a < b]


def periodo(
    de: date,
    fim_exclusivo: date,
    data_calculo: date,
    indice_correcao: str | None,
    tipo_juros: str,
    **kw,
) -> Periodo:
    """Cria um ``Periodo`` com ``ate`` inclusivo; o último período termina na data do cálculo."""
    ate = data_calculo if fim_exclusivo >= data_calculo else fim_exclusivo - UM_DIA
    return Periodo(de=de, ate=ate, indice_correcao=indice_correcao, tipo_juros=tipo_juros, **kw)


def juntar_iguais(periodos: list[Periodo]) -> list[Periodo]:
    """Funde períodos contíguos com o mesmo critério (evita cortes sem efeito na memória)."""
    saida: list[Periodo] = []
    for p in periodos:
        if saida:
            q = saida[-1]
            mesmo = (
                q.indice_correcao == p.indice_correcao
                and q.tipo_juros == p.tipo_juros
                and q.taxa_juros_am == p.taxa_juros_am
                and q.indice_juros == p.indice_juros
                and q.capitalizacao_correcao == p.capitalizacao_correcao
                and q.capitalizacao_juros == p.capitalizacao_juros
                and q.acrescimo_final_pct is None
                and q.selic_sobre_juros == p.selic_sobre_juros
                and q.base_legal == p.base_legal
                and q.ate + UM_DIA == p.de
            )
            if mesmo:
                q.ate = p.ate
                q.acrescimo_final_pct = p.acrescimo_final_pct
                continue
        saida.append(p)
    return saida
