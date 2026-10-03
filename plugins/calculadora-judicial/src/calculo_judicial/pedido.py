"""Pedido de cálculo em formato simples (JSON) — a ponte entre a conversa e o motor.

Usado pela skill do Claude Code (plugin) e pela linha de comando ``calculo_judicial.cli``.
O Claude preenche o pedido a partir do que o advogado disse; ``texto_conferencia`` mostra
tudo o que será usado (inclusive os critérios padrão e os períodos de cada parcela) para o
advogado confirmar; só então ``calcular_pedido`` roda o motor. Nenhuma conta é feita fora
do motor, e os valores e datas passam pelas mesmas leituras da interface (formato brasileiro).

Formato (campos opcionais podem faltar)::

    {
      "processo": {"numero_cnj": "", "vara": "", "tipo": "cumprimento"},
      "area": "civel",                       # civel | trabalhista | tributario | fazenda
      "data_calculo": "01/10/2026",          # padrão: hoje
      "datas": {"citacao": "15/03/2023", "ajuizamento": null, "transito": null,
                "intimacao_523": null, "fim_prazo_523": null},
      "parcelas": [{"descricao": "Aluguel jan/2023", "valor": "10.000,00", "data_base": "10/01/2023",
                    "natureza": "principal", "data_evento": null}],
      "abatimentos": [{"data": "01/04/2025", "valor": "5.000,00", "descricao": "depósito judicial"}],
      "acessorios": {"multa_contratual_pct": null, "honorarios_pct": "10",
                     "honorarios_fixos": {"valor": "2.000,00", "data": "01/03/2024"},
                     "multa_523": false, "honorarios_cumprimento": false},
      "criterios": {"pro_rata": false, "juros_civel_pre_14905": "um_por_cento", ...},
      "periodos": {"*": [{"de": "10/01/2023", "ate": "01/10/2026", "indice_correcao": "IPCA",
                          "tipo_juros": "simples", "taxa_juros_am": "1"}]}
    }

``periodos`` (opcional) são os critérios fixados no título: com eles os cortes legais
automáticos ficam desligados. A chave é a descrição da parcela, ou ``"*"`` para todas.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

from calculo_judicial.app import formulario, serializacao
from calculo_judicial.datas import formatar_data, mes_anterior
from calculo_judicial.decimais import formatar_brl
from calculo_judicial.indices.modelo import IndiceAusente, IndiceDesconhecido, RepositorioIndices, nome_curto
from calculo_judicial.motor import ErroRegra, ParametroInvalido, Parametros, Parcela, calcular
from calculo_judicial.motor.calcular import aplicar_periodos, meses_de_transicao, normalizar_periodos
from calculo_judicial.motor.memoria import Memoria
from calculo_judicial.motor.parcela import AREAS, IMPUTACOES, NOMES_NATUREZA
from calculo_judicial.motor.regras import avisos_da_regra, corte_lei_14905, cortes_da_area, marcos_da_parcela, montar_periodos

NOMES_AREA = {"civel": "Cível", "trabalhista": "Trabalhista", "tributario": "Tributário", "fazenda": "Fazenda Pública"}
DATAS = {
    "citacao": ("data_citacao", "Citação"),
    "ajuizamento": ("data_ajuizamento", "Ajuizamento"),
    "transito": ("data_transito", "Trânsito em julgado"),
    "intimacao_523": ("data_intimacao", "Intimação para pagamento (art. 523)"),
    "fim_prazo_523": ("data_decurso_523", "Fim do prazo de 15 dias úteis (art. 523)"),
}
INDICES_CIVEIS = ("TJGO", "TJSP", "INPC", "IPCA", "IPCA_E", "IPCA_15", "IGPM", "JF", "JF_PREV")
# critério -> (valores aceitos ou bool, descrição, explicação de cada valor)
CRITERIOS = {
    "pro_rata": (bool, "Pró-rata dia nos meses das pontas", {}),
    "termo_inicial_juros": (("citacao", "vencimento", "evento", "transito"), "Termo inicial dos juros", {
        "citacao": "citação", "vencimento": "vencimento de cada parcela", "evento": "evento danoso", "transito": "trânsito em julgado"}),
    "responsabilidade": (("contratual", "extracontratual"), "Responsabilidade", {}),
    "indice_correcao_civel": (INDICES_CIVEIS, "Índice cível até 29/08/2024", {
        "TJGO": "tabela do TJGO montada pela calculadora (INPC — Prov. CGJ-GO 06/2019)"}),
    "indice_correcao_pos_14905": (("IPCA", "IPCA_15"), "Índice de correção desde 30/08/2024 (cível/trabalhista)", {}),
    "juros_civel_pre_14905": (("um_por_cento", "selic_tema_1368"), "Juros cíveis até 29/08/2024", {
        "um_por_cento": "1% a.m. (CC, art. 406 original)", "selic_tema_1368": "SELIC (STJ, Tema 1.368)"}),
    "capitalizacao_selic": (("simples", "composta"), "Acumulação da SELIC", {
        "simples": "soma simples das taxas mensais (CJF/Receita)", "composta": "produto dos fatores"}),
    "selic_como": (("juros", "correcao"), "Acréscimo da SELIC tratado como", {"juros": "juros de mora", "correcao": "correção monetária"}),
    "trabalhista_pre_judicial_ate_corte": (bool, "Ação ajuizada desde 30/08/2024: IPCA-E + TR só até 29/08/2024, depois IPCA + "
                                           "taxa legal também na fase pré-judicial (TST)", {}),
    "fazenda_regime_pos_ec136": (("selic", "ipca15_taxa_legal"), "Fazenda: regime após ago/2025", {
        "selic": "SELIC (EC 113/2021)", "ipca15_taxa_legal": "IPCA-15 + taxa legal desde set/2025 (EC 136/2025; Manual CJF 2026)"}),
    "indice_correcao_fazenda": (("IPCA_E", "INPC"), "Fazenda: índice de correção até 11/2021", {}),
    "preservar_valor_nominal": (bool, "Deflação não reduz o principal abaixo do nominal", {}),
    "houve_impugnacao": (bool, "Houve impugnação ao cumprimento (Fazenda, Tema 1.190)", {}),
    "permitir_projecao": (bool, "Mês sem índice: usar o último mês como projeção (marcada na memória)", {}),
    "juros_fixados_no_titulo": (bool, "Juros de 1% a.m. fixados no título (a memória diz isso e não traz o aviso do Tema 1.368)", {}),
    "base_multa_523": (("decurso", "data_calculo"), "Base da multa/honorários do art. 523", {
        "decurso": "débito no fim do prazo (com os juros até lá), atualizado só pela correção até a data do cálculo",
        "data_calculo": "débito na data do cálculo"}),
    "arredondamento": (("exato", "cjf"), "Arredondamento", {"exato": "exato (10 casas)", "cjf": "truncamentos do Manual CJF"}),
    "imputacao": (IMPUTACOES, "Imputação dos pagamentos", {"juros_primeiro": "juros primeiro (CC, art. 354)",
                                                           "capital_primeiro": "capital primeiro",
                                                           "proporcional": "proporcional"}),
}
CRITERIOS_POR_AREA = {
    "civel": ["indice_correcao_civel", "juros_civel_pre_14905", "indice_correcao_pos_14905", "responsabilidade",
              "termo_inicial_juros", "pro_rata", "preservar_valor_nominal", "arredondamento", "imputacao"],
    "trabalhista": ["trabalhista_pre_judicial_ate_corte", "indice_correcao_pos_14905", "capitalizacao_selic", "selic_como",
                    "pro_rata", "preservar_valor_nominal", "arredondamento", "imputacao"],
    "tributario": ["capitalizacao_selic", "selic_como", "arredondamento", "imputacao"],
    "fazenda": ["indice_correcao_fazenda", "fazenda_regime_pos_ec136", "capitalizacao_selic", "selic_como", "houve_impugnacao",
                "responsabilidade", "termo_inicial_juros",
                "pro_rata", "preservar_valor_nominal", "arredondamento", "imputacao"],
}
DESCRICAO_HONORARIOS = "Honorários fixados em quantia certa"
SO_SE_ALTERADO = {"arredondamento", "preservar_valor_nominal", "capitalizacao_selic", "selic_como"}


class ErroPedido(ValueError):
    def __init__(self, erros: list[str]):
        self.erros = erros
        super().__init__("; ".join(erros))


@dataclass
class Pedido:
    processo: dict
    parcelas: list[Parcela]
    params: Parametros
    periodos_manuais: bool = False
    avisos_leitura: list[str] = field(default_factory=list)


def _texto(v) -> str:
    return "" if v is None else str(v).strip()


def _bool(v, rotulo: str) -> bool:
    if isinstance(v, bool):
        return v
    t = _texto(v).lower()
    if t in ("sim", "s", "true", "1", "yes"):
        return True
    if t in ("nao", "não", "n", "false", "0", "no", ""):
        return False
    raise ValueError(f"{rotulo}: use sim ou não (recebido {v!r})")


def ler_pedido(dados: dict, hoje: date | None = None) -> Pedido:
    """Converte o pedido em parcelas e parâmetros do motor; ErroPedido lista todos os problemas encontrados."""
    if not isinstance(dados, dict):
        raise ErroPedido(["o pedido deve ser um objeto JSON"])
    erros: list[str] = []
    conhecidos = {"processo", "area", "data_calculo", "datas", "parcelas", "abatimentos", "acessorios", "criterios", "periodos"}
    for chave in dados:
        if chave not in conhecidos:
            erros.append(f"campo desconhecido no pedido: {chave!r} (aceitos: {', '.join(sorted(conhecidos))})")

    bruto = dados.get("processo") or {}
    if not isinstance(bruto, dict):
        erros.append("processo deve ser um objeto, ex.: {\"numero_cnj\": \"...\", \"vara\": \"...\"}")
        bruto = {}
    processo = {chave: _texto(valor) for chave, valor in
                {"numero_cnj": "", "vara": "", "autor": "", "reu": "", "tipo": "", **bruto}.items()}
    area = _texto(dados.get("area")).lower()
    if area not in AREAS:
        erros.append(f"área inválida: {dados.get('area')!r} (use {', '.join(AREAS)})")

    def ler_data(v, rotulo):
        try:
            return formulario.ler_data(v, rotulo)
        except ValueError as erro:
            erros.append(str(erro))
            return None

    data_calculo = ler_data(dados.get("data_calculo"), "data do cálculo") if dados.get("data_calculo") else (hoje or date.today())
    valores_datas = {}
    for chave, valor in (dados.get("datas") or {}).items():
        if chave not in DATAS:
            erros.append(f"data desconhecida: {chave!r} (aceitas: {', '.join(DATAS)})")
            continue
        valores_datas[DATAS[chave][0]] = ler_data(valor, DATAS[chave][1])

    try:
        parcelas = formulario.parcelas_de_tabela(dados.get("parcelas") or [])
    except formulario.ErroFormulario as erro:
        erros.extend(erro.erros)
        parcelas = []
    if not parcelas and not any("parcela" in e for e in erros):
        erros.append("informe ao menos uma parcela (descrição, valor, data-base)")
    try:
        abatimentos = formulario.abatimentos_de_tabela(dados.get("abatimentos") or [])
    except formulario.ErroFormulario as erro:
        erros.extend(erro.erros)
        abatimentos = []

    ac = dados.get("acessorios") or {}
    for chave in ac:
        if chave not in ("multa_contratual_pct", "honorarios_pct", "honorarios_fixos", "multa_523", "honorarios_cumprimento"):
            erros.append(f"acessório desconhecido: {chave!r}")
    multa_contratual = honorarios_pct = None
    honorarios_fixos = None
    try:
        multa_contratual = formulario.ler_percentual(ac.get("multa_contratual_pct"), "multa contratual (%)")
        honorarios_pct = formulario.ler_percentual(ac.get("honorarios_pct"), "honorários (%)")
    except ValueError as erro:
        erros.append(str(erro))
    if ac.get("honorarios_fixos"):
        fixos = ac["honorarios_fixos"]
        try:
            valor = formulario.ler_valor(fixos.get("valor"), "honorários fixos (valor)")
            data_fixacao = formulario.ler_data(fixos.get("data"), "honorários fixos (data da fixação)")
            if valor is None or valor <= 0 or data_fixacao is None:
                raise ValueError("honorários fixos: informe valor (maior que zero) e data da fixação")
            honorarios_fixos = (valor, data_fixacao)
        except (ValueError, AttributeError) as erro:
            erros.append(str(erro))
    if honorarios_pct is not None and honorarios_fixos is not None:
        erros.append("honorários: informe percentual OU quantia certa, não os dois")
    flags = {}
    for chave, rotulo in (("multa_523", "multa do art. 523"), ("honorarios_cumprimento", "honorários do cumprimento")):
        try:
            flags[chave] = _bool(ac.get(chave, False), rotulo)
        except ValueError as erro:
            erros.append(str(erro))

    criterios = {}
    for chave, valor in (dados.get("criterios") or {}).items():
        if chave not in CRITERIOS:
            erros.append(f"critério desconhecido: {chave!r} (aceitos: {', '.join(CRITERIOS)})")
            continue
        aceitos, rotulo, _ = CRITERIOS[chave]
        if aceitos is bool:
            try:
                criterios[chave] = _bool(valor, rotulo)
            except ValueError as erro:
                erros.append(str(erro))
        elif valor is None and chave == "termo_inicial_juros":
            criterios[chave] = None
        elif chave == "indice_correcao_civel" and re.fullmatch(r"[A-Z][A-Z0-9_]{1,19}", _texto(valor).upper()):
            criterios[chave] = _texto(valor).upper()  # tabela de tribunal importada: conferida contra o banco no cálculo
        elif _texto(valor) not in aceitos:
            erros.append(f"{rotulo}: valor {valor!r} inválido (aceitos: {', '.join(aceitos)})")
        else:
            criterios[chave] = _texto(valor)

    if erros:
        raise ErroPedido(erros)

    params = Parametros(
        area=area, data_calculo=data_calculo, multa_contratual_pct=multa_contratual, honorarios_pct=honorarios_pct,
        honorarios_fixos=honorarios_fixos, abatimentos=abatimentos, **flags, **valores_datas, **criterios,
    )
    pedido = Pedido(processo, parcelas, params)
    periodos = dados.get("periodos")
    if periodos:
        _aplicar_periodos_manuais(pedido, periodos)
    return pedido


def _aplicar_periodos_manuais(pedido: Pedido, periodos: dict) -> None:
    """Critérios do título: cada parcela recebe seus períodos (cortes legais automáticos desligados)."""
    if not isinstance(periodos, dict):
        raise ErroPedido(['"periodos" deve ser um objeto: {"descrição da parcela" ou "*": [períodos]}'])
    params = pedido.params
    todas = list(pedido.parcelas)
    if params.honorarios_fixos is not None:  # o motor cria a parcela de honorários sem períodos: entra como parcela
        valor, data_fixacao = params.honorarios_fixos
        todas.append(Parcela(DESCRICAO_HONORARIOS, valor, data_fixacao, "honorarios"))
    descricoes = {p.descricao for p in todas}
    erros = [f"períodos para parcela inexistente: {chave!r}" for chave in periodos if chave != "*" and chave not in descricoes]
    for parcela in todas:
        linhas = periodos.get(parcela.descricao, periodos.get("*"))
        if not linhas:
            erros.append(f"{parcela.descricao}: sem períodos (informe-os ou use \"*\" para todas as parcelas)")
            continue
        try:
            parcela.periodos = formulario.periodos_de_tabela(linhas)
        except formulario.ErroFormulario as erro:
            erros.extend(f"{parcela.descricao}, {e}" for e in erro.erros)
    if erros:
        raise ErroPedido(erros)
    pedido.parcelas = todas
    pedido.params = replace(params, aplicar_cortes_legais=False, honorarios_fixos=None)
    pedido.periodos_manuais = True


# ---------------------------------------------------------------------------
# Conferência (antes de calcular)
# ---------------------------------------------------------------------------


def _valor_criterio(chave: str, valor) -> str:
    aceitos, _, nomes = CRITERIOS[chave]
    if aceitos is bool:
        return "sim" if valor else "não"
    if valor is None:
        return "padrão da área (citação; evento danoso no extracontratual)" if chave == "termo_inicial_juros" else "—"
    return nomes.get(valor, nome_curto(valor) if isinstance(valor, str) else valor)


GERAIS = ["pro_rata", "preservar_valor_nominal", "arredondamento", "imputacao"]  # valem também com períodos do título


def criterios_da_conferencia(p: Parametros, padrao: Parametros, parcelas=(), manuais: bool = False) -> list[str]:
    """Critérios mostrados na conferência: os que pesam na área e no caso, e TODO critério alterado.

    ``manuais``: períodos do título — os cortes da área não se aplicam, mas os critérios gerais continuam valendo.
    ``parcelas``: no tributário, custas e honorários seguem a regra da Fazenda.
    """
    relevantes = list(GERAIS) if manuais else list(CRITERIOS_POR_AREA[p.area])
    sempre = set()  # pesam neste caso mesmo no padrão
    if manuais and any(x.tipo_juros == "embutido" for parcela in parcelas for x in parcela.periodos):
        relevantes.append("selic_como")
    if (not manuais and p.area == "tributario"
            and (p.honorarios_fixos is not None or any(x.natureza != "principal" for x in parcelas))):
        relevantes += ["indice_correcao_fazenda", "fazenda_regime_pos_ec136", "pro_rata"]
    if p.multa_523 or p.honorarios_cumprimento:
        sempre.add("base_multa_523")
        if p.base_multa_523 == "decurso":  # índice que atualiza a base nos períodos de SELIC
            sempre.add("indice_correcao_civel" if p.area == "civel" else "indice_correcao_fazenda")
    if p.area == "civel" and p.juros_civel_pre_14905 == "selic_tema_1368":
        sempre |= {"capitalizacao_selic", "selic_como"}
    if p.area in ("trabalhista", "tributario", "fazenda") and not manuais:
        sempre.add("capitalizacao_selic")  # a SELIC entra no cálculo dessas áreas
    ajuizada_depois = p.data_ajuizamento is not None and p.data_ajuizamento >= corte_lei_14905()
    saida = []
    for chave in CRITERIOS:
        alterado = getattr(p, chave) != getattr(padrao, chave)
        if chave == "permitir_projecao":
            continue  # tem linha própria
        if chave == "trabalhista_pre_judicial_ate_corte" and not (ajuizada_depois or alterado):
            continue  # só pesa quando a ação foi ajuizada depois de 30/08/2024
        if alterado or chave in sempre:
            saida.append(chave)
        elif chave in relevantes and not (chave in SO_SE_ALTERADO or (chave == "imputacao" and not p.abatimentos)):
            saida.append(chave)
    return saida


def _parcelas_do_motor(pedido: Pedido) -> list[Parcela]:
    """As parcelas que o motor vai calcular (inclui os honorários fixos, que o motor acrescenta)."""
    todas = list(pedido.parcelas)
    if pedido.params.honorarios_fixos is not None:
        valor, data_fixacao = pedido.params.honorarios_fixos
        todas.append(Parcela(DESCRICAO_HONORARIOS, valor, data_fixacao, "honorarios"))
    return todas


def texto_conferencia(pedido: Pedido, repo: RepositorioIndices, comando: str = "pedido") -> tuple[str, list[str]]:
    """Tudo o que o cálculo vai usar, em português, para o advogado confirmar. Devolve (texto, problemas)."""
    p = pedido.params
    padrao = Parametros(area=p.area, data_calculo=p.data_calculo)
    linhas = ["PEDIDO DE CÁLCULO — confira antes de calcular", ""]
    proc = pedido.processo
    if proc.get("numero_cnj") or proc.get("vara") or proc.get("tipo"):
        partes = [proc.get("numero_cnj") or "sem número", proc.get("vara"), proc.get("tipo")]
        linhas.append("Processo: " + " — ".join(x for x in partes if x))
    linhas.append(f"Área: {NOMES_AREA[p.area]}   |   Data do cálculo: {formatar_data(p.data_calculo)} "
                  f"(índices até {mes_anterior(p.data_calculo):%m/%Y})")
    datas = [f"{rotulo}: {formatar_data(getattr(p, campo))}" for campo, rotulo in DATAS.values() if getattr(p, campo)]
    if datas:
        linhas.append("Datas: " + "; ".join(datas))
    elif p.area == "tributario" or pedido.periodos_manuais:
        linhas.append("Datas: não se aplicam (" + ("os períodos do título fixam os critérios)" if pedido.periodos_manuais
                                                    else "a SELIC corre do mês seguinte a cada pagamento)"))
    else:
        linhas.append("Datas: nenhuma informada")
    linhas.append("")
    linhas.append(f"Parcelas ({len(pedido.parcelas)}):")
    for n, parcela in enumerate(pedido.parcelas, start=1):
        extra = f" — evento {formatar_data(parcela.data_evento)}" if parcela.data_evento else ""
        linhas.append(f"  {n}. {parcela.descricao} — {formatar_brl(parcela.valor_original)} — data-base "
                      f"{formatar_data(parcela.data_base)} — {NOMES_NATUREZA.get(parcela.natureza, parcela.natureza)}{extra}")
    if p.abatimentos:
        linhas.append(f"Pagamentos/abatimentos ({len(p.abatimentos)}):")
        for a in p.abatimentos:
            linhas.append(f"  - {formatar_data(a.data)} — {formatar_brl(a.valor)} — {a.descricao}")
    else:
        linhas.append("Pagamentos/abatimentos: nenhum")
    acess = []
    if p.multa_contratual_pct is not None:
        acess.append(f"multa contratual {p.multa_contratual_pct}% sobre principal corrigido + juros")
    if p.honorarios_pct is not None:
        acess.append(f"honorários {p.honorarios_pct}% sobre principal corrigido + juros"
                     + (" + multa contratual" if p.multa_contratual_pct else ""))
    if p.honorarios_fixos is not None:
        acess.append(f"honorários fixos {formatar_brl(p.honorarios_fixos[0])} em {formatar_data(p.honorarios_fixos[1])}")
    if p.multa_523:
        acess.append("multa de 10% do art. 523")
    if p.honorarios_cumprimento:
        acess.append("honorários de 10% do cumprimento (art. 523)")
    linhas.append("Acessórios: " + ("; ".join(acess) if acess else "nenhum"))
    linhas.append("")
    if pedido.periodos_manuais:
        linhas.append("Critérios: períodos informados pelo título (cortes legais automáticos DESLIGADOS)")
        linhas.append("Critérios gerais que continuam valendo ([padrão] = escolha padrão do sistema; pode ser trocada):")
    else:
        linhas.append("Critérios que mudam o resultado ([padrão] = escolha padrão do sistema; pode ser trocada):")
        linhas.append("  Cortes legais automáticos: " + "; ".join(cortes_da_area(p, _parcelas_do_motor(pedido))))
    for chave in criterios_da_conferencia(p, padrao, _parcelas_do_motor(pedido), pedido.periodos_manuais):
        valor = getattr(p, chave)
        marca = "  [ALTERADO]" if valor != getattr(padrao, chave) else "  [padrão]"
        texto_valor = _valor_criterio(chave, valor)
        if chave == "indice_correcao_civel" and valor not in INDICES_CIVEIS:  # tabela importada: o nome dela
            try:
                texto_valor = f"{valor} — {repo.info(valor).nome}"
            except IndiceDesconhecido:
                pass
        linhas.append(f"  - {CRITERIOS[chave][1]}: {texto_valor}{marca}")
    if p.permitir_projecao:
        linhas.append("  - Mês sem índice: usar o último mês como PROJEÇÃO (marcada na memória)")
    problemas: list[str] = []
    avisos: list[str] = []
    linhas.append("")
    linhas.append("Como cada parcela será atualizada:")
    if not p.pro_rata and p.area != "tributario":
        linhas.append("  (meses cheios, sem pró-rata: o mês da data inicial conta inteiro, e o mês em que o regime muda fica "
                      "inteiro em um dos períodos — veja \"Meses de mudança de regime\" abaixo)")
    for parcela in _parcelas_do_motor(pedido):
        linhas.append(f"  {parcela.descricao}:")
        marcos = "" if pedido.periodos_manuais else marcos_da_parcela(parcela, p)
        if marcos:
            linhas.append(f"    {marcos[0].upper()}{marcos[1:]}")
        try:
            periodos = parcela.periodos if pedido.periodos_manuais else montar_periodos(parcela, p)
            for pa in aplicar_periodos(normalizar_periodos(periodos), p.data_calculo):
                fim = max(pa.fim_exclusivo - timedelta(days=1), pa.inicio)
                linhas.append(f"    • {formatar_data(pa.inicio)} a {formatar_data(fim)} — {pa.descricao}")
            for aviso in avisos_da_regra(parcela, p):
                if aviso not in avisos:
                    avisos.append(aviso)
        except (ErroRegra, ParametroInvalido, ValueError) as erro:
            problemas.append(f"{parcela.descricao}: {erro}")
            linhas.append(f"    ✗ {erro}")
    # roda o motor uma vez para descobrir índice faltante ou regra impossível (o total só é mostrado depois)
    if not problemas:
        try:
            memoria = calcular(_copiar_parcelas(pedido.parcelas), p, repo)
            for aviso in memoria.avisos:
                if aviso not in avisos:
                    avisos.append(aviso)
            transicoes = [(mp.parcela.descricao, meses_de_transicao(mp)) for mp in memoria.parcelas]
            if any(t for _, t in transicoes):
                linhas.append("")
                linhas.append("Meses de mudança de regime (em qual período cada um foi calculado):")
                linhas.extend(f"  {descricao}: {t}" for descricao, t in transicoes if t)
        except IndiceAusente as erro:
            problemas.append(_texto_indice_ausente(erro, p, comando))
        except IndiceDesconhecido as erro:
            problemas.append(f"a tabela {erro.args[0] if erro.args else erro} não está na calculadora — veja as disponíveis com o comando 'tabelas' ou "
                             "importe a do tribunal com 'importar'")
        except (ErroRegra, ParametroInvalido) as erro:
            problemas.append(str(erro))
    if avisos:
        linhas.append("")
        linhas.append("Avisos que vão constar da memória:")
        linhas.extend(f"  ! {a}" for a in avisos)
    if problemas:
        linhas.append("")
        linhas.append("PROBLEMAS — o cálculo não roda assim:")
        linhas.extend(f"  ✗ {x}" for x in problemas)
    return "\n".join(linhas), problemas


def _copiar_parcelas(parcelas: list[Parcela]) -> list[Parcela]:
    return [replace(x, periodos=list(x.periodos)) for x in parcelas]


def _texto_indice_ausente(erro: IndiceAusente, p: Parametros, comando: str = "pedido") -> str:
    if "nenhum período da tabela composta cobre o mês" in str(erro):
        return (f"a tabela {erro.indice} montada pela calculadora não cobre {erro.mes:%m/%Y} (antes do início da cadeia de "
                "índices oficiais do tribunal). Use a tabela oficial do tribunal (comando 'importar') ou informe os "
                "períodos do título.")
    if not erro.motivo.startswith("último mês gravado"):  # antes do início da série, lacuna ou série encerrada
        return (f"{erro} — a calculadora não tem esse mês para {erro.indice}. Escolha outra tabela ou índice (comando "
                "'tabelas'), importe a tabela completa do tribunal (comando 'importar') ou informe os períodos do título.")
    texto = f"{erro} — nenhum índice é estimado sem autorização."
    mes = erro.mes
    nova = date(mes.year, mes.month, 1)
    if nova < p.data_calculo:
        if comando == "atualizar-salvo":
            opcoes = (f"(a) atualizar até {formatar_data(nova)} (último índice divulgado): --data {formatar_data(nova)}; "
                      "(b) projeção do último mês disponível, marcada na memória: --permitir-projecao")
        else:
            opcoes = (f"(a) data do cálculo {formatar_data(nova)} (último índice divulgado): \"data_calculo\": "
                      f"\"{formatar_data(nova)}\"; (b) projeção do último mês disponível, marcada na memória: "
                      "\"criterios\": {\"permitir_projecao\": true}")
        texto += f" Opções: {opcoes}. O comando 'indices' busca o que já foi publicado."
    return texto


# ---------------------------------------------------------------------------
# Cálculo e arquivos
# ---------------------------------------------------------------------------


def calcular_pedido(pedido: Pedido, repo: RepositorioIndices) -> Memoria:
    return calcular(_copiar_parcelas(pedido.parcelas), pedido.params, repo)


def texto_resultado(memoria: Memoria) -> str:
    p = memoria.parametros
    linhas = [f"RESULTADO — {NOMES_AREA[p.area]}, cálculo em {formatar_data(p.data_calculo)}", ""]
    for nome, valor in memoria.resumo.linhas():
        linhas.append(f"  {nome:<22} {formatar_brl(valor):>18}")
    if memoria.resumo.saldo_credor:
        linhas.append(f"  Saldo credor (pagamento acima do devido): {formatar_brl(memoria.resumo.saldo_credor)}")
    linhas.append("")
    linhas.append("Por parcela:")
    for mp in memoria.parcelas:
        linhas.append(f"  - {mp.parcela.descricao}: {formatar_brl(mp.valor_original)} → corrigido "
                      f"{formatar_brl(mp.principal_corrigido)} + juros {formatar_brl(mp.juros)} = {formatar_brl(mp.total)}")
    projecoes = [f"{f.codigo} {', '.join(m.strftime('%m/%Y') for m in f.meses_projecao)}" for f in memoria.fontes if f.meses_projecao]
    if projecoes:
        linhas.append("")
        linhas.append("ATENÇÃO — índices usados como PROJEÇÃO (refazer quando saírem): " + "; ".join(projecoes))
    if memoria.avisos:
        linhas.append("")
        linhas.append("Avisos:")
        linhas.extend(f"  ! {a}" for a in memoria.avisos)
    return "\n".join(linhas)


def gravar_arquivos(memoria: Memoria, pedido: Pedido, pasta: Path, config=None) -> dict[str, Path]:
    """PDF e Excel do cálculo na pasta de saída, sem sobrescrever arquivos anteriores.

    Fora do plugin grava também a entrada (JSON), base para montar um caso-ouro; no plugin ela fica só
    no cálculo salvo (a pasta de relatórios do advogado recebe só o PDF e o Excel).
    """
    from calculo_judicial import relatorio

    arquivos = relatorio.gravar(memoria, Path(pasta), config, pedido.processo)
    if not os.environ.get("CALCULADORA_VERSAO"):
        entrada = arquivos["pdf"].with_name(arquivos["pdf"].stem + "_entrada.json")
        try:
            with open(entrada, "x", encoding="utf-8") as saida:
                saida.write(entrada_json(pedido))
            arquivos["entrada"] = entrada
        except FileExistsError:
            pass
    return arquivos


def entrada_json(pedido: Pedido) -> str:
    """Entrada no formato salvo pela interface (abre em 'Cálculos salvos')."""
    return serializacao.entrada_para_json(pedido.processo, _copiar_parcelas(pedido.parcelas), pedido.params)


def pedido_de_entrada_salva(entrada: str) -> Pedido:
    """Reconstrói um pedido a partir de um cálculo salvo (para atualizar até nova data)."""
    processo, parcelas, params = serializacao.entrada_de_json(entrada)
    return Pedido(processo, parcelas, params, periodos_manuais=not params.aplicar_cortes_legais)


def atualizar_ate(pedido: Pedido, nova_data: date) -> Pedido:
    """Mesmo cálculo até outra data; períodos manuais que iam até a data antiga acompanham a nova."""
    antiga = pedido.params.data_calculo
    parcelas = []
    for parcela in pedido.parcelas:
        periodos = [replace(x, ate=nova_data) if x.ate == antiga else x for x in parcela.periodos]
        parcelas.append(replace(parcela, periodos=periodos))
    return Pedido(pedido.processo, parcelas, replace(pedido.params, data_calculo=nova_data), pedido.periodos_manuais)


def modelo_pedido(area: str = "civel") -> dict:
    """Pedido de exemplo para a área (ponto de partida para o Claude preencher)."""
    base = {
        "processo": {"numero_cnj": "", "vara": "", "tipo": ""},
        "area": area,
        "data_calculo": formatar_data(date.today()),
        "datas": {},
        "parcelas": [{"descricao": "Parcela 1", "valor": "10.000,00", "data_base": "10/01/2023", "natureza": "principal"}],
        "abatimentos": [],
        "acessorios": {"honorarios_pct": None},
        "criterios": {},
    }
    if area == "civel":
        base["datas"] = {"citacao": "15/03/2023"}
        base["acessorios"].update({"multa_contratual_pct": None, "multa_523": False, "honorarios_cumprimento": False})
    if area == "fazenda":
        base["datas"] = {"citacao": "15/03/2023"}
        base["acessorios"]["honorarios_cumprimento"] = False
        base["criterios"] = {"houve_impugnacao": False}
    if area == "trabalhista":
        base["datas"] = {"ajuizamento": "10/06/2021"}
        base["parcelas"][0]["data_base"] = "05/02/2019"
    return base


def ler_arquivo_pedido(caminho: Path, hoje: date | None = None) -> Pedido:
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ErroPedido([f"arquivo não encontrado: {caminho}"]) from None
    except json.JSONDecodeError as erro:
        raise ErroPedido([f"JSON inválido em {caminho}: {erro}"]) from None
    return ler_pedido(dados, hoje)


__all__ = [
    "CRITERIOS", "ErroPedido", "Pedido", "atualizar_ate", "calcular_pedido", "entrada_json", "gravar_arquivos",
    "ler_arquivo_pedido", "ler_pedido", "modelo_pedido", "pedido_de_entrada_salva", "texto_conferencia", "texto_resultado",
]
