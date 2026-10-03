"""Regras legais por área: cada uma monta os períodos de uma parcela (SPEC, seção 6)."""

from __future__ import annotations

from dataclasses import replace

from calculo_judicial.datas import formatar_data
from calculo_judicial.indices.modelo import nome_curto
from calculo_judicial.motor.parcela import Parametros, Parcela, Periodo
from calculo_judicial.motor.regras import civel, fazenda, trabalhista, tributario
from calculo_judicial.motor.regras.comum import ErroRegra, corte_lei_14905, inicio_ec_113

REGRAS = {
    "civel": civel,
    "trabalhista": trabalhista,
    "tributario": tributario,
    "fazenda": fazenda,
}

__all__ = ["REGRAS", "ErroRegra", "montar_periodos", "avisos_da_regra", "cortes_da_area", "marcos_da_parcela"]


def montar_periodos(parcela: Parcela, params: Parametros) -> list[Periodo]:
    try:
        modulo = REGRAS[params.area]
    except KeyError:
        raise ErroRegra(f"área desconhecida: {params.area}") from None
    return modulo.montar_periodos(parcela, params)


def marcos_da_parcela(parcela: Parcela, params: Parametros) -> str:
    """Desde quando a parcela tem correção e juros, com o fundamento (cível e Fazenda), para a memória e a conferência.

    Mesma regra de ``civel.termo_inicial_juros`` (que a Fazenda também usa); devolve "" se faltar dado para a regra.
    """
    if params.area != "civel":  # na Fazenda, desde a EC 113 a SELIC corre desde a data-base: os marcos ficam nos períodos
        return ""
    try:
        inicio_juros = civel.termo_inicial_juros(parcela, params)
    except ErroRegra:
        return ""
    correcao = f"correção desde {formatar_data(parcela.data_base)}"
    if parcela.natureza == "dano_moral":
        correcao += " (arbitramento — STJ, Súmula 362)"
    if inicio_juros is None:
        return correcao + "; sem juros (custas: só correção desde o desembolso)"
    return f"{correcao}; juros desde {formatar_data(inicio_juros)} ({_fundamento_juros(parcela, params, inicio_juros)})"


def _fundamento_juros(parcela: Parcela, params: Parametros, inicio) -> str:
    """Por que os juros começam em ``inicio`` — o mesmo caminho de ``civel.termo_inicial_juros``."""
    if parcela.inicio_juros:
        return "data fixada no título"
    if parcela.natureza == "honorarios":
        if params.data_transito and params.data_transito < inicio:
            return ("data da fixação, posterior ao trânsito em julgado: não há mora antes da fixação — CC, arts. 394 e "
                    "397 (se houve trânsito da decisão que fixou os honorários, informe-o como termo inicial)")
        return "trânsito em julgado — CPC, art. 85, §16"
    termo = params.termo_inicial_juros or ("citacao" if params.responsabilidade == "contratual" else "evento")
    if parcela.natureza == "dano_moral" and termo == "evento":
        return "evento danoso — STJ, Súmula 54"
    if parcela.natureza == "dano_moral" and termo == "citacao":
        return "citação — CC, art. 405"
    data_termo = {"citacao": params.data_citacao, "transito": params.data_transito,
                  "evento": parcela.data_evento}.get(termo)
    if inicio == parcela.data_base and (termo == "vencimento" or (data_termo and data_termo < parcela.data_base)):
        if parcela.natureza == "dano_moral":
            return "arbitramento: sem juros antes da data-base da parcela"
        if termo == "evento":
            return "data-base (efetivo prejuízo) posterior ao evento: sem mora antes dela — CC, arts. 394 e 397"
        if termo != "vencimento":
            return "vencimento posterior ao termo inicial: não há mora antes do vencimento — CC, art. 397"
    return {
        "citacao": "citação — CC, art. 405",
        "vencimento": "vencimento, mora ex re — CC, art. 397",
        "evento": "evento danoso — CC, art. 398; STJ, Súmula 54",
        "transito": "trânsito em julgado",
    }.get(termo, termo)


