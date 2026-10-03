"""Função pura do motor: ``calcular(parcelas, parametros, repositorio_indices) -> Memoria`` (SPEC, seção 6).

Lê índices só do repositório local (nunca da internet), não grava nada e não
depende de interface. Todo parâmetro que altera o resultado vai para
``Memoria.parametros_relevantes``.
"""

from __future__ import annotations

import copy
import dataclasses
from datetime import date, timedelta
from decimal import Decimal, localcontext

from calculo_judicial.datas import formatar_data
from calculo_judicial.decimais import CONTEXTO, ZERO, formatar_brl, formatar_decimal_br, q10
from calculo_judicial.indices.importador_csv import MARCAS_PRESUMIDA
from calculo_judicial.indices.modelo import IndiceDesconhecido, RepositorioIndices, nome_curto
from calculo_judicial.motor import acessorios, prazos, regras
from calculo_judicial.motor.arredondamento import recompor_cjf
from calculo_judicial.motor.correcao import ItemMes
from calculo_judicial.motor.memoria import (
    Acessorios,
    FonteIndice,
    Memoria,
    ParametroRelevante,
    PeriodoAplicado,
    Resumo,
)
from calculo_judicial.motor.parcela import (
    AREAS,
    CAPITALIZACOES,
    IMPUTACOES,
    NATUREZAS,
    NOMES_NATUREZA,
    TIPOS_JUROS,
    Parametros,
    Parcela,
    Periodo,
)
from calculo_judicial.motor.regras.comum import ErroRegra
from calculo_judicial.motor.simulacao import SimuladorParcela

UM_DIA = timedelta(days=1)
NATUREZAS_CONDENACAO = ("principal", "dano_moral")
INDICES_SELIC = ("SELIC",)


class ParametroInvalido(ValueError):
    """Dado de entrada inválido (tipo, valor ou combinação)."""


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------


def _exigir_decimal(nome: str, valor) -> None:
    if valor is not None and not isinstance(valor, Decimal):
        raise ParametroInvalido(f"{nome} deve ser Decimal (nunca float): {valor!r}")


def validar_parametros(params: Parametros) -> None:
    if params.area not in AREAS:
        raise ParametroInvalido(f"área inválida: {params.area}")
    if params.imputacao not in IMPUTACOES:
        raise ParametroInvalido(f"imputação inválida: {params.imputacao}")
    if params.capitalizacao_selic not in CAPITALIZACOES:
        raise ParametroInvalido(f"capitalizacao_selic inválida: {params.capitalizacao_selic}")
    if params.selic_como not in ("juros", "correcao"):
        raise ParametroInvalido(f"selic_como inválido: {params.selic_como}")
    if params.base_multa_523 not in ("decurso", "data_calculo"):
        raise ParametroInvalido(f"base_multa_523 inválida: {params.base_multa_523}")
    if params.juros_civel_pre_14905 not in ("um_por_cento", "selic_tema_1368"):
        raise ParametroInvalido(f"juros_civel_pre_14905 inválido: {params.juros_civel_pre_14905}")
    if params.fazenda_regime_pos_ec136 not in ("selic", "ipca15_taxa_legal"):
        raise ParametroInvalido(f"fazenda_regime_pos_ec136 inválido: {params.fazenda_regime_pos_ec136}")
    if params.responsabilidade not in ("contratual", "extracontratual"):
        raise ParametroInvalido(f"responsabilidade inválida: {params.responsabilidade}")
    if params.arredondamento not in ("exato", "cjf"):
        raise ParametroInvalido(f"arredondamento inválido: {params.arredondamento}")
    if params.selic_como == "correcao" and params.arredondamento == "cjf":
        raise ParametroInvalido("arredondamento 'cjf' segue o Manual CJF, em que a SELIC é 'juros SELIC': use selic_como='juros'")
    if params.selic_como == "correcao" and params.abatimentos:
        raise ParametroInvalido(
            "selic_como='correcao' não aceita abatimentos (a SELIC passaria a incidir sobre o próprio acréscimo); "
            "use selic_como='juros'"
        )
    _exigir_decimal("multa_contratual_pct", params.multa_contratual_pct)
    _exigir_decimal("honorarios_pct", params.honorarios_pct)
    if params.honorarios_fixos is not None:
        _exigir_decimal("honorarios_fixos[0]", params.honorarios_fixos[0])
    for a in params.abatimentos:
        _exigir_decimal(f"abatimento {a.descricao}", a.valor)
        if a.valor <= 0:
            raise ParametroInvalido(f"abatimento {a.descricao}: valor deve ser positivo")
    if not isinstance(params.data_calculo, date):
        raise ParametroInvalido("data_calculo deve ser date")


def validar_parcela(parcela: Parcela) -> None:
    _exigir_decimal(f"valor_original de {parcela.descricao}", parcela.valor_original)
    if parcela.valor_original < 0:
        raise ParametroInvalido(f"{parcela.descricao}: valor original negativo")
    if parcela.natureza not in NATUREZAS:
        raise ParametroInvalido(f"{parcela.descricao}: natureza inválida {parcela.natureza}")


