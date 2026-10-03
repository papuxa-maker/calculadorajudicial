"""Regras da Fazenda Pública (SPEC, seção 6): Temas 810/STF e 905/STJ + EC 113/2021.

- Até 08/12/2021 (condenações não tributárias): correção pelo IPCA-E (STF, RE
  870.947, Tema 810; STJ, Tema 905) e juros de mora da caderneta de poupança
  (Lei 9.494/97, art. 1º-F, red. Lei 11.960/2009), desde a citação.
  Os juros da poupança são só a remuneração adicional, sem a TR (Lei 8.177/91,
  art. 12, II: 0,5% a.m. ou 70% da meta SELIC mensalizada), capitalizados de forma
  simples — Manual de Cálculos da JF 2026, 4.2.2. O SPEC mandava usar a série 195
  (rentabilidade total, com TR); usamos o índice derivado JUROS_POUPANCA.
  Previdenciário: INPC (Lei 8.213/91, art. 41-A; Tema 905, 3.2) via
  ``params.indice_correcao_fazenda``.
- Desde 09/12/2021: SELIC uma única vez, que engloba correção e juros (EC 113/2021,
  art. 3º), incidindo também sobre os juros já vencidos (consolidação — Res. CNJ
  303/2019, art. 22, §1º, red. Res. CNJ 448/2022).
- Depois de ago/2025: a EC 136/2025 reescreveu o art. 3º da EC 113 e o STF (Tema
  1.419) limitou a SELIC ao período da redação original. O Manual CJF 2026 aplica
  IPCA-15 + taxa legal desde set/2025 (``fazenda_regime_pos_ec136='ipca15_taxa_legal'``).
  Padrão do SPEC: SELIC até o cálculo (``'selic'``) — DECISÃO ABERTA.

Juros com termo inicial antes de 30/06/2009 (Lei 11.960/2009): o Tema 905 prevê
0,5% a.m. até 12/2002 e SELIC de 01/2003 a 06/2009 — não automatizado; o motor
avisa e o usuário edita os períodos.
"""

from __future__ import annotations

from datetime import date

from calculo_judicial.indices.modelo import nome_curto
from calculo_judicial.motor.parcela import Parametros, Parcela, Periodo
from calculo_judicial.motor.regras.comum import (
    ErroRegra,
    fatiar,
    inicio_ec_113,
    inicio_lei_11960,
    inicio_pos_ec_136,
    juntar_iguais,
    periodo,
)

BASE_TEMAS_810_905 = (
    "STF, RE 870.947 (Tema 810) e STJ, Tema 905: correção pelo IPCA-E; juros da poupança (Lei 9.494/97, "
    "art. 1º-F, red. Lei 11.960/2009; Lei 8.177/91, art. 12, II), sem TR, capitalização simples (Manual CJF 4.2.2)"
)
BASE_EC_113 = (
    "EC 113/2021, art. 3º: SELIC uma única vez, acumulada mensalmente, que engloba correção e juros; "
    "consolidação dos juros vencidos (Res. CNJ 303/2019, art. 22, §1º, red. Res. CNJ 448/2022)"
)
BASE_POS_EC_136 = (
    "EC 136/2025 e STF, Tema 1.419 (SELIC restrita à redação original da EC 113); Manual CJF 2026 "
    "(Res. CJF 990/2026): IPCA-15 + taxa legal desde set/2025 (CC, arts. 389 e 406)"
)


def termo_inicial_juros(parcela: Parcela, params: Parametros) -> date | None:
    """Termo inicial dos juros contra a Fazenda: os mesmos do cível.

    Citação na responsabilidade contratual (CC, art. 405; CPC, art. 240); evento
    danoso na extracontratual do Estado (CF, art. 37, §6º; Súmula 54/STJ, aplicada
    também à Fazenda); dano moral pela responsabilidade; honorários desde o trânsito
    (CPC, art. 85, §16); custas sem juros. Ver ``civel.termo_inicial_juros``.
    """
    from calculo_judicial.motor.regras import civel

    return civel.termo_inicial_juros(parcela, params)


def periodo_temas_810_905(de: date, fim: date, params: Parametros, juros: bool, corrige: bool = True) -> Periodo:
    """IPCA-E (ou INPC no previdenciário) + juros da poupança, até 08/12/2021.

    Sem correção antes do termo inicial dela (dano moral antes do arbitramento — Súmula 362).
    """
    indice = params.indice_correcao_fazenda if corrige else None
    if juros:
        base = BASE_TEMAS_810_905 if indice else (
            "juros da poupança (Lei 9.494/97, art. 1º-F, red. Lei 11.960/2009; Lei 8.177/91, art. 12, II), sem TR, "
            "simples (Manual CJF 4.2.2); sem correção antes da data-base (dano moral: desde o arbitramento — STJ, "
            "Súmula 362)")
        return periodo(
            de, fim, params.data_calculo, indice, "indice", indice_juros="JUROS_POUPANCA",
            base_legal=base, descricao=f"{nome_curto(indice) or 'sem correção'} + juros da poupança (simples)",
        )
    base = ("correção pelo IPCA-E (STF, Tema 810; STJ, Tema 905)" if indice == "IPCA_E"
            else f"correção pelo {nome_curto(indice)} (STJ, Tema 905)" if indice
            else "sem correção antes da data-base (dano moral: desde o arbitramento — STJ, Súmula 362)")
    return periodo(
        de, fim, params.data_calculo, indice, "nenhum", base_legal=f"sem juros antes do termo inicial dos juros; {base}",
        descricao=f"{nome_curto(indice) or 'sem correção'}, sem juros (antes do termo inicial dos juros)",
    )