def avisos_da_regra(parcela: Parcela, params: Parametros) -> list[str]:
    """Avisos jurídicos que acompanham a memória (decisões abertas, critérios provisórios)."""
    saida: list[str] = []
    automatico = params.aplicar_cortes_legais
    if params.area == "civel":
        # só com os cortes automáticos: com períodos do título, a taxa de juros é a que o título fixou
        if (params.juros_civel_pre_14905 == "um_por_cento" and parcela.natureza != "custas" and automatico
                and not params.juros_fixados_no_titulo):
            saida.append(
                "Juros cíveis de 11/01/2003 a 29/08/2024 a 1% a.m. (CC, art. 406, redação original; antes, 0,5% a.m. do "
                "CC/1916), critério adotado. Para título omisso, o STJ fixou a SELIC como taxa do art. 406 antes da Lei "
                "14.905/2024 (Tema 1.368, repetitivo, 29/10/2025)"
            )
    if params.area == "trabalhista" and automatico:
        saida.append(
            "Trabalhista (ADC 58): juros da fase pré-judicial pela TR (Lei 8.177/91, art. 39), acumulada mês a mês; a "
            "SELIC da fase judicial incide só sobre o principal corrigido, não sobre esses juros"
        )
    if params.area == "fazenda" or (params.area == "tributario" and parcela.natureza != "principal"):
        saida.extend(fazenda.avisos(parcela, params))
    return saida


def cortes_da_area(params: Parametros, parcelas: list[Parcela] | tuple = ()) -> list[str]:
    """Descrição dos cortes legais automáticos que a área aplica (para a memória).

    No tributário, custas e honorários seguem a regra da Fazenda (``tributario.montar_periodos``).
    """
    corte = corte_lei_14905()
    if params.area == "civel":
        indice = nome_curto(params.indice_correcao_civel)
        if params.juros_civel_pre_14905 == "um_por_cento":
            antes = f"{indice} + juros de 1% a.m." + (" fixados no título" if params.juros_fixados_no_titulo else "") + (
                " (0,5% a.m. antes de 11/01/2003, CC/1916)")
        else:  # a SELIC substitui a correção desde o início dos juros (nunca se soma a outro índice)
            antes = (f"{indice} até o início dos juros e, desde então, só a SELIC (Tema 1.368, sem outro índice; antes de "
                     "11/01/2003, 0,5% a.m. do CC/1916)")
        if params.pro_rata:
            datas = (f"até {corte.replace(day=29):%d/%m/%Y}", f"desde {corte:%d/%m/%Y}", " (agosto/2024 dividido por dias)")
        else:
            datas = ("até 08/2024", "desde 09/2024",
                     " (a lei vale desde 30/08/2024; sem pró-rata, agosto/2024 fica inteiro na regra anterior, como nas "
                     "tabelas dos tribunais)")
        return [
            f"Lei 14.905/2024: {datas[0]} {antes}; {datas[1]} {nome_curto(params.indice_correcao_pos_14905)} + taxa legal"
            + datas[2],
        ]
    if params.area == "trabalhista":
        return [
            "ADC 58: IPCA-E + TR na fase pré-judicial; SELIC desde o ajuizamento",
            f"Lei 14.905/2024 (TST, SBDI-1): desde {corte:%d/%m/%Y} {nome_curto(params.indice_correcao_pos_14905)} + taxa legal",
        ]
    if params.area == "tributario":
        cortes = ["Lei 9.250/95, art. 39, §4º: SELIC do mês seguinte ao pagamento + 1% no mês do cálculo"]
        if any(p.natureza != "principal" for p in parcelas) or params.honorarios_fixos is not None:
            regra = cortes_da_area(replace(params, area="fazenda"))
            cortes.append("custas e honorários (regra da Fazenda Pública): " + "; ".join(regra))
        return cortes
    if params.area == "fazenda":
        cortes = [
            f"Temas 810/905: {nome_curto(params.indice_correcao_fazenda)} + juros da poupança até 08/12/2021",
            f"EC 113/2021: SELIC desde {inicio_ec_113():%d/%m/%Y}",
        ]
        if params.fazenda_regime_pos_ec136 == "ipca15_taxa_legal":
            cortes.append("EC 136/2025 + Tema 1.419 (Manual CJF 2026): IPCA-15 + taxa legal desde 01/09/2025")
        return cortes
    return []