def validar_periodos(periodos: list[Periodo], descricao: str = "") -> None:
    """Regras invioláveis sobre os períodos de uma parcela.

    - SELIC nunca cumula com outro índice de correção nem com juros de mora no mesmo
      período (EC 113/2021, art. 3º; ADC 58; STJ, Tema 1.368): SELIC só como
      ``tipo_juros='embutido'``, sem ``taxa_juros_am`` nem ``indice_juros``.
    - Períodos em ordem, sem sobreposição.
    """
    for p in periodos:
        rotulo = f"{descricao}: período {formatar_data(p.de)}–{formatar_data(p.ate)}"
        if p.tipo_juros not in TIPOS_JUROS:
            raise ParametroInvalido(f"{rotulo}: tipo_juros inválido {p.tipo_juros}")
        if p.ate < p.de:
            raise ParametroInvalido(f"{rotulo}: 'ate' anterior a 'de'")
        _exigir_decimal(f"{rotulo}: taxa_juros_am", p.taxa_juros_am)
        _exigir_decimal(f"{rotulo}: acrescimo_final_pct", p.acrescimo_final_pct)
        if p.capitalizacao_correcao not in CAPITALIZACOES or (p.capitalizacao_juros and p.capitalizacao_juros not in CAPITALIZACOES):
            raise ParametroInvalido(f"{rotulo}: capitalização inválida")
        if p.tipo_juros == "embutido":
            if p.indice_correcao not in INDICES_SELIC:
                raise ParametroInvalido(f"{rotulo}: 'embutido' é só para a SELIC (índice informado: {p.indice_correcao})")
            if p.taxa_juros_am or p.indice_juros:
                raise ParametroInvalido(f"{rotulo}: SELIC não cumula com juros de mora no mesmo período")
        else:
            if p.indice_correcao in INDICES_SELIC:
                raise ParametroInvalido(
                    f"{rotulo}: SELIC como índice de correção só com tipo_juros='embutido' (não cumula com juros)"
                )
            if p.indice_juros in INDICES_SELIC:
                raise ParametroInvalido(f"{rotulo}: SELIC como juros só com tipo_juros='embutido' (não cumula com correção)")
            if p.acrescimo_final_pct:
                raise ParametroInvalido(f"{rotulo}: acréscimo final só existe na SELIC embutida")
        if p.tipo_juros in ("simples", "composto") and p.taxa_juros_am is None:
            raise ParametroInvalido(f"{rotulo}: juros {p.tipo_juros} exigem taxa_juros_am")
        if p.tipo_juros == "indice" and not p.indice_juros:
            raise ParametroInvalido(f"{rotulo}: juros por índice exigem indice_juros")
    for a, b in zip(periodos, periodos[1:]):
        if b.de <= a.ate:
            raise ParametroInvalido(f"{descricao}: períodos sobrepostos ({formatar_data(a.ate)} e {formatar_data(b.de)})")


def normalizar_periodos(periodos: list[Periodo]) -> list[Periodo]:
    saida = sorted((copy.copy(p) for p in periodos), key=lambda p: p.de)
    for p in saida:
        if p.tipo_juros == "taxa_legal" and not p.indice_juros:
            p.indice_juros = "TAXA_LEGAL"
    return saida


def aplicar_periodos(periodos: list[Periodo], data_calculo: date) -> list[PeriodoAplicado]:
    """Converte ``ate`` inclusivo em fim exclusivo; o último período vai até a data do cálculo (exclusiva)."""
    aplicados = []
    for p in periodos:
        if p.de > data_calculo or (p.de == data_calculo and not p.acrescimo_final_pct):
            continue
        # período de duração zero na data do cálculo: só o acréscimo final (ex.: 1% do mês da restituição)
        fim = data_calculo if p.ate >= data_calculo else p.ate + UM_DIA
        aplicados.append(
            PeriodoAplicado(
                numero=len(aplicados) + 1,
                periodo=p,
                inicio=p.de,
                fim_exclusivo=fim,
                descricao=p.descricao or _descrever(p),
                base_legal=p.base_legal or "critério do título (período informado manualmente)",
            )
        )
    return aplicados


def _avisos_periodos_manuais(parcela: Parcela, aplicados: list[PeriodoAplicado], data_calculo: date) -> list[str]:
    """Trechos sem atualização em períodos informados à mão (o saldo fica parado)."""
    avisos = []
    if aplicados[0].inicio != parcela.data_base:
        avisos.append(
            f"{parcela.descricao}: o primeiro período começa em {formatar_data(aplicados[0].inicio)}, "
            f"e a data-base é {formatar_data(parcela.data_base)}"
        )
    for a, b in zip(aplicados, aplicados[1:]):
        if a.fim_exclusivo < b.inicio:
            avisos.append(
                f"{parcela.descricao}: sem correção nem juros de {formatar_data(a.fim_exclusivo)} a "
                f"{formatar_data(b.inicio - UM_DIA)} (lacuna entre períodos)"
            )
    if aplicados[-1].fim_exclusivo < data_calculo:
        avisos.append(
            f"{parcela.descricao}: sem correção nem juros de {formatar_data(aplicados[-1].fim_exclusivo)} até a data "
            "do cálculo (o último período termina antes)"
        )
    return avisos


