"""Juros de mora: simples, compostos, por índice e SELIC embutida (SPEC, seção 6).

Todas as funções aplicam os juros sobre a ``base`` recebida, que o motor passa
já corrigida ("juros sobre o corrigido"). As variantes ``pct_*`` devolvem só o
percentual acumulado (como fração: 0,01 = 1%).

Contagem de meses (juros simples e compostos): diferença entre os meses de
``de`` e ``ate`` — os mesmos meses de competência da correção (o mês de ``de``
entra, o de ``ate`` não). Ex.: 15/03/2023 → 01/10/2026 = 43 meses (mar/2023 a
set/2026). Com ``pro_rata``, soma-se (dia de ``ate`` − dia de ``de``) / 30, o que
dá a contagem exata em meses e dias (42 meses e 16 dias = 42,533). Assim cada mês
pertence a um único regime de juros nos cortes legais (ex.: 30/08/2024), sem
contar agosto duas vezes. É também a contagem do Manual de Cálculos da JF
(número de meses entre o mês inicial e o da conta).

Juros por índice: acumula as taxas mensais do índice entre ``de`` e ``ate`` na
mesma convenção de meses da correção. A forma de acumulação depende do índice:

- TAXA_LEGAL: SOMA SIMPLES das taxas mensais, com pró-rata por dias corridos —
  Res. CMN 5.171/2024, art. 6º (confere com a Calculadora do Cidadão, aba Taxa
  Legal: 30/08/2024 a 01/10/2026 = 17,818065%). O SPEC dizia "acumula como se
  fosse correção" (produto); a resolução manda juros simples.
- JUROS_POUPANCA: soma simples (Manual de Cálculos da JF 2026, 4.2.2:
  "capitalizados de forma simples").
- TR (art. 39, caput, da Lei 8.177/91, "TRD acumulada"): produto, até que um
  caso-ouro do PJe-Calc indique outra coisa.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext

from calculo_judicial.datas import indice_mes
from calculo_judicial.decimais import CEM, CONTEXTO, UM, ZERO, q10
from calculo_judicial.indices.modelo import RepositorioIndices
from calculo_judicial.motor.correcao import ItemMes, detalhar, fator_de_itens

DIAS_MES_COMERCIAL = Decimal(30)

CAPITALIZACAO_PADRAO_JUROS = {
    "TAXA_LEGAL": "simples",  # Res. CMN 5.171/2024, art. 6º
    "JUROS_POUPANCA": "simples",  # Manual CJF 2026, 4.2.2
    "TR": "composta",  # art. 39, caput, Lei 8.177/91 ("TRD acumulada") — provisório
    "SELIC": "simples",  # Manual CJF 2026; Lei 9.250/95, art. 39, §4º ("acumulada mensalmente")
}


def capitalizacao_padrao(indice: str) -> str:
    return CAPITALIZACAO_PADRAO_JUROS.get(indice, "composta")


def meses_juros(de: date, ate: date, pro_rata: bool = False) -> Decimal:
    """Número de meses de juros entre ``de`` e ``ate``.

    Sem pró-rata: diferença entre os meses (meses de competência de ``de`` até o
    mês anterior a ``ate``). Com pró-rata: + (dia de ``ate`` − dia de ``de``) / 30.
    """
    if ate <= de:
        return ZERO
    meses = indice_mes(ate) - indice_mes(de)
    with localcontext(CONTEXTO):
        n = Decimal(meses)
        if pro_rata and ate.day != de.day:
            n += Decimal(ate.day - de.day) / DIAS_MES_COMERCIAL
        return max(n, ZERO)


def pct_simples(taxa_am: Decimal, de: date, ate: date, pro_rata: bool = False) -> Decimal:
    """Percentual (fração) de juros simples: taxa × meses."""
    with localcontext(CONTEXTO):
        return q10(taxa_am / CEM * meses_juros(de, ate, pro_rata))


def simples(base: Decimal, taxa_am: Decimal, de: date, ate: date, pro_rata: bool = False) -> Decimal:
    """Juros simples: ``base × taxa × meses`` (SPEC). Juros de mora não se capitalizam (CC, art. 406; CTN, art. 161, §1º)."""
    with localcontext(CONTEXTO):
        return q10(base * pct_simples(taxa_am, de, ate, pro_rata))


def pct_composto(taxa_am: Decimal, de: date, ate: date, pro_rata: bool = False) -> Decimal:
    """Percentual (fração) de juros compostos: (1 + taxa)^meses − 1."""
    n = meses_juros(de, ate, pro_rata)
    with localcontext(CONTEXTO):
        base = UM + taxa_am / CEM
        if n == n.to_integral_value():
            fator = base ** int(n)
        else:
            fator = (n * base.ln()).exp()
        return q10(fator - UM)


def composto(base: Decimal, taxa_am: Decimal, de: date, ate: date, pro_rata: bool = False) -> Decimal:
    """Juros compostos: ``base × ((1 + taxa)^meses − 1)`` (SPEC). Só quando o título manda capitalizar."""
    with localcontext(CONTEXTO):
        return q10(base * pct_composto(taxa_am, de, ate, pro_rata))


def itens_por_indice(
    indice: str,
    de: date,
    ate: date,
    repo: RepositorioIndices,
    pro_rata: bool = False,
    permitir_projecao: bool = False,
) -> list[ItemMes]:
    return detalhar(indice, de, ate, repo, pro_rata, permitir_projecao)


def pct_por_indice(
    indice: str,
    de: date,
    ate: date,
    repo: RepositorioIndices,
    capitalizacao: str | None = None,
    pro_rata: bool = False,
    permitir_projecao: bool = False,
) -> Decimal:
    """Percentual (fração) de juros pelo índice ``indice`` entre ``de`` e ``ate``.

    ``capitalizacao`` None usa o padrão do índice (ver ``CAPITALIZACAO_PADRAO_JUROS``).
    """
    itens = itens_por_indice(indice, de, ate, repo, pro_rata, permitir_projecao)
    if not itens:
        return ZERO
    return q10(fator_de_itens(itens, capitalizacao or capitalizacao_padrao(indice)) - UM)


def por_indice(
    base: Decimal,
    indice: str,
    de: date,
    ate: date,
    repo: RepositorioIndices,
    capitalizacao: str | None = None,
    pro_rata: bool = False,
    permitir_projecao: bool = False,
) -> Decimal:
    """Juros pelo índice de juros (TAXA_LEGAL, TR, JUROS_POUPANCA) entre ``de`` e ``ate``.

    Acumula o índice de juros na convenção de meses da correção (SPEC) e aplica
    sobre ``base``. Taxa legal: soma simples (Res. CMN 5.171/2024, art. 6º).
    """
    with localcontext(CONTEXTO):
        return q10(base * pct_por_indice(indice, de, ate, repo, capitalizacao, pro_rata, permitir_projecao))


def pct_selic_embutida(
    de: date,
    ate: date,
    repo: RepositorioIndices,
    capitalizacao: str = "simples",
    pro_rata: bool = False,
    permitir_projecao: bool = False,
    indice: str = "SELIC",
) -> Decimal:
    """Acréscimo da SELIC no período (fração), que engloba correção e juros.

    ``tipo_juros == 'embutido'`` (SPEC): no período só incide a SELIC; não há
    outro índice de correção nem juros de mora (EC 113/2021, art. 3º; STJ, Tema
    1.368; ADC 58). Padrão: soma simples das taxas mensais (SGS 4390), como o
    Manual de Cálculos da JF e a Receita Federal.
    """
    itens = detalhar(indice, de, ate, repo, pro_rata, permitir_projecao)
    if not itens:
        return ZERO
    return q10(fator_de_itens(itens, capitalizacao) - UM)
