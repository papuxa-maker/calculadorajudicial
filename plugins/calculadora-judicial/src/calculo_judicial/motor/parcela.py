"""Modelo de dados do motor de cálculo (SPEC, seção 6).

Os campos do SPEC estão todos aqui, com os mesmos nomes. Campos marcados como
"extensão" não estavam no SPEC; todos têm valor padrão (o comportamento sem
eles é o do SPEC, salvo onde a lei ou a resolução aplicável exigem outra coisa,
o que está dito no campo) e aparecem na memória de cálculo quando usados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

NATUREZAS = ("principal", "dano_moral", "custas", "honorarios")
NOMES_NATUREZA = {"principal": "principal", "dano_moral": "dano moral", "custas": "custas", "honorarios": "honorários"}
TIPOS_JUROS = ("nenhum", "simples", "composto", "taxa_legal", "indice", "embutido")
AREAS = ("civel", "trabalhista", "tributario", "fazenda")
IMPUTACOES = ("juros_primeiro", "capital_primeiro", "proporcional")
CAPITALIZACOES = ("composta", "simples")


@dataclass
class Periodo:
    """Trecho da vida da parcela com um critério de correção e de juros.

    ``de`` e ``ate`` são inclusivos (``ate`` = último dia do período). Convenção
    de meses (CLAUDE.md): o mês de ``de`` entra inteiro no período; o mês do dia
    seguinte a ``ate`` já pertence ao período seguinte. O último período termina
    na data do cálculo, cujo mês não entra (corrigir de A até B acumula de A até
    o mês anterior a B).

    ``tipo_juros``:
      - ``nenhum``;
      - ``simples``: ``taxa_juros_am`` % ao mês, sem capitalização;
      - ``composto``: ``taxa_juros_am`` % ao mês, capitalizada;
      - ``taxa_legal``: juros pelo índice TAXA_LEGAL (art. 406 do CC);
      - ``indice`` (extensão): juros por outro índice (TR, JUROS_POUPANCA...);
      - ``embutido``: SELIC no período, que já engloba correção e juros.
    """

    de: date
    ate: date
    indice_correcao: str | None
    tipo_juros: str
    taxa_juros_am: Decimal | None = None
    indice_juros: str | None = None
    # --- extensões ---
    capitalizacao_correcao: str = "composta"  # 'simples' = 1 + Σ taxas (SELIC "acumulada mensalmente", CJF/Receita)
    capitalizacao_juros: str | None = None  # juros por índice: None = padrão do índice (ver juros.py)
    acrescimo_final_pct: Decimal | None = None  # tributário: 1% no mês do cálculo (Lei 9.250/95, art. 39, §4º)
    selic_sobre_juros: bool = True  # SELIC embutida incide também sobre os juros já vencidos (consolidação)
    base_legal: str = ""
    descricao: str = ""


@dataclass
class Parcela:
    descricao: str
    valor_original: Decimal
    data_base: date  # vencimento, evento danoso ou arbitramento
    natureza: str  # 'principal' | 'dano_moral' | 'custas' | 'honorarios'
    periodos: list[Periodo] = field(default_factory=list)  # preenchidos pela regra da área ou manualmente
    # --- extensões ---
    data_evento: date | None = None  # evento danoso (dano moral/extracontratual): termo inicial dos juros
    inicio_juros: date | None = None  # termo inicial dos juros fixado no título, se diferente do padrão


@dataclass
class Abatimento:
    data: date
    valor: Decimal
    descricao: str


@dataclass
class Parametros:
    area: str  # 'civel' | 'trabalhista' | 'tributario' | 'fazenda'
    data_calculo: date
    data_citacao: date | None = None
    data_ajuizamento: date | None = None
    data_transito: date | None = None
    aplicar_cortes_legais: bool = True
    pro_rata: bool = False
    multa_523: bool = False
    multa_contratual_pct: Decimal | None = None
    honorarios_pct: Decimal | None = None
    honorarios_fixos: tuple[Decimal, date] | None = None
    honorarios_cumprimento: bool = False
    abatimentos: list[Abatimento] = field(default_factory=list)
    imputacao: str = "juros_primeiro"
    # --- extensões (todas aparecem na memória) ---
    permitir_projecao: bool = False  # mês sem índice: parar (False) ou repetir o último mês como projeção (True)
    data_intimacao: date | None = None  # intimação para pagamento (art. 523 CPC)
    data_decurso_523: date | None = None  # último dia dos 15 dias úteis; se vazio, estimado a partir da intimação
    base_multa_523: str = "decurso"  # 'decurso' (SPEC: débito na data do decurso) | 'data_calculo'
    custas_na_base_523: bool = True  # STJ, REsp 1.757.033: custas entram na base da multa/honorários do art. 523
    responsabilidade: str = "contratual"  # cível: 'contratual' (juros da citação) | 'extracontratual' (do evento)
    termo_inicial_juros: str | None = None  # cível, sobrepõe o padrão: 'citacao'|'vencimento'|'evento'|'transito'
    indice_correcao_civel: str = "TJGO"  # cível até 29/08/2024 (SPEC: padrão TJGO)
    indice_correcao_pos_14905: str = "IPCA"  # cível/trabalhista desde 30/08/2024: 'IPCA' (art. 389 CC) | 'IPCA_15'
    juros_civel_pre_14905: str = "um_por_cento"  # 'um_por_cento' (SPEC) | 'selic_tema_1368' (STJ, Tema 1.368)
    juros_fixados_no_titulo: bool = False  # o 1% a.m. veio do título (texto da memória; não muda o cálculo)
    capitalizacao_selic: str = "simples"  # 'simples' (CJF/Receita: soma das taxas) | 'composta' (produto)
    selic_como: str = "juros"  # 'juros' (CJF/PJe-Calc: acréscimo SELIC é juro) | 'correcao' (SPEC: "a correção já é a SELIC")
    trabalhista_pre_judicial_ate_corte: bool = True  # TST: IPCA + taxa legal desde 30/08/2024 em qualquer fase
    fazenda_regime_pos_ec136: str = "selic"  # 'selic' (SPEC) | 'ipca15_taxa_legal' (CJF, desde set/2025)
    indice_correcao_fazenda: str = "IPCA_E"  # Tema 810/905: IPCA-E (geral); 'INPC' para previdenciário
    preservar_valor_nominal: bool = True  # deflação não reduz o principal abaixo do nominal (STJ, REsp 1.265.580)
    houve_impugnacao: bool = False  # Fazenda: honorários do cumprimento só com impugnação (STJ, Tema 1.190)
    arredondamento: str = "exato"  # 'exato' (10 casas, arredonda só na exibição) | 'cjf' (truncamentos do Manual CJF)