def _descrever(p: Periodo) -> str:
    """Descrição de um período manual com tudo o que altera o resultado."""
    correcao = nome_curto(p.indice_correcao) or "sem correção"
    if p.indice_correcao and p.capitalizacao_correcao == "simples":
        correcao += " (acumulação simples: 1 + soma das taxas)"
    if p.tipo_juros == "nenhum":
        return f"{correcao}, sem juros"
    if p.tipo_juros == "embutido":
        texto = f"{nome_curto(p.indice_correcao)} embutida (correção + juros), acumulação {p.capitalizacao_correcao}"
        texto += ", incidindo também sobre os juros vencidos" if p.selic_sobre_juros else ", só sobre o principal"
        if p.acrescimo_final_pct:
            texto += f", + {_pct(p.acrescimo_final_pct)} no mês do cálculo"
        return texto
    nomes = {"simples": "simples", "composto": "compostos"}
    if p.tipo_juros in nomes:
        return f"{correcao} + juros {nomes[p.tipo_juros]} de {_pct(p.taxa_juros_am)} a.m."
    capitalizacao = p.capitalizacao_juros or "padrão do índice"
    return f"{correcao} + juros pelo índice {nome_curto(p.indice_juros)} (acumulação {capitalizacao})"


# ---------------------------------------------------------------------------
# Fontes
# ---------------------------------------------------------------------------


class _ColetorFontes:
    def __init__(self, repo: RepositorioIndices):
        self.repo = repo
        self.itens: dict[str, list[ItemMes]] = {}

    def registrar(self, item: ItemMes) -> None:
        self.itens.setdefault(item.indice, []).append(item)
        if item.indice_origem and item.indice_origem != item.indice:
            self.itens.setdefault(item.indice_origem, []).append(
                ItemMes(item.mes, item.indice_origem, item.taxa_pct, item.fracao, item.projecao, None, item.data_coleta)
            )

    def fontes(self) -> list[FonteIndice]:
        saida = []
        for codigo, itens in sorted(self.itens.items()):
            try:
                info = self.repo.info(codigo)
                nome, fonte, url = info.nome, info.fonte, info.url_fonte
            except IndiceDesconhecido:
                nome, fonte, url = codigo, "", None
            meses = sorted({i.mes for i in itens})
            projecao = sorted({i.mes for i in itens if i.projecao})
            coleta = max((i.data_coleta for i in itens if i.data_coleta), default="")
            saida.append(FonteIndice(codigo, nome, fonte, url, coleta, len(meses), projecao, meses[0], meses[-1]))
        return saida


# ---------------------------------------------------------------------------
# Parâmetros relevantes (tudo que altera o resultado aparece na memória)
# ---------------------------------------------------------------------------

DESCRICOES = {
    "area": "Área (regras legais aplicadas)",
    "data_calculo": "Data do cálculo (corrige até o mês anterior)",
    "data_citacao": "Data da citação",
    "data_ajuizamento": "Data do ajuizamento",
    "data_transito": "Trânsito em julgado",
    "aplicar_cortes_legais": "Cortes legais automáticos",
    "pro_rata": "Pró-rata dia nos meses das pontas",
    "multa_523": "Multa de 10% do art. 523 do CPC",
    "multa_contratual_pct": "Multa contratual (%)",
    "honorarios_pct": "Honorários de sucumbência (%)",
    "honorarios_fixos": "Honorários fixados em quantia certa (valor, data)",
    "honorarios_cumprimento": "Honorários de 10% do cumprimento (art. 523)",
    "abatimentos": "Abatimentos",
    "imputacao": "Imputação dos pagamentos",
    "permitir_projecao": "Mês sem índice: usar o último mês como projeção",
    "data_intimacao": "Intimação para pagamento (art. 523)",
    "data_decurso_523": "Fim do prazo de 15 dias úteis (art. 523)",
    "base_multa_523": "Base da multa/honorários do art. 523",
    "responsabilidade": "Responsabilidade",
    "termo_inicial_juros": "Termo inicial dos juros",
    "indice_correcao_civel": "Índice de correção cível até 29/08/2024",
    "indice_correcao_pos_14905": "Índice de correção desde 30/08/2024",
    "juros_civel_pre_14905": "Juros cíveis até 29/08/2024",
    "juros_fixados_no_titulo": "Juros de 1% a.m. fixados no título",
    "capitalizacao_selic": "Acumulação da SELIC",
    "selic_como": "Acréscimo da SELIC embutida tratado como",
    "trabalhista_pre_judicial_ate_corte": "Trabalhista: fase pré-judicial termina em 29/08/2024",
    "fazenda_regime_pos_ec136": "Fazenda: regime após ago/2025",
    "indice_correcao_fazenda": "Fazenda: índice de correção até 11/2021",
    "preservar_valor_nominal": "Deflação não reduz o principal abaixo do nominal",
    "houve_impugnacao": "Houve impugnação ao cumprimento",
    "arredondamento": "Arredondamento (exato ou truncamentos do Manual CJF)",
}


# rótulo de cada valor de critério (a memória mostra o significado, não o código interno)
ROTULOS_VALORES = {
    "area": {"civel": "Cível", "trabalhista": "Trabalhista", "tributario": "Tributário federal", "fazenda": "Fazenda Pública"},
    "imputacao": {"juros_primeiro": "juros primeiro (CC, art. 354)", "capital_primeiro": "capital primeiro",
                  "proporcional": "proporcional"},
    "base_multa_523": {"decurso": "débito no fim do prazo (com os juros até lá), atualizado só pela correção até a data do "
                                  "cálculo", "data_calculo": "débito na data do cálculo"},
    "termo_inicial_juros": {"citacao": "citação", "vencimento": "vencimento de cada parcela", "evento": "evento danoso",
                            "transito": "trânsito em julgado"},
    "juros_civel_pre_14905": {"um_por_cento": "1% a.m. (CC, art. 406, redação original; 0,5% a.m. antes de 11/01/2003)",
                              "selic_tema_1368": "SELIC desde 11/01/2003 (STJ, Tema 1.368; antes, 0,5% a.m. do CC/1916)"},
    "capitalizacao_selic": {"simples": "soma simples das taxas mensais", "composta": "produto dos fatores mensais"},
    "selic_como": {"juros": "juros de mora", "correcao": "correção monetária"},
    "fazenda_regime_pos_ec136": {"selic": "SELIC (EC 113/2021)", "ipca15_taxa_legal": "IPCA-15 + taxa legal (EC 136/2025)"},
    "arredondamento": {"exato": "exato (10 casas)", "cjf": "truncamentos do Manual CJF"},
}
INDICES_EM_PARAMETROS = ("indice_correcao_civel", "indice_correcao_pos_14905", "indice_correcao_fazenda")


