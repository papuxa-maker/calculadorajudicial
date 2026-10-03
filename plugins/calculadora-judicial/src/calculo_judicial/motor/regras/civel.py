"""Regras do contencioso cível (SPEC, seção 6): corte de 30/08/2024, Súmulas 43, 54 e 362.

Linha do tempo de cada parcela:

- correção desde a data-base (efetivo prejuízo — Súmula 43/STJ; no dano moral,
  o arbitramento — Súmula 362/STJ);
- juros desde o termo inicial (citação na responsabilidade contratual — CC,
  art. 405; evento na extracontratual — CC, art. 398 e Súmula 54/STJ; vencimento
  na obrigação líquida e com termo — CC, art. 397; trânsito em julgado para
  honorários fixados em quantia certa — CPC, art. 85, §16);
- até 29/08/2024: índice do perfil (padrão TJGO) + juros de 1% a.m. simples;
- desde 30/08/2024: IPCA + taxa legal (Lei 14.905/2024).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from calculo_judicial.indices.modelo import nome_curto
from calculo_judicial.motor.parcela import Parametros, Parcela, Periodo
from calculo_judicial.motor.regras.comum import (
    ErroRegra,
    corte_lei_14905_no_calculo,
    fatiar,
    inicio_cc_2002,
    juntar_iguais,
    periodo,
)

JUROS_LEGAIS_ANTERIORES_AM = Decimal("1")

BASE_CC_1916 = (
    "CC/1916, art. 1.062: juros legais de 6% a.a. (0,5% a.m.) até 10/01/2003 (STJ, REsp 1.111.117/PR); "
    "correção pela tabela do tribunal"
)

BASE_REGIME_ANTERIOR = (
    "CC, art. 406 (redação original) c/c CTN, art. 161, §1º: juros de 1% a.m. simples; "
    "correção pela tabela do tribunal (TJGO: Prov. CGJ-GO 06/2019, art. 7º — INPC)"
)
BASE_JUROS_ANTERIOR = "CC, art. 406 (redação original) c/c CTN, art. 161, §1º: juros de 1% a.m. simples"
BASE_SEM_JUROS = "sem juros antes do termo inicial dos juros"
BASE_SEM_CORRECAO = "sem correção antes da data-base (dano moral: desde o arbitramento — STJ, Súmula 362)"


def base_correcao(indice: str | None) -> str:
    """Fundamento da correção no trecho anterior à Lei 14.905/2024 (o índice escolhido para o processo)."""
    if indice is None:
        return BASE_SEM_CORRECAO
    if indice == "TJGO":
        return "correção pela tabela do tribunal (TJGO: Prov. CGJ-GO 06/2019, art. 7º — INPC)"
    return f"correção pelo índice do tribunal ou do título ({nome_curto(indice)})"


def base_regime_anterior(indice: str | None, juros: bool) -> str:
    """Base legal do trecho de 11/01/2003 a 29/08/2024 conforme o que de fato corre nele."""
    return f"{BASE_JUROS_ANTERIOR if juros else BASE_SEM_JUROS}; {base_correcao(indice)}"
BASE_TEMA_1368 = (
    "STJ, Tema 1.368 (REsp 2.199.164, Corte Especial, 29/10/2025): antes da Lei 14.905/2024 os juros "
    "do art. 406 do CC são a SELIC, que não se cumula com correção monetária"
)
BASE_LEI_14905 = (
    "Lei 14.905/2024: CC, art. 389, p.ú. (correção pelo IPCA) e art. 406 (juros pela taxa legal = "
    "SELIC − IPCA, piso zero; Res. CMN 5.171/2024, juros simples)"
)


def termo_inicial_correcao(parcela: Parcela) -> date:
    """Correção desde a data-base da parcela.

    Súmula 43/STJ: "Incide correção monetária sobre dívida por ato ilícito a partir
    da data do efetivo prejuízo". Dano moral: Súmula 362/STJ, correção desde o
    arbitramento — a data-base da parcela de dano moral é a data do arbitramento.
    """
    return parcela.data_base


def termo_inicial_juros(parcela: Parcela, params: Parametros) -> date | None:
    """Termo inicial dos juros de mora no cível.

    - Fixado no título (``parcela.inicio_juros``): prevalece.
    - Custas: sem juros (só correção desde o desembolso).
    - Honorários em quantia certa: trânsito em julgado (CPC, art. 85, §16).
    - Dano moral: na responsabilidade extracontratual, evento danoso (Súmula 54/STJ;
      CC, art. 398); na contratual, citação (CC, art. 405 — jurisprudência pacífica do
      STJ, que restringe a Súmula 54 ao ilícito extracontratual).
    - Principal: ``params.termo_inicial_juros`` se informado; senão citação na
      responsabilidade contratual (CC, art. 405; CPC, art. 240) e evento na
      extracontratual (CC, art. 398; Súmula 54/STJ). "vencimento" = mora ex re
      da obrigação positiva e líquida com termo (CC, art. 397).
    - Exceto no dano moral, os juros nunca começam antes da data-base: não há mora
      antes do vencimento (CC, arts. 394 e 397) — parcelas vincendas (CPC, art. 323)
      e honorários fixados depois do trânsito têm juros só desde a própria data.
    """
    if parcela.inicio_juros:
        return parcela.inicio_juros
    if parcela.natureza == "custas":
        return None
    if parcela.natureza == "honorarios":
        if params.data_transito is None:
            raise ErroRegra("honorários em quantia certa: juros desde o trânsito em julgado (CPC, art. 85, §16) — informe a data do trânsito em julgado")
        return max(params.data_transito, parcela.data_base)
    termo = params.termo_inicial_juros or ("citacao" if params.responsabilidade == "contratual" else "evento")
    if parcela.natureza == "dano_moral":
        if termo == "evento":
            if parcela.data_evento is None:
                raise ErroRegra(f"{parcela.descricao}: dano moral extracontratual exige a data do evento danoso (juros desde o evento, Súmula 54/STJ)")
            return parcela.data_evento
        if termo == "citacao":
            if params.data_citacao is None:
                raise ErroRegra("dano moral contratual: juros desde a citação (CC, art. 405) — informe a data da citação")
            return params.data_citacao
    data = _termo_principal(parcela, params, termo)
    return max(data, parcela.data_base) if data else data


def _termo_principal(parcela: Parcela, params: Parametros, termo: str) -> date:
    if termo == "citacao":
        if params.data_citacao is None:
            raise ErroRegra("juros desde a citação (CC, art. 405): informe a data da citação")
        return params.data_citacao
    if termo == "vencimento":
        return parcela.data_base
    if termo == "evento":
        return parcela.data_evento or parcela.data_base
    if termo == "transito":
        if params.data_transito is None:
            raise ErroRegra("juros desde o trânsito em julgado: informe a data do trânsito em julgado")
        return params.data_transito
    raise ErroRegra(f"termo inicial de juros desconhecido: {termo}")


def juros_legais_cc_1916() -> Decimal:
    """Juros legais de 6% a.a. (0,5% a.m.) até 10/01/2003 — CC/1916, art. 1.062 (STJ, REsp 1.111.117/PR, Corte Especial)."""
    return Decimal("0.5")


def periodo_cc_1916(de: date, fim: date, params: Parametros, corrige: bool, juros: bool) -> Periodo:
    """Trecho anterior à vigência do CC/2002 (11/01/2003): índice do perfil + juros de 0,5% a.m. simples."""
    indice = params.indice_correcao_civel if corrige else None
    if juros:
        return periodo(
            de, fim, params.data_calculo, indice, "simples", taxa_juros_am=juros_legais_cc_1916(),
            base_legal=BASE_CC_1916, descricao=f"{nome_curto(indice) or 'sem correção'} + juros de 0,5% a.m. simples (CC/1916)",
        )
    return periodo(
        de, fim, params.data_calculo, indice, "nenhum", base_legal=f"{BASE_SEM_JUROS}; {base_correcao(indice)}",
        descricao=f"{nome_curto(indice) or 'sem correção'}, sem juros (antes do termo inicial dos juros)",
    )


def periodo_regime_anterior(de: date, fim: date, params: Parametros, corrige: bool, juros: bool) -> Periodo:
    """Trecho de 11/01/2003 a 29/08/2024 (CC/2002, antes da Lei 14.905/2024).

    Padrão (SPEC): índice do perfil (``params.indice_correcao_civel``, padrão TJGO)
    + juros simples de 1% a.m. (CC, art. 406, redação original, c/c CTN, art. 161, §1º).
    Com ``params.juros_civel_pre_14905 == 'selic_tema_1368'``: SELIC desde o termo
    inicial dos juros, sem outro índice (STJ, Tema 1.368).
    """
    indice = params.indice_correcao_civel if corrige else None
    if juros and params.juros_civel_pre_14905 == "selic_tema_1368":
        if not corrige:
            raise ErroRegra(
                "juros pela SELIC (Tema 1.368) antes do termo inicial da correção (ex.: dano moral com evento "
                "anterior ao arbitramento): a SELIC embute correção; decisão aberta — edite os períodos manualmente"
            )
        return periodo(
            de, fim, params.data_calculo, "SELIC", "embutido",
            capitalizacao_correcao=params.capitalizacao_selic, base_legal=BASE_TEMA_1368,
            descricao="SELIC (Tema 1.368/STJ), sem outro índice",
        )
    if juros:
        return periodo(
            de, fim, params.data_calculo, indice, "simples", taxa_juros_am=JUROS_LEGAIS_ANTERIORES_AM,
            base_legal=base_regime_anterior(indice, True),
            descricao=f"{nome_curto(indice) or 'sem correção'} + juros de 1% a.m. simples",
        )
    return periodo(
        de, fim, params.data_calculo, indice, "nenhum", base_legal=base_regime_anterior(indice, False),
        descricao=f"{nome_curto(indice) or 'sem correção'}, sem juros (antes do termo inicial dos juros)",
    )


def periodo_lei_14905(de: date, fim: date, params: Parametros, corrige: bool, juros: bool) -> Periodo:
    """Trecho desde 30/08/2024: IPCA (CC, art. 389, p.ú.) + taxa legal (CC, art. 406; Res. CMN 5.171/2024).

    A taxa legal incide sobre o valor corrigido (Res. 5.171, art. 7º) e se acumula
    em regime simples (art. 6º). Antes do termo inicial da correção (dano moral
    antes do arbitramento) a taxa legal incide sobre o valor nominal.
    """
    indice = params.indice_correcao_pos_14905 if corrige else None
    return periodo(
        de, fim, params.data_calculo, indice, "taxa_legal" if juros else "nenhum",
        indice_juros="TAXA_LEGAL" if juros else None, base_legal=base_lei_14905(indice, juros),
        descricao=f"{nome_curto(indice) or 'sem correção'}" + (" + taxa legal (juros simples)" if juros else ", sem juros"),
    )


def base_lei_14905(indice: str | None, juros: bool) -> str:
    """Base legal do trecho desde 30/08/2024 conforme o que de fato corre nele."""
    if indice is None and juros:
        return ("Lei 14.905/2024: CC, art. 406 (juros pela taxa legal = SELIC − IPCA, piso zero; Res. CMN 5.171/2024, "
                f"juros simples); {BASE_SEM_CORRECAO}")
    if indice == "IPCA" and juros:
        return BASE_LEI_14905
    correcao = ("CC, art. 389, p.ú. (correção pelo IPCA)" if indice == "IPCA"
                else f"correção pelo {nome_curto(indice)} (critério escolhido; o art. 389, p.ú., do CC fixa o IPCA)"
                if indice else BASE_SEM_CORRECAO)
    juros_txt = ("art. 406 (juros pela taxa legal = SELIC − IPCA, piso zero; Res. CMN 5.171/2024, juros simples)"
                 if juros else BASE_SEM_JUROS)
    return f"Lei 14.905/2024: {correcao}; {juros_txt}"


def montar_periodos(parcela: Parcela, params: Parametros) -> list[Periodo]:
    """Períodos de uma parcela cível com os cortes legais automáticos (SPEC, seção 6)."""
    inicio_correcao = termo_inicial_correcao(parcela)
    inicio_juros = termo_inicial_juros(parcela, params)
    inicio = min(inicio_correcao, inicio_juros) if inicio_juros else inicio_correcao
    if inicio >= params.data_calculo:
        return []
    corte = corte_lei_14905_no_calculo(params.pro_rata)  # sem pró-rata, agosto/2024 inteiro na regra anterior
    cc2002 = inicio_cc_2002()
    periodos = []
    for a, b in fatiar(inicio, params.data_calculo, [inicio_correcao, inicio_juros, cc2002, corte]):
        corrige = a >= inicio_correcao
        juros = inicio_juros is not None and a >= inicio_juros
        if a < cc2002:
            periodos.append(periodo_cc_1916(a, b, params, corrige, juros))
        elif a < corte:
            periodos.append(periodo_regime_anterior(a, b, params, corrige, juros))
        else:
            periodos.append(periodo_lei_14905(a, b, params, corrige, juros))
    return juntar_iguais(periodos)
