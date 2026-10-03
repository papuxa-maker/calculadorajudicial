"""Tabelas da memória de cálculo, com os valores já formatados.

A mesma função alimenta a tela (Streamlit), o PDF e o Excel, para que os valores
mostrados sejam exatamente os exportados (SPEC, seção 7, critério de aceite).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from calculo_judicial.datas import formatar_competencia, formatar_data
from calculo_judicial.decimais import arredondar_exibicao, formatar_brl, formatar_decimal_br
from calculo_judicial.indices.modelo import nome_curto
from calculo_judicial.motor.memoria import Memoria

NOMES_IMPUTACAO = {"juros_primeiro": "juros primeiro (CC, art. 354)", "capital_primeiro": "capital primeiro",
                   "proporcional": "proporcional"}
COLUNAS_ANALITICA = [
    "Competência", "Valor base", "Índice %", "Fator acumulado", "Valor corrigido", "Juros %", "Juros acumulados", "Subtotal",
]


@dataclass
class TabelasMemoria:
    resumo: list[tuple[str, str]]
    parcelas: list[dict] = field(default_factory=list)  # {titulo, linhas: [dict], total, ...}
    abatimentos: list[dict] = field(default_factory=list)
    fontes: list[dict] = field(default_factory=list)


def _pct(valor: Decimal | None, casas: int = 4) -> str:
    return "" if valor is None else formatar_decimal_br(valor, casas) + "%"


SIGLAS = {"IGPM": "IGP-M", "JF_PREV": "JF prev.", "JUROS_POUPANCA": "poupança", "TAXA_LEGAL": "taxa legal", "IPC_R": "IPC-r",
          "IPCA_E": "IPCA-E", "IPCA_15": "IPCA-15"}


def sigla(codigo: str | None) -> str:
    """Nome curto do índice para colunas estreitas (``IPCA_E`` → ``IPCA-E``); outros códigos como foram gravados."""
    if not codigo:
        return ""
    return SIGLAS.get(codigo, codigo)


def linha_analitica(l) -> dict:
    indice = nome_curto(l.indice) + (f" ({nome_curto(l.indice_origem)})" if l.indice_origem and l.indice_origem != l.indice else "")
    usado = sigla(l.indice_origem or l.indice) or "sem correção"
    return {
        "Competência": formatar_competencia(l.competencia),
        "Período": l.periodo,
        "Período/índice": f"{l.periodo} · {usado}" if l.periodo else usado,
        "Índice": indice,
        "Valor base": formatar_decimal_br(l.base, 2),
        "Índice %": _pct(l.taxa_indice_pct),
        "Fator acumulado": formatar_decimal_br(l.fator_acumulado, 6),
        "Valor corrigido": formatar_decimal_br(l.corrigido, 2),
        "Juros %": _pct(l.taxa_juros_pct),
        "Juros acumulados": formatar_decimal_br(l.juros_acumulados, 2),
        "Subtotal": formatar_decimal_br(l.subtotal, 2),
        "Obs.": ("projeção; " if l.projecao else "") + l.observacao,
    }


def data_coleta(texto: str) -> str:
    """Data de coleta gravada em ISO (aaaa-mm-dd...) exibida como dd/mm/aaaa."""
    try:
        return formatar_data(date.fromisoformat(texto[:10]))
    except ValueError:
        return texto


def tabelas_da_memoria(memoria: Memoria) -> TabelasMemoria:
    resumo = [(nome, formatar_brl(valor)) for nome, valor in memoria.resumo.linhas()]
    parcelas = []
    for mp in memoria.parcelas:
        parcelas.append(
            {
                "titulo": f"{mp.parcela.descricao} — {formatar_brl(mp.valor_original)} em {formatar_data(mp.parcela.data_base)}",
                "linhas": [linha_analitica(l) for l in mp.linhas],
                "total": formatar_brl(mp.total),
                "principal_corrigido": formatar_brl(mp.principal_corrigido),
                "juros": formatar_brl(mp.juros),
            }
        )
    abatimentos = [
        {
            "Data": formatar_data(a.data), "Descrição": a.descricao, "Saldo antes": formatar_brl(a.saldo_antes),
            "Valor abatido": formatar_brl(a.valor), "Juros": formatar_brl(a.juros_imputados),
            "Capital": formatar_brl(a.capital_imputado), "Imputação": NOMES_IMPUTACAO.get(a.imputacao, a.imputacao.replace("_", " ")),
            "Saldo após": formatar_brl(a.saldo_apos),
        }
        for a in memoria.abatimentos
    ]
    fontes = [
        {
            "Índice": nome_curto(f.codigo), "Nome": f.nome, "Órgão/fonte": f.fonte, "URL": f.url or "", "Data de coleta": data_coleta(f.data_coleta),
            "Meses": f"{formatar_competencia(f.primeiro_mes)} a {formatar_competencia(f.ultimo_mes)}" if f.primeiro_mes else "",
            "Projeção": ", ".join(formatar_competencia(m) for m in f.meses_projecao),
        }
        for f in memoria.fontes
    ]
    return TabelasMemoria(resumo, parcelas, abatimentos, fontes)


def total_exibido(memoria: Memoria) -> Decimal:
    """Total arredondado (2 casas) — o mesmo número da tela, do PDF e do Excel."""
    return arredondar_exibicao(memoria.total)