def _formatar_valor(valor, nome: str = "") -> str:
    if isinstance(valor, str) and nome in ROTULOS_VALORES:
        return ROTULOS_VALORES[nome].get(valor, valor)
    if isinstance(valor, str) and nome in INDICES_EM_PARAMETROS:
        return nome_curto(valor)
    if nome == "termo_inicial_juros" and valor is None:
        return "padrão da área (citação; evento danoso no extracontratual)"
    if nome == "data_decurso_523" and valor is None:
        return "não informado: estimado a partir da intimação (ver Observações)"
    if nome in ("multa_contratual_pct", "honorarios_pct") and isinstance(valor, Decimal):
        return _pct(valor)
    if nome == "honorarios_fixos" and isinstance(valor, tuple):
        return f"{formatar_brl(valor[0])}, fixados em {formatar_data(valor[1])}"
    if valor is None:
        return "—"
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, date):
        return formatar_data(valor)
    if isinstance(valor, Decimal):
        return format(valor, "f")
    if isinstance(valor, tuple):
        return ", ".join(_formatar_valor(v) for v in valor)
    if isinstance(valor, list):
        return "; ".join(f"{formatar_data(a.data)} {formatar_brl(a.valor)} ({a.descricao})" for a in valor) or "nenhum"
    return str(valor)


PARAMETROS_COMUNS = {  # honorarios_fixos aparece quando preenchido (difere do padrão)
    "area", "data_calculo", "aplicar_cortes_legais", "pro_rata", "multa_contratual_pct", "honorarios_pct",
    "abatimentos", "permitir_projecao", "arredondamento", "preservar_valor_nominal",
}
PARAMETROS_DA_AREA = {
    "civel": {"data_citacao", "data_transito", "responsabilidade", "termo_inicial_juros", "indice_correcao_civel",
              "indice_correcao_pos_14905", "juros_civel_pre_14905"},
    "trabalhista": {"data_ajuizamento", "indice_correcao_pos_14905", "capitalizacao_selic", "selic_como"},
    "tributario": {"capitalizacao_selic", "selic_como"},
    "fazenda": {"data_citacao", "data_transito", "responsabilidade", "termo_inicial_juros", "indice_correcao_fazenda",
                "fazenda_regime_pos_ec136", "capitalizacao_selic", "selic_como"},
}
PARAMETROS_523 = {"multa_523", "honorarios_cumprimento", "data_intimacao", "data_decurso_523", "base_multa_523"}


def _parametros_que_pesam(params: Parametros, parcelas=(), periodos=()) -> set[str]:
    """Parâmetros que entram neste cálculo: os comuns, os da área (com cortes automáticos), os do art. 523
    quando pedidos e todo parâmetro alterado em relação ao padrão (este sempre aparece).

    ``parcelas``: no tributário, custas e honorários seguem a regra da Fazenda. ``periodos``: os períodos
    aplicados (com SELIC embutida, o tratamento do acréscimo da SELIC pesa mesmo com períodos manuais).
    """
    nomes = set(PARAMETROS_COMUNS)
    if params.abatimentos:
        nomes.add("imputacao")
    if any(p.tipo_juros == "embutido" for p in periodos):
        nomes.add("selic_como")
    if params.aplicar_cortes_legais:
        nomes |= PARAMETROS_DA_AREA.get(params.area, set())
        if params.area == "tributario" and (params.honorarios_fixos is not None
                                            or any(x.natureza != "principal" for x in parcelas)):
            nomes |= PARAMETROS_DA_AREA["fazenda"]
        if params.area == "civel" and params.juros_civel_pre_14905 == "selic_tema_1368":
            nomes |= {"capitalizacao_selic", "selic_como"}
        if params.area == "civel" and params.juros_fixados_no_titulo:
            nomes.add("juros_fixados_no_titulo")
        if params.area == "trabalhista" and params.data_ajuizamento and params.data_ajuizamento >= regras.corte_lei_14905():
            nomes.add("trabalhista_pre_judicial_ate_corte")
    if params.multa_523 or params.honorarios_cumprimento:
        nomes |= PARAMETROS_523
        if params.area == "fazenda":
            nomes.add("houve_impugnacao")
        if params.base_multa_523 == "decurso":  # índice que atualiza a base nos períodos de SELIC
            nomes.add("indice_correcao_civel" if params.area == "civel" else "indice_correcao_fazenda")
    padrao = Parametros(area=params.area, data_calculo=params.data_calculo)
    nomes |= {f.name for f in dataclasses.fields(params) if getattr(params, f.name) != getattr(padrao, f.name)}
    return nomes