def periodo_ec_113(de: date, fim: date, params: Parametros) -> Periodo:
    """SELIC embutida desde 09/12/2021 (EC 113/2021, art. 3º), com consolidação dos juros vencidos."""
    return periodo(
        de, fim, params.data_calculo, "SELIC", "embutido", capitalizacao_correcao=params.capitalizacao_selic,
        selic_sobre_juros=True, base_legal=BASE_EC_113, descricao="SELIC (EC 113/2021), sem outro índice",
    )


def periodo_pos_ec_136(de: date, fim: date, params: Parametros, juros: bool, corrige: bool = True) -> Periodo:
    """IPCA-15 + taxa legal desde set/2025 (Manual CJF 2026), quando escolhido."""
    indice = "IPCA_15" if corrige else None
    base = BASE_POS_EC_136
    if not juros:
        base += "; sem juros antes do termo inicial dos juros"
    if not corrige:
        base += "; sem correção antes da data-base (dano moral: desde o arbitramento — STJ, Súmula 362)"
    return periodo(
        de, fim, params.data_calculo, indice, "taxa_legal" if juros else "nenhum",
        indice_juros="TAXA_LEGAL" if juros else None, base_legal=base,
        descricao=f"{nome_curto(indice) or 'sem correção'}" + (" + taxa legal" if juros else ", sem juros") + " (pós-EC 136/2025, Manual CJF 2026)",
    )


def montar_periodos(parcela: Parcela, params: Parametros) -> list[Periodo]:
    """Até 08/12/2021: IPCA-E + juros da poupança; de 09/12/2021: SELIC embutida (SPEC).

    A SELIC da EC 113 vale para toda a dívida desde 09/12/2021, inclusive antes da
    citação (é o índice único de correção e juros). Exceção: dano moral cujo
    arbitramento é posterior — a SELIC embutiria correção antes do arbitramento
    (Súmula 362); decisão aberta, períodos manuais.
    """
    inicio_correcao = parcela.data_base
    inicio_juros = termo_inicial_juros(parcela, params)
    inicio = min(inicio_correcao, inicio_juros) if inicio_juros else inicio_correcao
    if inicio >= params.data_calculo:
        return []
    ec113 = inicio_ec_113()
    pos136 = inicio_pos_ec_136() if params.fazenda_regime_pos_ec136 == "ipca15_taxa_legal" else None
    periodos = []
    for a, b in fatiar(inicio, params.data_calculo, [inicio_correcao, inicio_juros, ec113, pos136]):
        corrige = a >= inicio_correcao
        juros = inicio_juros is not None and a >= inicio_juros
        if a < ec113:
            periodos.append(periodo_temas_810_905(a, b, params, juros, corrige))
        elif pos136 is None or a < pos136:
            if not corrige:
                raise ErroRegra(
                    f"{parcela.descricao}: SELIC da EC 113 antes do termo inicial da correção (dano moral com evento "
                    "anterior ao arbitramento): a SELIC embute correção; decisão aberta — edite os períodos manualmente"
                )
            periodos.append(periodo_ec_113(a, b, params))
        else:
            periodos.append(periodo_pos_ec_136(a, b, params, juros, corrige))
    return juntar_iguais(periodos)


def avisos(parcela: Parcela, params: Parametros) -> list[str]:
    """Avisos jurídicos da regra (não alteram o cálculo)."""
    saida = []
    inicio_juros = None
    try:
        inicio_juros = termo_inicial_juros(parcela, params)
    except ErroRegra:
        pass
    if inicio_juros and inicio_juros < inicio_lei_11960():
        saida.append(
            f"{parcela.descricao}: juros desde {inicio_juros:%d/%m/%Y}, antes da Lei 11.960/2009; o cálculo usou os juros "
            "da poupança em todo o período (critério adotado). O Tema 905 prevê 0,5% a.m. até 12/2002 e SELIC de 01/2003 a "
            "06/2009"
        )
    if params.fazenda_regime_pos_ec136 == "selic" and params.data_calculo > inicio_pos_ec_136():
        saida.append(
            "Fazenda Pública: SELIC aplicada também depois de ago/2025 (EC 113/2021), critério adotado. Com a EC 136/2025 e "
            "o Tema 1.419/STF, o Manual de Cálculos da Justiça Federal 2026 adota IPCA-15 + taxa legal desde set/2025"
        )
    return saida
