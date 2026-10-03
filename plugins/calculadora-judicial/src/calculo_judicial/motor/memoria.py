"""Estruturas de saída do motor: a memória de cálculo (SPEC, seções 6 e 8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from calculo_judicial.decimais import ZERO, arredondar_exibicao

from calculo_judicial.motor.parcela import Parametros, Parcela, Periodo


@dataclass
class LinhaMemoria:
    """Uma linha da memória analítica (um mês de competência, ou o fecho de um período).

    ``base``: valor base na moeda da data-base (principal remanescente / fator
    acumulado), de modo que ``corrigido = base × fator_acumulado``.
    ``taxa_indice_pct`` e ``taxa_juros_pct`` são as taxas do mês (com a fração
    aplicada, se pró-rata); os valores em R$ são os acumulados no fim da linha.
    """

    competencia: date
    fim: date
    periodo: int
    indice: str | None
    taxa_indice_pct: Decimal | None
    fracao: Decimal
    fator_acumulado: Decimal
    base: Decimal
    corrigido: Decimal
    tipo_juros: str
    taxa_juros_pct: Decimal
    juros_acumulados: Decimal
    subtotal: Decimal
    projecao: bool = False
    indice_origem: str | None = None
    observacao: str = ""


@dataclass
class PeriodoAplicado:
    """Período efetivamente usado no cálculo, com datas e descrição para os critérios."""

    numero: int
    periodo: Periodo
    inicio: date
    fim_exclusivo: date
    descricao: str
    base_legal: str


@dataclass
class MemoriaParcela:
    parcela: Parcela
    periodos: list[PeriodoAplicado]
    linhas: list[LinhaMemoria]
    valor_original: Decimal
    principal_corrigido: Decimal
    juros: Decimal
    capital_pago: Decimal = ZERO
    juros_pago: Decimal = ZERO
    avisos: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return self.principal_corrigido + self.juros

    @property
    def correcao(self) -> Decimal:
        return self.principal_corrigido + self.capital_pago - self.valor_original

    @property
    def juros_brutos(self) -> Decimal:
        return self.juros + self.juros_pago


@dataclass
class LinhaAbatimento:
    data: date
    descricao: str
    valor: Decimal
    saldo_antes: Decimal
    juros_imputados: Decimal
    capital_imputado: Decimal
    excedente: Decimal
    saldo_apos: Decimal
    imputacao: str


@dataclass
class Acessorios:
    multa_contratual: Decimal = ZERO
    honorarios_sucumbencia: Decimal = ZERO
    multa_523: Decimal = ZERO
    honorarios_cumprimento: Decimal = ZERO
    data_base_523: date | None = None
    debito_base_523: Decimal = ZERO
    fator_atualizacao_523: Decimal = ZERO
    detalhes: list[str] = field(default_factory=list)


@dataclass
class Resumo:
    """Resumo de 7 linhas (SPEC, seção 8): principal + correção + juros + multa + honorários − abatimentos = total."""

    principal: Decimal
    correcao: Decimal
    juros: Decimal
    multa: Decimal
    honorarios: Decimal
    abatimentos: Decimal
    total: Decimal
    saldo_credor: Decimal = ZERO

    def linhas(self) -> list[tuple[str, Decimal]]:
        return [
            ("Principal original", self.principal),
            ("Correção monetária", self.correcao),
            ("Juros de mora", self.juros),
            ("Multa", self.multa),
            ("Honorários", self.honorarios),
            ("Abatimentos", -self.abatimentos),
            ("Total devido", self.total),
        ]

    def arredondado(self) -> dict[str, Decimal]:
        return {nome: arredondar_exibicao(valor) for nome, valor in self.linhas()}


@dataclass
class FonteIndice:
    codigo: str
    nome: str
    fonte: str
    url: str | None
    data_coleta: str
    meses_usados: int
    meses_projecao: list[date] = field(default_factory=list)
    primeiro_mes: date | None = None
    ultimo_mes: date | None = None


@dataclass
class ParametroRelevante:
    nome: str
    valor: str
    descricao: str
    aplica: bool = True  # pesa neste cálculo (área, acessórios pedidos ou alterado); os relatórios mostram só esses


@dataclass
class Memoria:
    parametros: Parametros
    parcelas: list[MemoriaParcela]
    abatimentos: list[LinhaAbatimento]
    acessorios: Acessorios
    resumo: Resumo
    criterios: list[str]
    cortes_aplicados: list[str]
    fontes: list[FonteIndice]
    parametros_relevantes: list[ParametroRelevante]
    avisos: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return self.resumo.total