def parametros_relevantes(params: Parametros, parcelas=(), periodos=()) -> list[ParametroRelevante]:
    """Todos os parâmetros com o valor usado; ``aplica`` marca os que pesam neste cálculo (os relatórios mostram esses)."""
    pesam = _parametros_que_pesam(params, parcelas, periodos)
    return [
        ParametroRelevante(f.name, _formatar_valor(getattr(params, f.name), f.name), DESCRICOES.get(f.name, f.name),
                           f.name in pesam)
        for f in dataclasses.fields(params)
    ]


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------


def _pct(valor: Decimal) -> str:
    """15 -> '15%'; 12.5 -> '12,5%' (exibição)."""
    return f"{formatar_decimal_br(valor, 4).rstrip('0').rstrip(',')}%"


def _soma(valores) -> Decimal:
    return sum(valores, ZERO)


def calcular(parcelas: list[Parcela], parametros: Parametros, repositorio_indices: RepositorioIndices) -> Memoria:
    """Calcula o débito de todas as parcelas na data do cálculo e monta a memória.

    Ordem (CLAUDE.md): correção, juros sobre o corrigido, multa, honorários sobre o total.
    Levanta ``IndiceAusente`` se faltar mês de índice no banco local (salvo projeção
    autorizada), ``ErroRegra`` se a regra legal não puder montar os períodos e
    ``ParametroInvalido`` para entradas inválidas.
    """
    params = parametros
    repo = repositorio_indices
    validar_parametros(params)
    avisos: list[str] = []
    criterios: list[str] = []

    todas = list(parcelas)
    if params.honorarios_fixos is not None:
        valor, data_fixacao = params.honorarios_fixos
        todas.append(Parcela("Honorários fixados em quantia certa", valor, data_fixacao, "honorarios"))
    if not todas:
        raise ParametroInvalido("nenhuma parcela informada")

    if params.area == "civel" and params.aplicar_cortes_legais:
        try:
            tipo = repo.info(params.indice_correcao_civel).tipo
        except IndiceDesconhecido:
            tipo = "correcao"  # tabela ausente: o erro sai no mês em que ela for usada, com a mensagem própria
        if tipo != "correcao":
            raise ParametroInvalido(f"{params.indice_correcao_civel} é taxa de juros ou remuneração, não índice de "
                                    "correção monetária: escolha um índice ou tabela de correção (comando 'tabelas')")
    fontes = _ColetorFontes(repo)
    fim_dos_criterios: list[int] = []  # onde termina o bloco de cada parcela nos critérios
    simuladores: list[SimuladorParcela] = []
    aplicados_por_parcela: list[tuple[Parcela, list[PeriodoAplicado]]] = []
    for parcela in todas:
        validar_parcela(parcela)
        if params.aplicar_cortes_legais:
            periodos = regras.montar_periodos(parcela, params)
            if parcela.periodos:
                avisos.append(f"{parcela.descricao}: períodos manuais ignorados (cortes legais automáticos ligados)")
        else:
            if not parcela.periodos:
                raise ErroRegra(f"{parcela.descricao}: sem cortes automáticos é preciso informar os períodos (critérios do título)")
            periodos = parcela.periodos
        periodos = normalizar_periodos(periodos)
        validar_periodos(periodos, parcela.descricao)
        aplicados = aplicar_periodos(periodos, params.data_calculo)
        if not aplicados:
            avisos.append(f"{parcela.descricao}: nenhum período até a data do cálculo; mantido o valor nominal")
        elif not params.aplicar_cortes_legais:
            avisos.extend(_avisos_periodos_manuais(parcela, aplicados, params.data_calculo))
        for aviso in regras.avisos_da_regra(parcela, params):
            if aviso not in avisos:
                avisos.append(aviso)
        simuladores.append(SimuladorParcela(parcela, aplicados, params, repo, fontes.registrar))
        aplicados_por_parcela.append((parcela, aplicados))
        criterios.extend(_criterios_parcela(parcela, aplicados, params))
        fim_dos_criterios.append(len(criterios))

    # Multa contratual e honorários percentuais incidem sobre a condenação sem dedução dos abatimentos:
    # o pagamento quita juros e capital do credor (CC, art. 354), não os honorários do advogado.
    brutos = None
    if params.abatimentos and (params.multa_contratual_pct or params.honorarios_pct):
        brutos = [SimuladorParcela(p, a, params, repo) for p, a in aplicados_por_parcela]

    # Cadeias montadas pela calculadora (ex.: TJGO a partir do provimento), não tabelas oficiais importadas
    for codigo in sorted({p.periodo.indice_correcao for s in simuladores for p in s.periodos if p.periodo.indice_correcao}):
        try:
            nome = repo.info(codigo).nome
        except IndiceDesconhecido:
            continue
        if any(marca in nome for marca in MARCAS_PRESUMIDA):
            avisos.append(nome if MARCAS_PRESUMIDA[0] in nome else f"Índice {codigo}: {nome}")

    # Art. 523: data do decurso e retrato do débito nessa data
    decurso = None
    retrato: dict[str, object] = {}
    if params.multa_523 or params.honorarios_cumprimento:
        decurso = params.data_decurso_523
        if decurso is None and params.data_intimacao is not None:
            decurso = prazos.data_decurso_523(params.data_intimacao)
            avisos.append(
                f"Prazo do art. 523 estimado: intimação em {formatar_data(params.data_intimacao)} + 15 dias úteis = "
                f"{formatar_data(decurso)} (feriados nacionais e recesso de 20/12 a 20/01; confira feriados locais)"
            )
        if decurso is None:
            raise ParametroInvalido("multa/honorários do art. 523: informe a data da intimação para pagamento ou a do fim "
                                    "do prazo de 15 dias úteis")
    eventos = {}
    if decurso is not None and params.base_multa_523 == "decurso" and decurso < params.data_calculo:
        def tirar_retrato():
            retrato["data"] = decurso
            retrato["saldos"] = [(s, s.C + s.J if s.ativa else ZERO) for s in simuladores]
            if brutos is not None:
                for b in brutos:
                    b.avancar(decurso)
                retrato["condenacao_bruta"] = _soma(
                    b.C + b.J for b in brutos if b.ativa and b.parcela.natureza in NATUREZAS_CONDENACAO
                )
        eventos[decurso] = tirar_retrato

    linhas_abatimento, avisos_ab = acessorios.aplicar_abatimentos(
        simuladores, params.abatimentos, params.imputacao, params.data_calculo, eventos
    )
    avisos.extend(avisos_ab)
    for s in simuladores:
        s.avancar(params.data_calculo)
    memorias = [s.finalizar() for s in simuladores]
    if params.arredondamento == "cjf":
        ajustadas = []
        for s, m in zip(simuladores, memorias):
            m, aviso = recompor_cjf(s, m)
            ajustadas.append(m)
            if aviso:
                avisos.append(aviso)
        memorias = ajustadas
        criterios.append(
            "Arredondamento do Manual de Cálculos da JF: coeficiente com 10 casas, percentuais de cada regime de juros "
            "truncados em 2 casas e valores truncados em centavos (linhas analíticas sem truncamento; ajuste na última linha)"
        )
    for m in memorias:
        avisos.extend(a for a in m.avisos if a not in avisos)

    # meses em que o regime muda: em qual período cada um foi calculado (inteiro, sem pró-rata, ou dividido)
    for fim, m in reversed(list(zip(fim_dos_criterios, memorias))):
        texto = meses_de_transicao(m)
        if texto:
            criterios.insert(fim, f"  Meses de mudança de regime: {texto}")
    for m in memorias:
        for aviso in _avisos_tr_zero(m):
            if aviso not in avisos:
                avisos.append(aviso)

    # Acessórios na data do cálculo
    with localcontext(CONTEXTO):
        base_condenacao = _soma(m.total for m in memorias if m.parcela.natureza in NATUREZAS_CONDENACAO)
        outros = _soma(m.total for m in memorias if m.parcela.natureza not in NATUREZAS_CONDENACAO)
        base_acessorios = base_condenacao
        if brutos is not None:
            for b in brutos:
                b.avancar(params.data_calculo)
            memorias_brutas = [b.finalizar() for b in brutos]
            if params.arredondamento == "cjf":
                memorias_brutas = [recompor_cjf(b, m)[0] for b, m in zip(brutos, memorias_brutas)]
            base_acessorios = _soma(m.total for m in memorias_brutas if m.parcela.natureza in NATUREZAS_CONDENACAO)
        ac = Acessorios()
        ac.multa_contratual = acessorios.multa_contratual(base_acessorios, params.multa_contratual_pct)
        ac.honorarios_sucumbencia = acessorios.honorarios_percentuais(base_acessorios + ac.multa_contratual, params.honorarios_pct)
        fora_da_base = any(m.parcela.natureza not in NATUREZAS_CONDENACAO for m in memorias)
        if params.multa_contratual_pct:
            ac.detalhes.append(
                f"Multa contratual: {_pct(params.multa_contratual_pct)} × {formatar_brl(base_acessorios)} (principal "
                f"corrigido + juros) = {formatar_brl(ac.multa_contratual)}"
            )
        if params.honorarios_pct:
            base_h = base_acessorios + ac.multa_contratual
            ac.detalhes.append(
                f"Honorários de sucumbência: {_pct(params.honorarios_pct)} × {formatar_brl(base_h)} (principal corrigido + "
                f"juros{' + multa contratual' if ac.multa_contratual else ''}) = {formatar_brl(ac.honorarios_sucumbencia)}"
                + (" — custas e honorários em quantia certa ficam fora da base" if fora_da_base else "")
            )
        if brutos is not None:
            ac.detalhes.append(
                f"Multa contratual e honorários calculados sobre a condenação sem dedução dos abatimentos "
                f"({formatar_brl(base_acessorios)}): os pagamentos quitam juros e capital do credor, não os honorários"
            )
        if decurso is not None:
            _acessorios_523(params, decurso, retrato, simuladores, base_condenacao, outros, ac, avisos)

        total_parcelas = base_condenacao + outros
        total = q10(total_parcelas + ac.multa_contratual + ac.honorarios_sucumbencia + ac.multa_523 + ac.honorarios_cumprimento)
        abatido = _soma(l.juros_imputados + l.capital_imputado for l in linhas_abatimento)
        # honorários fixados em quantia certa (parcela de natureza "honorarios", já corrigida e com juros) vão para a
        # linha "Honorários"; principal, correção e juros ficam só com o crédito da parte
        credito = [m for m in memorias if m.parcela.natureza != "honorarios"]
        honorarios_parcela = _soma(m.valor_original + m.correcao + m.juros_brutos for m in memorias
                                   if m.parcela.natureza == "honorarios")
        resumo = Resumo(
            principal=_soma(m.valor_original for m in credito),
            correcao=_soma(m.correcao for m in credito),
            juros=_soma(m.juros_brutos for m in credito),
            multa=ac.multa_contratual + ac.multa_523,
            honorarios=ac.honorarios_sucumbencia + ac.honorarios_cumprimento + honorarios_parcela,
            abatimentos=abatido,
            total=total,
            saldo_credor=_soma(l.excedente for l in linhas_abatimento),
        )
        conferencia = resumo.principal + resumo.correcao + resumo.juros + resumo.multa + resumo.honorarios - resumo.abatimentos
        if conferencia != resumo.total:  # identidade contábil da memória
            raise AssertionError(f"resumo inconsistente: {conferencia} != {resumo.total}")

    cortes = regras.cortes_da_area(params, [p for p, _ in aplicados_por_parcela]) if params.aplicar_cortes_legais else []
    if params.aplicar_cortes_legais:
        criterios.insert(0, "Cortes legais aplicados automaticamente: " + "; ".join(cortes))
    else:
        criterios.insert(0, "Critérios do título: períodos informados manualmente (cortes legais automáticos desligados)")
    criterios.append(
        "Pró-rata dia nos meses das pontas: " + ("sim (fração de dias corridos; juros simples por 1/30 do mês)" if params.pro_rata else "não (meses cheios)")
    )
    criterios.append(
        "Convenção: corrigir de A até B acumula os índices dos meses de A (inclusive) até o mês anterior a B; juros "
        "percentuais contados nos mesmos meses (com pró-rata, ± dias/30); juros sobre o principal corrigido, sem "
        "capitalização (por isso os juros acumulados acompanham a correção do principal)"
    )
    return Memoria(
        parametros=params,
        parcelas=memorias,
        abatimentos=linhas_abatimento,
        acessorios=ac,
        resumo=resumo,
        criterios=criterios,
        cortes_aplicados=cortes,
        fontes=fontes.fontes(),
        parametros_relevantes=parametros_relevantes(params, [p for p, _ in aplicados_por_parcela],
                                                    [pa.periodo for _, a in aplicados_por_parcela for pa in a]),
        avisos=avisos,
    )


