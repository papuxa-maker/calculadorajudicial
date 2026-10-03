"""Convenção de arredondamento do Manual de Cálculos da Justiça Federal (``arredondamento='cjf'``).

O Manual CJF 2026 (nota 6 do item 4.2.1.1) apresenta o "cálculo resumido":

    C = valor × coeficiente de correção (10 casas)          → truncado em centavos
    juros de cada regime = base × % do regime (2 casas)      → truncado em centavos
        base = C, ou (C + juros anteriores) na SELIC com consolidação
    total = C + Σ juros

e avisa que "eventuais diferenças de centavos ... são inerentes ao critério de
truncamento de casas decimais aplicado em cada etapa". O modelo do motor é o
mesmo (juros sobre o principal corrigido; SELIC sobre o crédito consolidado;
juros anteriores só corrigidos), então basta recompor o total de cada parcela
com os percentuais de cada período truncados. Conferido nos dois exemplos da
Fazenda Pública do Manual 2026 (R$ 3.484,95 em jun/2022 e R$ 5.218,28 em jun/2026).

Só se aplica a parcelas sem abatimentos (com abatimento o cálculo resumido do
Manual não se aplica; a parcela fica com os valores exatos e a memória avisa).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, localcontext

from calculo_judicial.decimais import CEM, CONTEXTO, ZERO
from calculo_judicial.motor import juros as mod_juros
from calculo_judicial.motor.memoria import LinhaMemoria, MemoriaParcela

CENTAVO = Decimal("0.01")
DUAS_CASAS_PCT = Decimal("0.0001")  # 2 casas no percentual = 4 casas na fração


def truncar_centavos(valor: Decimal) -> Decimal:
    with localcontext(CONTEXTO):
        return valor.quantize(CENTAVO, rounding=ROUND_DOWN)


def truncar_percentual(fracao: Decimal) -> Decimal:
    """Trunca um percentual (dado como fração) em 2 casas: 0,024567 (2,4567%) → 0,0245 (2,45%)."""
    with localcontext(CONTEXTO):
        return fracao.quantize(DUAS_CASAS_PCT, rounding=ROUND_DOWN)


def percentual_do_periodo(simulador, pa) -> Decimal:
    """Juros acumulados do período inteiro (fração), do início ao fim, sem truncar."""
    p = pa.periodo
    params = simulador.params
    if p.tipo_juros == "nenhum":
        return ZERO
    if p.tipo_juros == "embutido":
        s = mod_juros.pct_selic_embutida(
            pa.inicio, pa.fim_exclusivo, simulador.repo, p.capitalizacao_correcao, simulador._pro_rata(pa),
            params.permitir_projecao, p.indice_correcao or "SELIC",
        )
        if p.acrescimo_final_pct:
            s += p.acrescimo_final_pct / CEM
        return s
    if p.tipo_juros == "simples":
        return mod_juros.pct_simples(p.taxa_juros_am or ZERO, pa.inicio, pa.fim_exclusivo, params.pro_rata)
    if p.tipo_juros == "composto":
        return mod_juros.pct_composto(p.taxa_juros_am or ZERO, pa.inicio, pa.fim_exclusivo, params.pro_rata)
    indice = p.indice_juros or "TAXA_LEGAL"
    return mod_juros.pct_por_indice(
        indice, pa.inicio, pa.fim_exclusivo, simulador.repo, p.capitalizacao_juros, params.pro_rata, params.permitir_projecao,
    )


def recompor_cjf(simulador, memoria: MemoriaParcela) -> tuple[MemoriaParcela, str | None]:
    """Recompõe o total da parcela pelo cálculo resumido do Manual CJF (com truncamentos).

    Devolve a memória ajustada (com uma linha final de ajuste) e um aviso, se houver.
    """
    if memoria.capital_pago or memoria.juros_pago:
        return memoria, (
            f"{memoria.parcela.descricao}: com abatimento, o truncamento do Manual CJF não se aplica; valores exatos mantidos"
        )
    if not simulador.periodos or not simulador.ativa:
        return memoria, None
    with localcontext(CONTEXTO):
        coeficiente = simulador.F.quantize(Decimal("1E-10"))
        if simulador.params.preservar_valor_nominal and coeficiente < 1:
            coeficiente = Decimal(1)  # deflação acumulada: prevalece o nominal (Manual CJF 4.1.2.2)
        C = truncar_centavos(memoria.valor_original * coeficiente)
        componentes: list[Decimal] = []
        for pa in simulador.periodos:
            pct = percentual_do_periodo(simulador, pa)
            if not pct:
                continue
            pct = truncar_percentual(pct)
            base = C
            if pa.periodo.tipo_juros == "embutido" and pa.periodo.selic_sobre_juros and simulador.params.selic_como == "juros":
                base = C + sum(componentes, ZERO)
            componentes.append(truncar_centavos(base * pct))
        J = sum(componentes, ZERO)
    ultima = memoria.linhas[-1] if memoria.linhas else None
    if ultima is not None and (ultima.corrigido != C or ultima.juros_acumulados != J):
        memoria.linhas.append(
            LinhaMemoria(
                competencia=ultima.competencia,
                fim=ultima.fim,
                periodo=ultima.periodo,
                indice=None,
                taxa_indice_pct=None,
                fracao=ultima.fracao,
                fator_acumulado=coeficiente,
                base=memoria.valor_original,
                corrigido=C,
                tipo_juros="ajuste",
                taxa_juros_pct=ZERO,
                juros_acumulados=J,
                subtotal=C + J,
                observacao="ajuste de truncamento do Manual CJF (percentuais com 2 casas e centavos truncados)",
            )
        )
    memoria.principal_corrigido = C
    memoria.juros = J
    return memoria, None
