"""Interface web (SPEC, seção 7): Novo cálculo, Cálculos salvos, Índices, Configurações.

    streamlit run src/calculo_judicial/app/streamlit_app.py

A interface só monta as entradas e chama o motor (``calculo_judicial.motor.calcular``);
nenhuma regra de cálculo fica aqui. Valores monetários são digitados como texto e
convertidos para Decimal (nunca float).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):
    # executado por "streamlit run caminho/streamlit_app.py": garante o pacote no path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from calculo_judicial import pedido as mod_pedido
from calculo_judicial import relatorio
from calculo_judicial.app import configuracoes, formulario, importacao, perfis, persistencia, serializacao
from calculo_judicial.datas import formatar_data, primeiro_dia, somar_meses
from calculo_judicial.decimais import formatar_brl, para_decimal
from calculo_judicial.indices import atualizacao_automatica as auto
from calculo_judicial.indices import coletor_bcb
from calculo_judicial.indices.modelo import IndiceAusente, IndiceDesconhecido, RepositorioSQLite, caminho_db, conectar
from calculo_judicial.motor import ErroRegra, ParametroInvalido, Parametros, Parcela, calcular
from calculo_judicial.motor.parcela import IMPUTACOES, NATUREZAS
from calculo_judicial.motor.regras import montar_periodos

# instalada como plugin do Claude Code (cj.py define a versão): sem as opções de quem desenvolve a calculadora
MODO_PLUGIN = bool(os.environ.get("CALCULADORA_VERSAO"))
TELAS = ["Novo cálculo", "Cálculos salvos", "Índices", "Configurações"]
AREAS = {"civel": "Cível", "trabalhista": "Trabalhista", "tributario": "Tributário", "fazenda": "Fazenda Pública"}
TIPOS_PROCESSO = ["liquidação", "cumprimento", "execução fiscal", "atualização simples"]
NOMES_INDICE = {
    "TJGO": "TJGO (INPC até 29/08/2024; IPCA depois)", "TJSP": "Tabela prática do TJSP", "INPC": "INPC", "IPCA": "IPCA",
    "IPCA_E": "IPCA-E", "IPCA_15": "IPCA-15", "IGPM": "IGP-M", "JF": "Tabela da JF (condenatórias em geral)",
    "JF_PREV": "Tabela da JF (benefícios previdenciários)", "SELIC": "SELIC", "": "(sem correção)",
}
OPCOES_INDICE = ["TJGO", "TJSP", "INPC", "IPCA", "IPCA_E", "IPCA_15", "IGPM", "JF", "JF_PREV"]
OPCOES_INDICE_CORRECAO_PERIODO = ["", "TJGO", "TJSP", "INPC", "IPCA", "IPCA_E", "IPCA_15", "IGPM", "JF", "JF_PREV", "SELIC"]
OPCOES_INDICE_JUROS = ["", "TAXA_LEGAL", "TR", "JUROS_POUPANCA"]
NOMES_TERMO = {
    "(padrão)": "padrão da área (citação; evento no extracontratual)", "citacao": "citação",
    "vencimento": "vencimento de cada parcela", "evento": "evento danoso", "transito": "trânsito em julgado",
}
NOMES_FAZENDA = {"selic": "SELIC (EC 113/2021)", "ipca15_taxa_legal": "IPCA-15 + taxa legal (EC 136/2025)"}
NOMES_SELIC_COMO = {"juros": "como juros de mora", "correcao": "como correção monetária"}
NOMES_CAP_SELIC = {"simples": "simples (soma das taxas)", "composta": "composta (produto dos fatores)"}
NOMES_JUROS_PRE = {"um_por_cento": "1% a.m. (CC, art. 406 original)", "selic_tema_1368": "SELIC (STJ, Tema 1.368)"}
NOMES_IMPUTACAO = {"juros_primeiro": "juros primeiro (art. 354 CC)", "capital_primeiro": "capital primeiro", "proporcional": "proporcional"}
NOMES_BASE_523 = {"decurso": "débito no fim do prazo (atualizado)", "data_calculo": "débito na data do cálculo"}
DATAS_EXIGIDAS = {"civel": ["Citação"], "fazenda": ["Citação"], "trabalhista": ["Ajuizamento"], "tributario": []}
INDICES_DISPONIBILIDADE = ["IPCA", "INPC", "IPCA_E", "IPCA_15", "IGPM", "SELIC", "TR", "TAXA_LEGAL"]
CHAVE_HONORARIOS = "honorarios_fixos"
DESCRICAO_HONORARIOS = "Honorários fixados em quantia certa"

PADROES = {
    "f_numero_cnj": "", "f_vara": "", "f_autor": "", "f_reu": "", "f_area": "civel", "f_tipo": "cumprimento",
    "f_data_calculo": formatar_data(date.today()), "f_data_citacao": "", "f_data_ajuizamento": "", "f_data_transito": "",
    "f_data_intimacao": "", "f_data_decurso_523": "",
    "f_pro_rata": False, "f_editar_periodos": False,
    "f_multa_523": False, "f_multa_contratual": "", "f_honorarios_modo": "nenhum", "f_honorarios_pct": "",
    "f_honorarios_fixo_valor": "", "f_honorarios_fixo_data": "", "f_honorarios_cumprimento": False,
    "f_imputacao": "juros_primeiro",
    "f_responsabilidade": "contratual", "f_termo_inicial_juros": "(padrão)", "f_indice_civel": "TJGO",
    "f_indice_pos": "IPCA", "f_juros_pre": "um_por_cento", "f_cap_selic": "simples", "f_selic_como": "juros",
    "f_trab_pre_corte": True, "f_fazenda_regime": "selic", "f_indice_fazenda": "IPCA_E", "f_preservar_nominal": True,
    "f_impugnacao": False, "f_permitir_projecao": False, "f_base_523": "decurso", "f_arredondamento": "exato",
    "f_custas_523": True,
}
# campos que um perfil redefine (SPEC, seção 7: "escolher um preenche todos os campos abaixo"); ficam de fora os
# fatos do processo (partes, datas, parcelas, abatimentos, multa contratual e honorários fixados no título)
CAMPOS_CRITERIO = [
    "f_pro_rata", "f_editar_periodos", "f_multa_523", "f_honorarios_cumprimento", "f_imputacao", "f_responsabilidade",
    "f_termo_inicial_juros", "f_indice_civel", "f_indice_pos", "f_juros_pre", "f_cap_selic", "f_selic_como",
    "f_trab_pre_corte", "f_fazenda_regime", "f_indice_fazenda", "f_preservar_nominal", "f_impugnacao",
    "f_permitir_projecao", "f_base_523", "f_arredondamento", "f_custas_523",
]

MAPA_PARAMETROS = {
    "pro_rata": "f_pro_rata", "multa_523": "f_multa_523",
    "honorarios_cumprimento": "f_honorarios_cumprimento", "imputacao": "f_imputacao", "responsabilidade": "f_responsabilidade",
    "indice_correcao_civel": "f_indice_civel", "indice_correcao_pos_14905": "f_indice_pos", "juros_civel_pre_14905": "f_juros_pre",
    "capitalizacao_selic": "f_cap_selic", "selic_como": "f_selic_como", "trabalhista_pre_judicial_ate_corte": "f_trab_pre_corte",
    "fazenda_regime_pos_ec136": "f_fazenda_regime", "indice_correcao_fazenda": "f_indice_fazenda",
    "preservar_valor_nominal": "f_preservar_nominal", "houve_impugnacao": "f_impugnacao",
    "permitir_projecao": "f_permitir_projecao", "base_multa_523": "f_base_523", "arredondamento": "f_arredondamento",
    "custas_na_base_523": "f_custas_523",
    "data_citacao": "f_data_citacao", "data_ajuizamento": "f_data_ajuizamento", "data_transito": "f_data_transito",
    "data_intimacao": "f_data_intimacao", "data_decurso_523": "f_data_decurso_523", "data_calculo": "f_data_calculo",
}

PARCELAS_VAZIAS = [{"descricao": "", "valor": "", "data_base": "", "natureza": "principal", "data_evento": ""}]
ABATIMENTOS_VAZIOS = [{"data": "", "valor": "", "descricao": ""}]
CAMPOS_DATA = {"data_citacao", "data_ajuizamento", "data_transito", "data_intimacao", "data_decurso_523", "data_calculo"}
ROTULOS_DATA = {
    "data_calculo": "Data do cálculo", "data_citacao": "Citação", "data_ajuizamento": "Ajuizamento",
    "data_transito": "Trânsito em julgado", "data_intimacao": "Intimação (art. 523)", "data_decurso_523": "Fim do prazo do art. 523",
}
# parâmetros que não mudam os períodos gerados pelas regras (só o cálculo sobre eles)
CAMPOS_FORA_DOS_PERIODOS = {
    "abatimentos", "multa_contratual_pct", "honorarios_pct", "honorarios_fixos", "multa_523", "honorarios_cumprimento",
    "imputacao", "base_multa_523", "custas_na_base_523", "houve_impugnacao", "arredondamento", "permitir_projecao", "data_calculo",
    "data_intimacao", "data_decurso_523", "aplicar_cortes_legais", "preservar_valor_nominal", "pro_rata",
}
LOG_ERROS = coletor_bcb.PASTA_DADOS / "log" / "interface_erros.log"


def ler_data_formulario(texto, rotulo: str, obrigatoria: bool = False) -> date | None:
    """Datas digitadas como dd/mm/aaaa (também aceita aaaa-mm-dd)."""
    try:
        valor = formulario.ler_data(texto, rotulo)
    except ValueError as erro:
        raise formulario.ErroFormulario([str(erro)]) from None
    if valor is None and obrigatoria:
        raise formulario.ErroFormulario([f"{rotulo}: informe a data (dd/mm/aaaa)"])
    return valor


def _texto_data(valor) -> str:
    if valor is None or valor == "":
        return ""
    if isinstance(valor, date):
        return formatar_data(valor)
    return str(valor)


def _md(texto: str) -> str:
    """Texto livre em rótulos markdown do Streamlit: "$" não pode virar fórmula."""
    return str(texto).replace("$", "\\$")


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------


def iniciar_estado() -> None:
    s = st.session_state
    for chave, valor in PADROES.items():
        s.setdefault(chave, valor)
    s.setdefault("parcelas_base", list(PARCELAS_VAZIAS))
    s.setdefault("parcelas_versao", 0)
    s.setdefault("abatimentos_base", list(ABATIMENTOS_VAZIOS))
    s.setdefault("abatimentos_versao", 0)
    s.setdefault("periodos", {})  # chave da parcela -> {linhas, assinatura, editado, data_calculo, erro, versao}
    s.setdefault("periodos_editados", {})  # chave da parcela -> linhas devolvidas pelo editor na última execução
    s.setdefault("memoria", None)
    s.setdefault("calculo_entrada", None)  # (parcelas, parâmetros) do último cálculo
    s.setdefault("assinatura_calculo", None)
    s.setdefault("calculo_id", None)
    s.setdefault("exclusoes", 0)
    s.setdefault("tela", TELAS[0])


def preservar_formulario() -> None:
    """O Streamlit descarta o estado dos campos que não foram desenhados nesta execução (outra tela).

    Reatribuir as chaves no início de cada execução mantém o formulário ao trocar de tela
    (contorno documentado do Streamlit para estado de widgets entre páginas).
    """
    s = st.session_state
    for chave in PADROES:
        if chave in s:
            s[chave] = s[chave]


def guardar_tabelas() -> None:
    """Ao sair do Novo cálculo: as edições das tabelas viram a base (o estado dos editores se perde)."""
    s = st.session_state
    if "parcelas_editadas" in s:
        s["parcelas_base"] = s.pop("parcelas_editadas") or list(PARCELAS_VAZIAS)
        s["parcelas_versao"] += 1
    if "abatimentos_editados" in s:
        s["abatimentos_base"] = s.pop("abatimentos_editados") or list(ABATIMENTOS_VAZIOS)
        s["abatimentos_versao"] += 1
    for chave, linhas in s.get("periodos_editados", {}).items():
        registro = s["periodos"].get(chave)
        if registro is not None:
            registro["linhas"] = linhas
            registro["versao"] += 1
    s["periodos_editados"] = {}


def ao_trocar_tela() -> None:
    if st.session_state.get("tela_anterior") == "Novo cálculo":
        guardar_tabelas()


def limpar_calculo() -> None:
    """Novo cálculo em branco (não apaga nada do banco)."""
    s = st.session_state
    for chave, valor in PADROES.items():
        s[chave] = valor
    s["parcelas_base"], s["abatimentos_base"] = list(PARCELAS_VAZIAS), list(ABATIMENTOS_VAZIOS)
    s["parcelas_versao"] += 1
    s["abatimentos_versao"] += 1
    for chave in ("parcelas_editadas", "abatimentos_editados", "perfil_escolhido", "indice_faltante", "aviso_carga"):
        s.pop(chave, None)
    s["periodos"], s["periodos_editados"] = {}, {}
    s["memoria"], s["calculo_entrada"], s["assinatura_calculo"], s["calculo_id"] = None, None, None, None


def aplicar_perfil(chave: str) -> None:
    s = st.session_state
    for campo in CAMPOS_CRITERIO:
        s[campo] = PADROES[campo]
    valores = perfis.aplicar_perfil(chave)
    s["f_area"] = valores.pop("area")
    s["f_tipo"] = valores.pop("tipo_processo")
    s["f_editar_periodos"] = not valores.pop("aplicar_cortes_legais", True)
    for campo, valor in valores.items():
        widget = MAPA_PARAMETROS.get(campo)
        if widget:
            s[widget] = valor
    s["perfil_escolhido"] = chave


def _registros(df_ou_lista) -> list[dict]:
    if isinstance(df_ou_lista, pd.DataFrame):
        registros = df_ou_lista.to_dict("records")
    else:
        registros = list(df_ou_lista or [])
    limpos = []
    for r in registros:
        limpo = {}
        for k, v in r.items():
            try:
                vazio = pd.isna(v)
            except (TypeError, ValueError):
                vazio = False
            if isinstance(vazio, bool) and vazio:
                v = None
            elif isinstance(v, pd.Timestamp):
                v = v.date()
            limpo[k] = v
        limpos.append(limpo)
    return limpos


def montar_parametros() -> Parametros:
    s = st.session_state
    erros: list[str] = []

    def tentar(funcao, *args):
        try:
            return funcao(*args)
        except formulario.ErroFormulario as erro:
            erros.extend(erro.erros)
        except ValueError as erro:
            erros.append(str(erro))
        return None

    multa_contratual = tentar(formulario.ler_percentual, s["f_multa_contratual"], "Multa contratual (%)")
    honorarios_pct = None
    honorarios_fixos = None
    if s["f_honorarios_modo"] == "percentual":
        honorarios_pct = tentar(formulario.ler_percentual, s["f_honorarios_pct"], "Honorários (%)")
        if honorarios_pct is None and not erros:
            erros.append("Honorários (%): informe o percentual ou escolha 'nenhum'")
    if s["f_honorarios_modo"] == "fixo":
        valor = tentar(formulario.ler_valor, s["f_honorarios_fixo_valor"], "Honorários fixos (R$)")
        data_fixacao = tentar(ler_data_formulario, s["f_honorarios_fixo_data"], "Data da fixação dos honorários", True)
        if valor is not None and valor <= 0:
            erros.append("Honorários fixos (R$): o valor deve ser maior que zero")
        elif valor is None and not any("Honorários fixos" in e for e in erros):
            erros.append("Honorários fixos (R$): informe o valor ou escolha 'nenhum'")
        elif data_fixacao is not None:
            honorarios_fixos = (valor, data_fixacao)
    termo = s["f_termo_inicial_juros"]
    valores = {}
    for campo, widget in MAPA_PARAMETROS.items():
        if campo in CAMPOS_DATA:
            valores[campo] = tentar(ler_data_formulario, s[widget], ROTULOS_DATA[campo], campo == "data_calculo")
        else:
            valores[campo] = s[widget]
    abatimentos = tentar(formulario.abatimentos_de_tabela, s.get("abatimentos_editados", s["abatimentos_base"])) or []
    if erros:
        raise formulario.ErroFormulario(erros)
    return Parametros(
        area=s["f_area"],
        multa_contratual_pct=multa_contratual,
        honorarios_pct=honorarios_pct,
        honorarios_fixos=honorarios_fixos,
        abatimentos=abatimentos,
        termo_inicial_juros=None if termo == "(padrão)" else termo,
        aplicar_cortes_legais=not s["f_editar_periodos"],
        **valores,
    )


def processo_atual() -> dict:
    s = st.session_state
    return {"numero_cnj": s["f_numero_cnj"], "vara": s["f_vara"], "autor": s["f_autor"], "reu": s["f_reu"], "tipo": s["f_tipo"]}


# ---------------------------------------------------------------------------
# Períodos editáveis (critérios do título)
# ---------------------------------------------------------------------------


def chave_parcela(parcela: Parcela) -> str:
    """Identidade da parcela para os períodos (não a posição na tabela: excluir uma linha não troca os períodos)."""
    evento = parcela.data_evento.isoformat() if parcela.data_evento else ""
    return f"{parcela.descricao}|{parcela.data_base.isoformat()}|{parcela.natureza}|{evento}"


def _id_curto(chave: str) -> str:
    return hashlib.md5(chave.encode("utf-8")).hexdigest()[:10]


def opcoes_indice_civel() -> tuple[list[str], dict[str, str]]:
    """Índices fixos e as tabelas de tribunal que estiverem no banco (ex.: a do TJMG importada pelo advogado)."""
    from calculo_judicial.indices.tabelas import tabelas_de_correcao

    opcoes, nomes = list(OPCOES_INDICE), dict(NOMES_INDICE)
    try:
        conn = conectar()
        try:
            for t in tabelas_de_correcao(conn):
                if t.codigo not in opcoes and t.codigo != "IPCA_15" and t.codigo not in ("SELIC",):
                    opcoes.append(t.codigo)
                    nomes[t.codigo] = t.nome
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - sem banco, ficam as opções fixas
        pass
    return opcoes, nomes


def assinatura_regra(parcela: Parcela, params: Parametros) -> str:
    """O que muda os períodos gerados pela regra (sem a data do cálculo, tratada à parte)."""
    dados = {k: v for k, v in params.__dict__.items() if k not in CAMPOS_FORA_DOS_PERIODOS}
    return json.dumps([chave_parcela(parcela), serializacao.para_json(dados)], sort_keys=True, ensure_ascii=False)


def _periodos_texto(linhas: list[dict]) -> list[dict]:
    return [{**l, "de": _texto_data(l.get("de")), "ate": _texto_data(l.get("ate"))} for l in linhas]


def gerar_periodos(parcela: Parcela, params: Parametros) -> tuple[list[dict], str | None]:
    """Períodos da regra legal da área (os mesmos dos cortes automáticos), como linhas editáveis."""
    try:
        periodos = montar_periodos(parcela, replace(params, aplicar_cortes_legais=True))
        return _periodos_texto(formulario.periodos_para_tabela(periodos)), None
    except (ErroRegra, ParametroInvalido, ValueError) as erro:
        modelo = {"de": formatar_data(parcela.data_base), "ate": formatar_data(params.data_calculo), "indice_correcao": "",
                  "tipo_juros": "nenhum", "taxa_juros_am": "", "indice_juros": "", "capitalizacao_correcao": "composta",
                  "capitalizacao_juros": "", "acrescimo_final_pct": "", "selic_sobre_juros": True, "descricao": "",
                  "base_legal": "", "gerado": ""}
        return [modelo], str(erro)


def _assinaturas(linhas: list[dict]) -> list[str]:
    return [formulario.assinatura_periodo(l) for l in linhas
            if not all(formulario._vazio(l.get(c)) for c in ("de", "ate", "indice_correcao", "taxa_juros_am", "indice_juros"))]


def _registro_periodos(parcela: Parcela, params: Parametros, anterior: dict | None) -> dict:
    linhas, erro = gerar_periodos(parcela, params)
    return {"linhas": linhas, "assinatura": assinatura_regra(parcela, params), "editado": False,
            "data_calculo": params.data_calculo, "erro": erro, "versao": anterior["versao"] + 1 if anterior else 0}


def parcelas_com_honorarios(parcelas: list[Parcela], params: Parametros) -> list[Parcela]:
    """Com períodos manuais, os honorários fixados em quantia certa também precisam de períodos próprios."""
    if params.honorarios_fixos is None:
        return list(parcelas)
    valor, data_fixacao = params.honorarios_fixos
    return list(parcelas) + [Parcela(DESCRICAO_HONORARIOS, valor, data_fixacao, "honorarios")]


def editar_periodos(parcelas: list[Parcela], params: Parametros) -> None:
    """Lista de períodos de cada parcela para ajuste manual (critérios do título)."""
    s = st.session_state
    st.info("Períodos por parcela, gerados pelas regras da área: edite o que o título fixar de diferente. "
            "Com esta opção os cortes legais automáticos ficam desligados; a memória descreve o que for editado.")
    for n, parcela in enumerate(parcelas_com_honorarios(parcelas, params), start=1):
        chave = chave_parcela(parcela)
        registro = s["periodos"].get(chave)
        editados = s["periodos_editados"].get(chave)
        if registro is not None and editados is not None and _assinaturas(editados) != _assinaturas(registro["linhas"]):
            registro["editado"] = True
        if registro is None or (not registro["editado"] and (registro["assinatura"] != assinatura_regra(parcela, params)
                                                             or registro["data_calculo"] != params.data_calculo)):
            registro = _registro_periodos(parcela, params, registro)
            s["periodos"][chave] = registro
            s["periodos_editados"].pop(chave, None)
        st.markdown(_md(f"**Parcela {n}: {parcela.descricao}** — {formatar_brl(parcela.valor_original)} em "
                        f"{formatar_data(parcela.data_base)}" + (" · editada" if registro["editado"] else "")))
        if registro["erro"]:
            st.warning(f"Os períodos não puderam ser gerados pelas regras ({registro['erro']}). Complete as datas acima "
                       "ou informe os períodos manualmente; sem isso o cálculo não roda.")
        elif registro["editado"] and registro["assinatura"] != assinatura_regra(parcela, params):
            st.warning("Datas ou critérios mudaram depois que estes períodos foram editados. Confira-os ou use "
                       "'Gerar de novo pelas regras'.")
        if registro["editado"] and registro["data_calculo"] != params.data_calculo:
            st.caption(f"Os períodos que iam até {formatar_data(registro['data_calculo'])} (data do cálculo anterior) "
                       f"serão estendidos até {formatar_data(params.data_calculo)}.")
        if st.button("Gerar de novo pelas regras (descarta as edições)", key=f"regerar_{_id_curto(chave)}_{registro['versao']}"):
            s["periodos"][chave] = _registro_periodos(parcela, params, registro)
            s["periodos_editados"].pop(chave, None)
            st.rerun()
        colunas = formulario.COLUNAS_PERIODOS + formulario.COLUNAS_PERIODOS_OCULTAS
        df = pd.DataFrame([{c: l.get(c) for c in colunas} for l in _periodos_texto(registro["linhas"])], columns=colunas)
        for coluna in colunas:
            if coluna != "selic_sobre_juros":
                df[coluna] = df[coluna].fillna("").astype(str).replace({"None": "", "nan": ""})
        df["selic_sobre_juros"] = df["selic_sobre_juros"].map(lambda v: formulario._bool(v))
        editado = st.data_editor(
            df,
            key=f"ed_periodos_{_id_curto(chave)}_{registro['versao']}",
            num_rows="dynamic",
            width="stretch",
            column_config={
                "de": st.column_config.TextColumn("De (dd/mm/aaaa)"),
                "ate": st.column_config.TextColumn("Até (inclusive)"),
                "indice_correcao": st.column_config.SelectboxColumn("Correção", options=OPCOES_INDICE_CORRECAO_PERIODO),
                "tipo_juros": st.column_config.SelectboxColumn(
                    "Juros", options=["nenhum", "simples", "composto", "taxa_legal", "indice", "embutido"],
                    help="embutido = SELIC (correção e juros juntos, sem cumular)"),
                "taxa_juros_am": st.column_config.TextColumn("% a.m."),
                "indice_juros": st.column_config.SelectboxColumn("Índice de juros", options=OPCOES_INDICE_JUROS),
                "capitalizacao_correcao": st.column_config.SelectboxColumn("Acumulação", options=["composta", "simples"]),
                "capitalizacao_juros": st.column_config.SelectboxColumn(
                    "Capitalização dos juros", options=["", "simples", "composta"], help="vazio = padrão do índice"),
                "acrescimo_final_pct": st.column_config.TextColumn("Acréscimo final %", help="ex.: 1 (tributário: 1% no mês do cálculo)"),
                "selic_sobre_juros": st.column_config.CheckboxColumn("SELIC sobre juros vencidos"),
                "descricao": None, "base_legal": None, "gerado": None,
            },
        )
        s["periodos_editados"][chave] = _registros(editado)


def periodos_da_parcela(parcela: Parcela, params: Parametros) -> list:
    s = st.session_state
    chave = chave_parcela(parcela)
    registro = s["periodos"].get(chave)
    if registro is None:
        raise formulario.ErroFormulario([f"{parcela.descricao}: os períodos ainda não foram gerados"])
    if registro["erro"] and not registro["editado"]:
        raise formulario.ErroFormulario(
            [f"{parcela.descricao}: períodos não gerados pelas regras ({registro['erro']}); complete as datas ou preencha os períodos"])
    linhas = s["periodos_editados"].get(chave, registro["linhas"])
    linhas = formulario.estender_ate(linhas, registro["data_calculo"], params.data_calculo)
    try:
        return formulario.periodos_de_tabela(linhas)
    except formulario.ErroFormulario as erro:
        raise formulario.ErroFormulario([f"{parcela.descricao}, {e}" for e in erro.erros]) from None


def montar_entrada() -> tuple[list[Parcela], Parametros]:
    """Parcelas e parâmetros do formulário atual (ErroFormulario com todos os problemas encontrados)."""
    s = st.session_state
    erros = []
    try:
        parcelas = formulario.parcelas_de_tabela(s.get("parcelas_editadas", s["parcelas_base"]))
    except formulario.ErroFormulario as erro:
        erros.extend(erro.erros)
        parcelas = []
    try:
        params = montar_parametros()
    except formulario.ErroFormulario as erro:
        raise formulario.ErroFormulario(erros + erro.erros) from None
    if erros:
        raise formulario.ErroFormulario(erros)
    if not parcelas:
        raise formulario.ErroFormulario(["informe ao menos uma parcela"])
    if s["f_editar_periodos"]:
        todas = parcelas_com_honorarios(parcelas, params)
        for parcela in todas:
            try:
                parcela.periodos = periodos_da_parcela(parcela, params)
            except formulario.ErroFormulario as erro:
                erros.extend(erro.erros)
        if erros:
            raise formulario.ErroFormulario(erros)
        # os honorários fixos viram uma parcela com os próprios períodos (o motor só os cria sem períodos)
        return todas, replace(params, honorarios_fixos=None)
    return parcelas, params


def carregar_entrada(entrada_json: str, calculo_id: int | None, nova_data: date | None = None) -> None:
    """Coloca no formulário um cálculo salvo (abrir) ou uma cópia ainda não gravada (duplicar)."""
    processo, parcelas, params = serializacao.entrada_de_json(entrada_json)
    limpar_calculo()
    s = st.session_state
    s["f_numero_cnj"], s["f_vara"] = processo.get("numero_cnj", ""), processo.get("vara", "")
    s["f_autor"], s["f_reu"], s["f_tipo"] = processo.get("autor", ""), processo.get("reu", ""), processo.get("tipo", "cumprimento")
    s["f_area"] = params.area
    for campo, widget in MAPA_PARAMETROS.items():
        valor = getattr(params, campo)
        s[widget] = _texto_data(valor) if campo in CAMPOS_DATA else valor
    if nova_data is not None:
        s["f_data_calculo"] = formatar_data(nova_data)
    s["f_multa_contratual"] = format(params.multa_contratual_pct, "f") if params.multa_contratual_pct is not None else ""
    if params.honorarios_fixos:
        s["f_honorarios_modo"] = "fixo"
        s["f_honorarios_fixo_valor"] = format(params.honorarios_fixos[0], "f")
        s["f_honorarios_fixo_data"] = formatar_data(params.honorarios_fixos[1])
    elif params.honorarios_pct is not None:
        s["f_honorarios_modo"], s["f_honorarios_pct"] = "percentual", format(params.honorarios_pct, "f")
    s["f_termo_inicial_juros"] = params.termo_inicial_juros or "(padrão)"
    s["f_editar_periodos"] = not params.aplicar_cortes_legais
    s["parcelas_base"] = [
        {"descricao": p.descricao, "valor": format(p.valor_original, "f").replace(".", ","), "data_base": _texto_data(p.data_base),
         "natureza": p.natureza, "data_evento": _texto_data(p.data_evento)}
        for p in parcelas
    ] or list(PARCELAS_VAZIAS)
    s["abatimentos_base"] = [
        {"data": _texto_data(a.data), "valor": format(a.valor, "f").replace(".", ","), "descricao": a.descricao}
        for a in params.abatimentos
    ] or list(ABATIMENTOS_VAZIOS)
    # períodos salvos valem como editados (critérios do título): não são refeitos sozinhos; os que iam até a
    # data do cálculo salva acompanham a nova data (duplicar)
    s["periodos"] = {
        chave_parcela(p): {"linhas": _periodos_texto(formulario.periodos_para_tabela(p.periodos)),
                           "assinatura": assinatura_regra(p, params), "editado": True, "data_calculo": params.data_calculo,
                           "erro": None, "versao": 0}
        for p in parcelas if p.periodos
    }
    s["calculo_id"] = calculo_id


# ---------------------------------------------------------------------------
# Tela: Novo cálculo
# ---------------------------------------------------------------------------


def _disponibilidade_indices() -> str:
    try:
        repo = RepositorioSQLite()
        partes = []
        for codigo in INDICES_DISPONIBILIDADE:
            ultimo = repo.ultimo_mes(codigo)
            if ultimo:
                partes.append(f"{NOMES_INDICE.get(codigo, codigo)} {ultimo:%m/%Y}")
        return " · ".join(partes)
    except Exception:  # noqa: BLE001 - informação auxiliar; o cálculo informa o que faltar
        return ""


def tela_novo_calculo() -> None:
    s = st.session_state
    cabecalho, botao_limpar = st.columns([4, 1])
    cabecalho.header("Novo cálculo")
    botao_limpar.button("Limpar (novo cálculo)", key="botao_limpar", on_click=limpar_calculo,
                        help="Formulário em branco; cálculos salvos não são alterados.")
    if s.get("aviso_carga"):
        st.info(s["aviso_carga"])
    if s.get("calculo_id"):
        st.caption(f"Editando o cálculo salvo nº {s['calculo_id']}: 'Salvar' atualiza este registro; "
                   "'Salvar como novo' grava outro.")

    st.subheader("1. Processo")
    c1, c2 = st.columns(2)
    c1.text_input("Número CNJ", key="f_numero_cnj", placeholder="0000000-00.0000.8.09.0000")
    c2.text_input("Vara / tribunal", key="f_vara")
    c1.text_input("Autor", key="f_autor")
    c2.text_input("Réu", key="f_reu")
    c1.selectbox("Área", list(AREAS), format_func=AREAS.get, key="f_area")
    c2.selectbox("Tipo", TIPOS_PROCESSO, key="f_tipo")

    st.subheader("2. Perfil")
    colunas = st.columns(len(perfis.PERFIS))
    for coluna, (chave, perfil) in zip(colunas, perfis.PERFIS.items()):
        coluna.button(perfil.nome, key=f"perfil_{chave}", on_click=aplicar_perfil, args=(chave,), help=perfil.descricao,
                      width="stretch")
    if s.get("perfil_escolhido") in perfis.PERFIS:
        perfil = perfis.PERFIS[s["perfil_escolhido"]]
        exigidas = DATAS_EXIGIDAS.get(perfil.area, [])
        st.info(_md(f"**{perfil.nome}** — {perfil.descricao}"
                    + (f"\n\nDatas exigidas: {', '.join(exigidas)}." if exigidas else "")
                    + "\n\nOs campos abaixo foram preenchidos pelo perfil e continuam editáveis."))

    st.subheader("3. Datas")
    d1, d2, d3 = st.columns(3)
    d1.text_input("Data do cálculo", key="f_data_calculo", placeholder="dd/mm/aaaa")
    d2.text_input("Citação", key="f_data_citacao", placeholder="dd/mm/aaaa")
    d3.text_input("Ajuizamento", key="f_data_ajuizamento", placeholder="dd/mm/aaaa")
    d1.text_input("Trânsito em julgado", key="f_data_transito", placeholder="dd/mm/aaaa")
    d2.text_input("Intimação para pagamento (art. 523)", key="f_data_intimacao", placeholder="dd/mm/aaaa")
    d3.text_input("Fim do prazo do art. 523 (opcional)", key="f_data_decurso_523", placeholder="dd/mm/aaaa",
                  help="Se vazio, estimado como intimação + 15 dias úteis (feriados nacionais e recesso).")
    exigidas = DATAS_EXIGIDAS.get(s["f_area"], [])
    disponiveis = _disponibilidade_indices()
    st.caption(
        (f"Área {AREAS[s['f_area']]}: exige {', '.join(exigidas)}. " if exigidas else "")
        + "O cálculo usa os índices até o mês anterior à data do cálculo."
        + (f" Último mês no banco: {disponiveis}." if disponiveis else "")
    )

    st.subheader("4. Parcelas")
    arquivo = st.file_uploader("Importar Excel/CSV (colunas descricao, valor, data_base, natureza)", type=["csv", "xlsx"],
                               key=f"upload_parcelas_{s['parcelas_versao']}")
    if arquivo is not None:
        try:
            s["parcelas_base"] = formulario.ler_planilha_parcelas(arquivo.name, arquivo.getvalue()) or list(PARCELAS_VAZIAS)
            s.pop("parcelas_editadas", None)
            s["parcelas_versao"] += 1
            st.rerun()
        except Exception as erro:  # noqa: BLE001 - arquivo do usuário: mostrar o motivo sem derrubar a tela
            st.error(f"Não foi possível ler {arquivo.name}: {erro}")
    df_parcelas = pd.DataFrame([{k: _texto_data(v) if k in ("data_base", "data_evento") else v for k, v in r.items()}
                                for r in s["parcelas_base"]], columns=formulario.COLUNAS_PARCELAS)
    for coluna in ("valor", "data_base", "data_evento", "descricao"):
        df_parcelas[coluna] = df_parcelas[coluna].fillna("").astype(str).replace({"None": "", "nan": ""})
    df_parcelas["natureza"] = df_parcelas["natureza"].fillna("principal").replace({"": "principal"})
    editadas = st.data_editor(
        df_parcelas,
        key=f"ed_parcelas_{s['parcelas_versao']}",
        num_rows="dynamic",
        width="stretch",
        column_config={
            "descricao": st.column_config.TextColumn("Descrição"),
            "valor": st.column_config.TextColumn("Valor (R$)", help="Formato brasileiro: 10.000,00 (ponto de milhar, vírgula nos centavos)"),
            "data_base": st.column_config.TextColumn("Data-base (dd/mm/aaaa)", help="Vencimento, evento danoso ou arbitramento"),
            "natureza": st.column_config.SelectboxColumn("Natureza", options=list(NATUREZAS)),
            "data_evento": st.column_config.TextColumn("Evento (dano moral, dd/mm/aaaa)"),
        },
    )
    s["parcelas_editadas"] = _registros(editadas)

    st.subheader("5. Correção e juros")
    k1, k2 = st.columns(2)
    k1.checkbox("Pró-rata dia", key="f_pro_rata")
    k2.checkbox("Editar períodos (critérios do título; desliga os cortes legais automáticos)", key="f_editar_periodos")
    with st.expander("Critérios avançados (decisões jurídicas)"):
        a1, a2, a3 = st.columns(3)
        a1.selectbox("Responsabilidade (cível)", ["contratual", "extracontratual"], key="f_responsabilidade")
        a2.selectbox("Termo inicial dos juros (cível)", list(NOMES_TERMO), format_func=NOMES_TERMO.get, key="f_termo_inicial_juros")
        opcoes, nomes = opcoes_indice_civel()
        a3.selectbox("Índice cível até 29/08/2024", opcoes, format_func=lambda c: nomes.get(c, c), key="f_indice_civel")
        a1.selectbox("Índice desde 30/08/2024", ["IPCA", "IPCA_15"], format_func=NOMES_INDICE.get, key="f_indice_pos")
        a2.selectbox("Juros cíveis até 29/08/2024", list(NOMES_JUROS_PRE), format_func=NOMES_JUROS_PRE.get, key="f_juros_pre")
        a3.selectbox("Acumulação da SELIC", list(NOMES_CAP_SELIC), format_func=NOMES_CAP_SELIC.get, key="f_cap_selic")
        a1.selectbox("Acréscimo da SELIC embutida tratado", list(NOMES_SELIC_COMO), format_func=NOMES_SELIC_COMO.get, key="f_selic_como")
        a2.checkbox("Trabalhista: fase pré-judicial termina em 29/08/2024", key="f_trab_pre_corte")
        a3.selectbox("Fazenda: regime após ago/2025", list(NOMES_FAZENDA), format_func=NOMES_FAZENDA.get, key="f_fazenda_regime")
        a1.selectbox("Fazenda: índice até 11/2021", ["IPCA_E", "INPC"], format_func=NOMES_INDICE.get, key="f_indice_fazenda")
        a2.checkbox("Deflação não reduz o principal abaixo do nominal", key="f_preservar_nominal")
        a3.checkbox("Mês sem índice: usar o último mês como projeção", key="f_permitir_projecao")
        a1.selectbox("Arredondamento", ["exato", "cjf"], key="f_arredondamento",
                     format_func={"exato": "Exato (10 casas)", "cjf": "Manual CJF (truncamentos)"}.get)

    st.subheader("6. Acessórios")
    x1, x2, x3 = st.columns(3)
    x1.checkbox("Multa de 10% do art. 523", key="f_multa_523")
    x2.checkbox("Honorários de 10% do cumprimento", key="f_honorarios_cumprimento")
    x3.selectbox("Base da multa/honorários do 523", list(NOMES_BASE_523), format_func=NOMES_BASE_523.get, key="f_base_523")
    x3.checkbox("Custas na base da multa/honorários do 523 (STJ, REsp 1.757.033)", key="f_custas_523")
    x1.text_input("Multa contratual (% sobre principal corrigido + juros)", key="f_multa_contratual")
    x2.radio("Honorários de sucumbência", ["nenhum", "percentual", "fixo"], key="f_honorarios_modo", horizontal=True)
    if s["f_honorarios_modo"] == "percentual":
        x3.text_input("Honorários (% sobre principal corrigido + juros + multa contratual)", key="f_honorarios_pct")
    elif s["f_honorarios_modo"] == "fixo":
        x3.text_input("Honorários fixos (R$)", key="f_honorarios_fixo_valor")
        x3.text_input("Data da fixação", key="f_honorarios_fixo_data", placeholder="dd/mm/aaaa")
    x1.checkbox("Houve impugnação (Fazenda, Tema 1.190)", key="f_impugnacao")
    st.markdown("**Abatimentos**")
    df_ab = pd.DataFrame([{k: _texto_data(v) if k == "data" else v for k, v in r.items()} for r in s["abatimentos_base"]],
                         columns=formulario.COLUNAS_ABATIMENTOS)
    for coluna in ("data", "valor", "descricao"):
        df_ab[coluna] = df_ab[coluna].fillna("").astype(str).replace({"None": "", "nan": ""})
    ab = st.data_editor(
        df_ab,
        key=f"ed_abatimentos_{s['abatimentos_versao']}",
        num_rows="dynamic",
        width="stretch",
        column_config={
            "data": st.column_config.TextColumn("Data (dd/mm/aaaa)"),
            "valor": st.column_config.TextColumn("Valor (R$)", help="Formato brasileiro: 5.000,00"),
            "descricao": st.column_config.TextColumn("Descrição"),
        },
    )
    s["abatimentos_editados"] = _registros(ab)
    st.selectbox("Imputação dos pagamentos", list(IMPUTACOES), key="f_imputacao", format_func=NOMES_IMPUTACAO.get)

    if s["f_editar_periodos"]:
        st.subheader("Períodos de cada parcela")
        try:
            parcelas = formulario.parcelas_de_tabela(s["parcelas_editadas"])
            params = montar_parametros()
        except formulario.ErroFormulario as erro:
            st.warning("Corrija o formulário para gerar os períodos:\n- " + "\n- ".join(erro.erros))
        else:
            editar_periodos(parcelas, params)

    entrada, erro_entrada = None, None
    try:
        entrada = montar_entrada()
    except formulario.ErroFormulario as erro:
        erro_entrada = erro
    assinatura_atual = serializacao.entrada_para_json({}, *entrada) if entrada else None

    st.subheader("7. Calcular")
    clicou = st.button("Calcular", type="primary", key="botao_calcular")
    if clicou or s.pop("calcular_agora", False):
        executar_calculo(entrada, erro_entrada, assinatura_atual)
    elif s.get("indice_faltante"):
        oferecer_saidas_indice_ausente()
    memoria = s.get("memoria")
    if memoria is not None:
        desatualizado = assinatura_atual != s.get("assinatura_calculo")
        if desatualizado:
            st.warning("Os dados mudaram depois do último cálculo: o resultado abaixo é o anterior. Clique em Calcular "
                       "para atualizá-lo (salvar e exportar ficam bloqueados até lá).")
        mostrar_resultado(memoria)

        st.subheader("8. Salvar e exportar")
        e1, e2, e3 = st.columns(3)
        if e1.button("Salvar cálculo", key="botao_salvar", disabled=desatualizado):
            salvar_calculo(s.get("calculo_id"))
        if s.get("calculo_id") and e1.button("Salvar como novo", key="botao_salvar_novo", disabled=desatualizado):
            salvar_calculo(None)
        botoes_exportacao(memoria, e2, e3, desatualizado)


def executar_calculo(entrada, erro_entrada, assinatura_atual) -> None:
    s = st.session_state
    s["memoria"], s["indice_faltante"] = None, None  # resultado antigo nunca fica na tela depois de um erro
    if erro_entrada is not None:
        st.error("Corrija o formulário:\n- " + "\n- ".join(erro_entrada.erros))
        return
    parcelas, params = entrada
    try:
        memoria = calcular(parcelas, params, RepositorioSQLite())
    except IndiceAusente as erro:
        s["indice_faltante"] = {"indice": erro.indice, "mes": erro.mes, "texto": str(erro), "data_calculo": params.data_calculo,
                                "motivo": erro.motivo, "explicacao": mod_pedido._texto_indice_ausente(erro, params)}
        oferecer_saidas_indice_ausente()
        return
    except IndiceDesconhecido as erro:
        st.error(f"Índice não cadastrado no banco local: {erro}. Confira a tela Índices.")
        return
    except (ErroRegra, ParametroInvalido) as erro:
        st.error(str(erro))
        return
    except Exception as erro:  # noqa: BLE001 - nenhum traceback na tela; detalhe no log
        LOG_ERROS.parent.mkdir(parents=True, exist_ok=True)
        with LOG_ERROS.open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now().isoformat(timespec='seconds')} {traceback.format_exc()}\n")
        st.error(f"Não foi possível calcular ({type(erro).__name__}: {erro}). Confira os dados informados; "
                 f"detalhes técnicos em {LOG_ERROS.name}.")
        return
    s["memoria"] = memoria
    s["calculo_entrada"] = (parcelas, params)
    s["assinatura_calculo"] = assinatura_atual


def _usar_projecao() -> None:
    st.session_state["f_permitir_projecao"] = True
    st.session_state["calcular_agora"] = True


def _usar_data(texto: str) -> None:
    st.session_state["f_data_calculo"] = texto
    st.session_state["calcular_agora"] = True


def oferecer_saidas_indice_ausente() -> None:
    """IndiceAusente: o cálculo para (regra do projeto); oferece as duas saídas legítimas num clique."""
    falta = st.session_state["indice_faltante"]
    if not str(falta.get("motivo", "último mês gravado")).startswith("último mês gravado"):
        # antes do início da série/da tabela, lacuna ou série encerrada: projeção e outra data não resolvem
        st.error(falta.get("explicacao") or falta["texto"])
        return
    st.error(f"{falta['texto']}. Nenhum índice é estimado sem a sua autorização.")
    mes = falta["mes"]
    nova = date(mes.year, mes.month, 1)
    b1, b2 = st.columns(2)
    if falta["data_calculo"] > nova >= somar_meses(primeiro_dia(falta["data_calculo"]), -2):
        b1.button(f"Calcular até {formatar_data(nova)} (último índice divulgado)", key="botao_usar_data",
                  on_click=_usar_data, args=(formatar_data(nova),))
    b2.button(f"Calcular com projeção ({falta['indice']} de {mes:%m/%Y} = último mês disponível, marcado na memória)",
              key="botao_usar_projecao", on_click=_usar_projecao)


def mostrar_resultado(memoria) -> None:
    tabelas = formulario.tabelas_da_memoria(memoria)
    st.markdown("**Resumo**")
    for inicio in (0, 4):
        colunas = st.columns(4)
        for coluna, (nome, valor) in zip(colunas, tabelas.resumo[inicio:inicio + 4]):
            coluna.metric(nome, valor)
    for aviso in memoria.avisos:
        st.warning(_md(aviso))
    projetados = [(f.codigo, f.meses_projecao) for f in memoria.fontes if f.meses_projecao]
    if projetados:
        divulgados = auto.projecoes_ja_divulgadas(RepositorioSQLite(), projetados)
        if divulgados:
            st.error("O índice oficial de " + ", ".join(divulgados) + " já foi divulgado e gravado. Este resultado usou "
                     "projeção: clique em Calcular de novo para usar o índice oficial.")
    with st.expander("Critérios adotados"):
        for linha in memoria.criterios:
            st.text(linha)
        for detalhe in memoria.acessorios.detalhes:
            st.text(detalhe)
    st.markdown("**Memória analítica**")
    for parcela in tabelas.parcelas:
        with st.expander(_md(f"{parcela['titulo']} → {parcela['total']}")):
            st.dataframe(pd.DataFrame(parcela["linhas"]).drop(columns=["Período/índice"], errors="ignore"), width="stretch", hide_index=True)
    if tabelas.abatimentos:
        st.markdown("**Abatimentos**")
        st.dataframe(pd.DataFrame(tabelas.abatimentos), width="stretch", hide_index=True)
    with st.expander("Fontes dos índices"):
        st.dataframe(pd.DataFrame(tabelas.fontes), width="stretch", hide_index=True)


def salvar_calculo(id_existente: int | None) -> None:
    """Grava a entrada do último cálculo com o processo atual (o processo não altera o resultado)."""
    s = st.session_state
    memoria = s["memoria"]
    parcelas, params = s["calculo_entrada"]
    entrada_json = serializacao.entrada_para_json(processo_atual(), parcelas, params)
    conn = persistencia.conectar()
    try:
        novo_id = persistencia.salvar(
            conn, processo_atual(), memoria.parametros.area, memoria.parametros.data_calculo.isoformat(),
            format(memoria.total, "f"), entrada_json, serializacao.memoria_para_json(memoria), id_existente,
        )
    finally:
        conn.close()
    s["calculo_id"] = novo_id
    s.pop("aviso_carga", None)
    if id_existente and novo_id != id_existente:
        st.success(f"O cálculo nº {id_existente} não existe mais; gravado como novo (nº {novo_id}).")
    else:
        st.success(f"Cálculo salvo (nº {novo_id}).")


def botoes_exportacao(memoria, coluna_pdf, coluna_xlsx, desatualizado: bool) -> None:
    """PDF, Excel e a entrada em JSON (etapa 4); arquivos gerados só no clique."""
    s = st.session_state
    config = configuracoes.carregar()
    processo = processo_atual()
    nome = relatorio.nome_arquivo(memoria, processo, "pdf")
    coluna_pdf.download_button("PDF", data=lambda: relatorio.gerar_pdf(memoria, config, processo), file_name=nome,
                               mime="application/pdf", key="baixar_pdf", disabled=desatualizado)
    coluna_xlsx.download_button("Excel", data=lambda: relatorio.gerar_xlsx(memoria, config, processo),
                                file_name=nome.replace(".pdf", ".xlsx"), key="baixar_xlsx", disabled=desatualizado,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if s.get("calculo_entrada") and not MODO_PLUGIN:
        entrada_json = serializacao.entrada_para_json(processo, *s["calculo_entrada"])
        coluna_pdf.download_button("Entrada (JSON)", data=entrada_json, file_name=nome.replace(".pdf", "_entrada.json"),
                                   mime="application/json", key="baixar_json", disabled=desatualizado,
                                   help="Parcelas e parâmetros do cálculo (base para um caso-ouro em tests/casos_ouro/)")
    if coluna_xlsx.button("Gravar PDF e Excel na pasta de saída", key="botao_gravar_saida", disabled=desatualizado):
        try:
            pasta = Path(config.pasta_saida)
            gravados = relatorio.gravar(memoria, pasta, config, processo)  # nunca sobrescreve um relatório anterior
            st.success(f"Gravados em {pasta}: {gravados['pdf'].name} e {gravados['excel'].name}")
        except OSError as erro:
            st.error(f"Não foi possível gravar na pasta de saída ({config.pasta_saida}): {erro}")


# ---------------------------------------------------------------------------
# Tela: Cálculos salvos
# ---------------------------------------------------------------------------


def aviso_do_salvo(resultado_json: str) -> str:
    """Cálculo salvo que usou projeção e cujo índice oficial já saiu: precisa ser refeito."""
    try:
        fontes = json.loads(resultado_json).get("fontes", [])
        projetados = [(f["codigo"], [date.fromisoformat(m) for m in f.get("meses_projecao") or []]) for f in fontes]
        projetados = [(c, ms) for c, ms in projetados if ms]
        if not projetados:
            return ""
        divulgados = auto.projecoes_ja_divulgadas(RepositorioSQLite(), projetados)
    except Exception:  # noqa: BLE001 - registro antigo ou ilegível não impede a lista
        return ""
    return (f"usou projeção; o índice oficial já saiu ({', '.join(divulgados)}): abra o cálculo e clique em Calcular "
            "para refazê-lo na mesma data (ou Duplicar para atualizar até hoje)") if divulgados else ""


def tela_calculos_salvos() -> None:
    s = st.session_state
    st.header("Cálculos salvos")
    conn = persistencia.conectar()
    try:
        salvos = persistencia.listar(conn)
        if not salvos:
            st.info("Nenhum cálculo salvo ainda.")
            return
        tabela = pd.DataFrame(
            [
                {"Nº": c.id, "Processo": c.numero_cnj, "Autor": c.autor, "Réu": c.reu, "Área": AREAS.get(c.area, c.area),
                 "Data do cálculo": formatar_data(date.fromisoformat(c.data_calculo)), "Total": formatar_brl(para_decimal(c.total)),
                 "Atualizado em": c.atualizado_em.replace("T", " "), "Aviso": aviso_do_salvo(c.resultado_json)}
                for c in salvos
            ]
        )
        st.dataframe(tabela, width="stretch", hide_index=True)
        escolhido = st.selectbox("Cálculo", [c.id for c in salvos],
                                 format_func=lambda i: next(f"nº {c.id} — {c.numero_cnj or 'sem número'} — {c.autor}" for c in salvos if c.id == i),
                                 key="salvo_escolhido")
        b1, b2, b3 = st.columns(3)
        if b1.button("Abrir", key="botao_abrir"):
            c = persistencia.abrir(conn, escolhido)
            carregar_entrada(c.entrada_json, c.id)
            s["aviso_carga"] = f"Cálculo nº {c.id} aberto. Clique em Calcular para ver o resultado."
            s["ir_para"] = "Novo cálculo"
            st.rerun()
        if b2.button("Duplicar (atualizar até hoje)", key="botao_duplicar",
                     help="Abre uma cópia com a data do cálculo de hoje; a cópia só é gravada quando você salvar."):
            c = persistencia.abrir(conn, escolhido)
            carregar_entrada(c.entrada_json, None, nova_data=date.today())
            s["aviso_carga"] = (f"Cópia do cálculo nº {c.id} com data do cálculo {formatar_data(date.today())} "
                                "(ainda não gravada). Confira e clique em Calcular; 'Salvar' grava um cálculo novo.")
            s["ir_para"] = "Novo cálculo"
            st.rerun()
        confirmar = b3.checkbox("Confirmo a exclusão", key=f"confirmar_exclusao_{s['exclusoes']}")
        if b3.button("Excluir", key="botao_excluir", disabled=not confirmar):
            persistencia.excluir(conn, escolhido)
            s["exclusoes"] += 1  # a confirmação vale para uma exclusão só
            if s.get("calculo_id") == escolhido:
                s["calculo_id"] = None  # o formulário continua, mas "Salvar" passa a gravar um cálculo novo
            s["aviso_exclusao"] = f"Cálculo nº {escolhido} excluído."
            st.rerun()
        if s.get("aviso_exclusao"):
            st.success(s.pop("aviso_exclusao"))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tela: Índices
# ---------------------------------------------------------------------------


def _meses_novos(novos: dict) -> str:
    return auto.resumo_novos(novos)


def mensagens_da_verificacao(v) -> list[tuple[bool, str]]:
    """Resultado de uma conferência em frases: (é erro?, texto)."""
    if v is None:
        return [(True, "Já há uma conferência em andamento; aguarde alguns instantes e veja o resultado abaixo.")]
    if v.situacao == "sem_internet":
        return [(True, "Sem acesso à internet; nada foi alterado. Verifique a conexão e tente de novo.")]
    if v.situacao == "bcb_indisponivel":
        return [(True, "O site do Banco Central não respondeu (fora do ar ou lento); nada foi alterado. "
                       "A calculadora tenta de novo sozinha em 30 minutos.")]
    saida = []
    if v.novos:
        saida.append((False, "Novos meses gravados: " + _meses_novos(v.novos)))
    else:
        saida.append((False, "Conferência concluída: nenhum mês novo publicado desde a última vez."))
    saida += [(True, e) for e in v.erros]
    if v.atrasados:
        saida.append((True, f"Atrasados ({auto.LIMITE_ATRASO_MESES}+ meses): {', '.join(auto.nome(c) for c in v.atrasados)}"))
    return saida


NOMES_SITUACAO = {"ok": "concluída", "sem_internet": "sem internet", "bcb_indisponivel": "Banco Central fora do ar/lento",
                  "com_erros": "com problemas", "falhou": "falhou", "em_andamento": "em andamento",
                  "interrompida": "interrompida (calculadora fechada)"}


def mostrar_atualizacao_automatica(conn) -> None:
    st.subheader("Atualização automática")
    if auto.desligada_por_ambiente():
        st.caption("Desligada neste computador (variável CALCULADORA_ATUALIZACAO_AUTOMATICA=0).")
    else:
        st.caption(
            "Ligada: enquanto a calculadora estiver aberta, ela confere os índices ao abrir e a cada 30 minutos. "
            "Só consulta o Banco Central quando um mês novo já deveria ter sido divulgado (IPCA e INPC por volta do "
            "dia 12; SELIC, TR, taxa legal e IPCA-15 no início do mês); se ainda não saiu, tenta de novo a cada 6 horas. "
            "Sem internet ou com o site do Banco Central fora do ar, tenta de novo em 30 minutos. Tabelas de tribunal "
            "(TJSP, JF, JF previdenciária) não vêm do Banco Central: são importadas por arquivo (ou chegam com o plugin)."
        )
    historico = auto.historico(conn, 10)
    if not historico:
        st.caption("Nenhuma conferência registrada ainda.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {"Quando": f"{v.inicio:%d/%m/%Y %H:%M}", "Origem": "botão" if v.origem == "manual" else "automática",
                 "Resultado": NOMES_SITUACAO.get(v.situacao, v.situacao), "Meses novos": _meses_novos(v.novos) or "—",
                 "Observação": "; ".join(v.erros + ([f"atrasados: {', '.join(v.atrasados)}"] if v.atrasados else []))}
                for v in historico
            ]
        ),
        width="stretch",
        hide_index=True,
    )


@st.fragment(run_every=timedelta(seconds=3))
def resultado_da_conferencia_pedida() -> None:
    """Depois do botão: mostra "conferindo…" e, ao terminar, o resultado (atualiza sozinho a cada 3 s)."""
    pedida = st.session_state.get("conferencia_pedida_em")
    if not pedida or datetime.now() - pedida > timedelta(minutes=15):
        return
    conn = conectar()
    try:
        recentes = [v for v in auto.historico(conn, 5) if v.origem == "manual" and v.inicio >= pedida - timedelta(seconds=5)]
    finally:
        conn.close()
    concluida = next((v for v in recentes if v.situacao not in ("em_andamento",)), None)
    if concluida is None:
        st.info("Conferindo no Banco Central… o resultado aparece aqui em instantes.")
        return
    for erro, texto in mensagens_da_verificacao(concluida):
        (st.error if erro else st.success)(texto)


def tela_indices() -> None:
    s = st.session_state
    st.header("Índices")
    for erro, texto in s.pop("resultado_atualizacao", []):
        (st.error if erro else st.success)(texto)
    conn = conectar()
    try:
        linhas = coletor_bcb.status(conn)
        st.dataframe(
            pd.DataFrame(
                [
                    {"Índice": l.codigo, "Nome": l.nome, "Último mês": l.ultimo_mes.strftime("%m/%Y") if l.ultimo_mes else "—",
                     "Faltam (até o mês anterior)": l.meses_faltando, "Fonte": l.fonte, "Observação": l.observacao}
                    for l in linhas
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(f"Banco: {caminho_db()}")
        if st.button("Atualizar agora", key="botao_atualizar",
                     help="Confere já no Banco Central, mesmo que nenhum mês novo esteja previsto."):
            atualizador = obter_atualizador()
            if atualizador is not None:  # em segundo plano: o resultado aparece mesmo se você trocar de tela
                s["conferencia_pedida_em"] = datetime.now()
                atualizador.solicitar_agora()
            else:
                with st.spinner("Conferindo no Banco Central..."):
                    v = auto.verificar_e_atualizar(origem="manual", forcar=True)
                s["resultado_atualizacao"] = mensagens_da_verificacao(v)
                st.rerun()
        resultado_da_conferencia_pedida()
        mostrar_atualizacao_automatica(conn)

        st.subheader("Importar tabela de tribunal (CSV/XLSX)")
        with st.form("form_importar"):
            arquivo = st.file_uploader("Arquivo (mes_referencia,taxa_pct ou mes_referencia,fator)", type=["csv", "xlsx"])
            i1, i2 = st.columns(2)
            indice = i1.text_input("Código do índice", value="TJGO")
            convencao = i2.selectbox("Convenção do fator", ["fator_fim_do_mes", "fator_inicio_do_mes", "coeficiente_multiplicador"])
            nome = i1.text_input("Nome (opcional)")
            fonte = i2.text_input("Fonte (ex.: TJGO Corregedoria)")
            fora = i1.checkbox("Aceitar meses fora da faixa (tabela oficial)")
            substituir = i2.checkbox("Substituir meses já gravados / tabela composta")
            enviar = st.form_submit_button("Importar")
        if enviar:
            importar_tabela(conn, arquivo, indice, nome, fonte, convencao, fora, substituir)

        st.subheader("Log de alertas")
        log = coletor_bcb.CAMINHO_ALERTAS
        texto = log.read_text(encoding="utf-8") if log.exists() else ""
        st.code("\n".join(texto.splitlines()[-50:]) or "(vazio)", language=None)
    finally:
        conn.close()


def importar_tabela(conn, arquivo, indice: str, nome: str, fonte: str, convencao: str, fora: bool, substituir: bool) -> None:
    if arquivo is None:
        st.error("Escolha o arquivo a importar.")
        return
    try:
        r, destino = importacao.importar_enviado(conn, arquivo.name, arquivo.getvalue(), indice, nome=nome, fonte=fonte,
                                                 convencao_fator=convencao, substituir=substituir, aceitar_fora_da_faixa=fora)
    except importacao.ErroImportacaoInterface as erro:
        st.error(f"Importação recusada, nada foi gravado: {erro}")
        return
    st.success(f"{r.resumo()} Cópia do arquivo em dados/{destino.name}.")


# ---------------------------------------------------------------------------
# Tela: Configurações
# ---------------------------------------------------------------------------


def tela_configuracoes() -> None:
    st.header("Configurações")
    atual = configuracoes.carregar()
    if atual.logo and Path(atual.logo).exists():
        try:
            st.image(atual.logo, width=160, caption="Logo atual")
        except Exception:  # noqa: BLE001 - logo corrompido não pode travar a tela
            st.warning(f"O logo gravado ({atual.logo}) não pôde ser exibido; envie outro PNG.")
    with st.form("form_config"):
        st.subheader("Cabeçalho do escritório")
        nome = st.text_input("Nome do escritório", value=atual.nome_escritorio)
        oab = st.text_input("OAB", value=atual.oab)
        endereco = st.text_area("Endereço", value=atual.endereco)
        logo = st.file_uploader("Logo (PNG)", type=["png"])
        st.subheader("Relatório")
        ressalva = st.text_area("Texto padrão de ressalva ({data} = data do cálculo)", value=atual.ressalva)
        pasta = st.text_input("Pasta de saída dos relatórios", value=atual.pasta_saida)
        valores_smtp = None
        if not MODO_PLUGIN:  # no plugin os atrasos aparecem no painel e na conversa com o Claude, sem e-mail
            st.subheader("SMTP para alertas de índices atrasados (gravado no .env)")
            smtp = configuracoes.ler_smtp()
            s1, s2 = st.columns(2)
            host = s1.text_input("Servidor SMTP", value=smtp.get("SMTP_HOST", ""))
            porta = s2.text_input("Porta", value=smtp.get("SMTP_PORTA", "587"))
            usuario = s1.text_input("Usuário", value=smtp.get("SMTP_USUARIO", ""))
            senha = s2.text_input("Senha", value=smtp.get("SMTP_SENHA", ""), type="password")
            remetente = s1.text_input("Remetente", value=smtp.get("ALERTA_REMETENTE", ""))
            destinatarios = s2.text_input("Destinatários (separados por vírgula)", value=smtp.get("ALERTA_DESTINATARIOS", ""))
            tls = s1.checkbox("STARTTLS", value=smtp.get("SMTP_TLS", "1") not in ("0", ""))
            ssl = s2.checkbox("SSL (porta 465)", value=smtp.get("SMTP_SSL", "0") in ("1",))
            valores_smtp = {
                "SMTP_HOST": host.strip(), "SMTP_PORTA": porta.strip(), "SMTP_USUARIO": usuario.strip(), "SMTP_SENHA": senha,
                "ALERTA_REMETENTE": remetente.strip(), "ALERTA_DESTINATARIOS": destinatarios.strip(),
                "SMTP_TLS": "1" if tls else "0", "SMTP_SSL": "1" if ssl else "0",
            }
        salvar = st.form_submit_button("Salvar configurações")
    if salvar:
        erros = configuracoes.validar_smtp(valores_smtp) if valores_smtp is not None else []
        if logo is not None:
            try:
                configuracoes.validar_logo(logo.getvalue())
            except ValueError as erro:
                erros.append(str(erro))
        if erros:
            st.error("Nada foi salvo:\n- " + "\n- ".join(erros))
            return
        caminho_logo = str(configuracoes.salvar_logo(logo.getvalue())) if logo is not None else atual.logo
        configuracoes.salvar(configuracoes.Configuracao(nome, oab, endereco, caminho_logo, ressalva, pasta))
        if valores_smtp is not None:
            configuracoes.gravar_smtp(valores_smtp)
        st.success("Configurações salvas.")


# ---------------------------------------------------------------------------


@st.cache_resource(validate=lambda a: a is None or a.vivo(), on_release=lambda a: a.parar() if a else None)
def obter_atualizador():
    """Um único processo de atualização por calculadora aberta (sobrevive às recargas da página)."""
    if auto.desligada_por_ambiente():
        return None
    atualizador = auto.Atualizador()
    atualizador.iniciar()
    return atualizador


@st.fragment(run_every=timedelta(seconds=20))
def painel_indices(atualizador) -> None:
    """Situação dos índices na barra lateral; atualiza sozinha a cada 20 segundos."""
    try:
        if atualizador is not None and not atualizador.vivo():
            atualizador.iniciar()  # a rotina parou por algum motivo: religa
        conn = conectar()
        try:
            info = auto.painel(conn, datetime.now(), rodando=bool(atualizador and atualizador.rodando),
                               ligada=atualizador is not None, erro_rotina=atualizador.ultimo_erro if atualizador else None)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - o painel lateral nunca impede a tela principal de abrir
        st.warning("Não foi possível ler a situação dos índices agora; a calculadora tenta de novo em instantes.")
        return
    caixa = {"ok": st.success, "info": st.info, "aviso": st.warning, "erro": st.error}[info.nivel]
    caixa("**" + _md(info.titulo) + "**" + "".join("\n\n" + _md(d) for d in info.detalhes))


def main() -> None:
    st.set_page_config(page_title="Calculadora Judicial", page_icon="⚖️", layout="wide")
    iniciar_estado()
    preservar_formulario()
    if "ir_para" in st.session_state:  # navegação pedida por um botão na execução anterior
        st.session_state["tela"] = st.session_state.pop("ir_para")
    st.sidebar.title("Calculadora Judicial")
    st.sidebar.radio("Tela", TELAS, key="tela", on_change=ao_trocar_tela)
    with st.sidebar:
        painel_indices(obter_atualizador())
    tela = st.session_state["tela"]
    st.session_state["tela_anterior"] = tela
    if tela == "Novo cálculo":
        tela_novo_calculo()
    elif tela == "Cálculos salvos":
        tela_calculos_salvos()
    elif tela == "Índices":
        tela_indices()
    else:
        tela_configuracoes()


main()