def indice_correcao_pura(params: Parametros) -> str:
    """Índice de correção sem juros da área, usado para atualizar a multa/honorários do art. 523
    nos períodos de SELIC embutida (a SELIC engloba juros)."""
    if params.area == "civel":
        return params.indice_correcao_civel
    if params.area == "trabalhista":
        return "IPCA_E"
    return params.indice_correcao_fazenda


def _acessorios_523(params, decurso, retrato, simuladores, base_condenacao, outros, ac: Acessorios, avisos) -> None:
    """Multa de 10% e honorários de 10% do art. 523, §1º, do CPC."""
    aplica_multa = False
    if params.multa_523:
        aplica_multa, razao = acessorios.multa_523_cabivel(params.area)
        if not aplica_multa:
            avisos.append(f"Multa do art. 523 não calculada — {razao}")
    aplica_honorarios = False
    if params.honorarios_cumprimento:
        aplica_honorarios, razao = acessorios.honorarios_cumprimento_cabiveis(params.area, params.houve_impugnacao)
        if not aplica_honorarios:
            avisos.append(f"Honorários de 10% do cumprimento não calculados — {razao}")
    if not (aplica_multa or aplica_honorarios):
        return
    if decurso >= params.data_calculo:
        avisos.append(f"Prazo do art. 523 termina em {formatar_data(decurso)}, não antes da data do cálculo: multa/honorários ainda não incidem")
        return
    if params.base_multa_523 == "decurso":
        saldos = retrato.get("saldos", [])
        condenacao_d = _soma(v for s, v in saldos if s.parcela.natureza in NATUREZAS_CONDENACAO)
        outros_d = _soma(v for s, v in saldos if s.parcela.natureza not in NATUREZAS_CONDENACAO)
        base_acessorios_d = retrato.get("condenacao_bruta", condenacao_d)
        mc_d = acessorios.multa_contratual(base_acessorios_d, params.multa_contratual_pct)
        hs_d = acessorios.honorarios_percentuais(base_acessorios_d + mc_d, params.honorarios_pct)
        debito_d = q10(condenacao_d + outros_d + mc_d + hs_d)
        peso = _soma(v for _, v in saldos)
        indice_puro = indice_correcao_pura(params)
        if peso > 0:
            fator = q10(_soma(v * s.fator_atualizacao(decurso, params.data_calculo, indice_puro) for s, v in saldos) / peso)
        else:
            fator = Decimal(1)
        ac.data_base_523 = decurso
        ac.debito_base_523 = debito_d
        ac.fator_atualizacao_523 = fator
        base = q10(debito_d * fator)
        ac.detalhes.append(
            f"Art. 523: débito de {formatar_brl(debito_d)} em {formatar_data(decurso)} (fim do prazo), atualizado até "
            f"{formatar_data(params.data_calculo)} só pela correção, sem juros ("
            + (f"nos períodos de SELIC, pelo índice {nome_curto(indice_puro)}; " if any(
                p.periodo.tipo_juros == "embutido" for s, _ in saldos for p in s.periodos) else "")
            + f"fator {formatar_decimal_br(fator, 6)}) = "
            f"{formatar_brl(base)}" + (" — a deflação do período reduziu a base" if fator < 1 else "")
        )
    else:
        base = q10(base_condenacao + outros + ac.multa_contratual + ac.honorarios_sucumbencia)
        ac.data_base_523 = params.data_calculo
        ac.debito_base_523 = base
        ac.fator_atualizacao_523 = Decimal(1)
        ac.detalhes.append(f"Art. 523: débito na data do cálculo = {formatar_brl(base)}")
    if aplica_multa:
        ac.multa_523 = acessorios.multa_523(base)
        ac.detalhes.append(f"Multa de 10% (CPC, art. 523, §1º): 10% × {formatar_brl(base)} = {formatar_brl(ac.multa_523)}")
    if aplica_honorarios:
        ac.honorarios_cumprimento = acessorios.honorarios_cumprimento(base)
        ac.detalhes.append(
            f"Honorários de 10% do cumprimento (CPC, art. 523, §1º): 10% × {formatar_brl(base)} = "
            f"{formatar_brl(ac.honorarios_cumprimento)} (a multa não entra na base — STJ, REsp 1.757.033)"
        )


def meses_de_transicao(mp) -> str:
    """Mês em que começa cada período (depois do primeiro) quando a data não é dia 1: em qual período ele foi calculado.

    Lido das linhas da memória analítica (é o que de fato foi calculado): sem pró-rata, o mês inteiro fica no
    período que começa nele; com pró-rata, ele aparece dividido entre os dois períodos.
    """
    partes = []
    for pa in mp.periodos[1:]:
        if pa.inicio.day == 1:
            continue
        mes = pa.inicio.replace(day=1)
        no_mes = sorted({l.periodo for l in mp.linhas if l.competencia == mes and l.tipo_juros != "ajuste"})
        if no_mes == [pa.numero]:
            partes.append(f"{mes:%m/%Y} contado inteiro no período {pa.numero} (que começa em {formatar_data(pa.inicio)})")
        elif len(no_mes) > 1:
            partes.append(f"{mes:%m/%Y} dividido por dias entre os períodos {' e '.join(str(n) for n in no_mes)}")
    return "; ".join(partes)


def _avisos_tr_zero(mp) -> list[str]:
    """Juros pela TR em meses em que ela foi zero: diz isso, para não parecer que os juros foram esquecidos."""
    saida = []
    for pa in mp.periodos:
        if pa.periodo.indice_juros != "TR":
            continue
        linhas = [l for l in mp.linhas if l.periodo == pa.numero and l.tipo_juros != "ajuste"]
        if linhas and all(not l.taxa_juros_pct for l in linhas):
            saida.append(f"{mp.parcela.descricao}: a TR foi zero de {linhas[0].competencia:%m/%Y} a "
                         f"{linhas[-1].competencia:%m/%Y}; por isso não há juros no período {pa.numero}")
    return saida


def _criterios_parcela(parcela: Parcela, aplicados: list[PeriodoAplicado], params: Parametros) -> list[str]:
    linhas = [f"{parcela.descricao} (valor {formatar_brl(parcela.valor_original)}, data-base "
              f"{formatar_data(parcela.data_base)}, natureza {NOMES_NATUREZA.get(parcela.natureza, parcela.natureza)}):"]
    if params.aplicar_cortes_legais:
        marcos = regras.marcos_da_parcela(parcela, params)
        if marcos:
            linhas.append(f"  Marcos: {marcos}")
    juros_antes = False
    for pa in aplicados:
        ultimo = max(pa.fim_exclusivo - UM_DIA, pa.inicio)
        detalhes = _detalhes_tecnicos(pa.periodo, params, juros_antes)
        juros_antes = juros_antes or pa.periodo.tipo_juros != "nenhum"
        linhas.append(
            f"  Período {pa.numero}: {formatar_data(pa.inicio)} a {formatar_data(ultimo)} — {pa.descricao}"
            + (f" [{detalhes}]" if detalhes else "")
            + ("" if pa.descricao.endswith(".") and not detalhes else ".") + f" Base legal: {pa.base_legal}"
        )
    return linhas


def _detalhes_tecnicos(p: Periodo, params: Parametros, juros_antes: bool = True) -> str:
    """Convenções do período que alteram o resultado (para a memória)."""
    from calculo_judicial.motor import juros as mod_juros

    partes = []
    if p.tipo_juros == "embutido":
        partes.append(f"SELIC por {'soma simples' if p.capitalizacao_correcao == 'simples' else 'produto'} das taxas mensais")
        partes.append("tratada como " + ("juros" if params.selic_como == "juros" else "correção"))
        if juros_antes:  # só há juros vencidos a considerar se um período anterior teve juros
            partes.append("incide também sobre os juros vencidos antes" if p.selic_sobre_juros
                          else "não incide sobre os juros vencidos antes")
        if p.acrescimo_final_pct:
            partes.append(f"+ {_pct(p.acrescimo_final_pct)} no mês do cálculo, SELIC por meses cheios")
    else:
        if p.indice_correcao and p.capitalizacao_correcao == "simples":
            partes.append("correção por soma simples das taxas")
        if p.tipo_juros in ("taxa_legal", "indice"):
            indice = p.indice_juros or "TAXA_LEGAL"
            cap = p.capitalizacao_juros or mod_juros.capitalizacao_padrao(indice)
            if not (indice == "TAXA_LEGAL" and cap == "simples" and "juros simples" in (p.descricao or "")):
                partes.append(f"juros: {nome_curto(indice)} por {'soma simples' if cap == 'simples' else 'produto'}")
    return "; ".join(partes)
