"""Tabelas de correção disponíveis e ensaio de importação de tabela de tribunal.

Usado pela conversa com o Claude (comandos ``tabelas`` e ``importar`` da linha de comando) e pela
tela: o advogado de qualquer estado vê o que a calculadora já tem e pode trazer a tabela oficial do
seu tribunal. Antes de gravar, o ensaio importa o arquivo numa cópia do banco em memória e compara as
taxas mensais que saem dele com os índices oficiais do Banco Central — um arquivo lido com a convenção
errada (fator do fim × do início do mês) aparece deslocado de um mês e é apontado antes de entrar no
cálculo. Nenhuma taxa é estimada: o que vai para o banco é o que está no arquivo do tribunal.
"""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from calculo_judicial.datas import somar_meses
from calculo_judicial.indices.importador_csv import CONVENCOES_FATOR, ErroImportacao, importar_arquivo
from calculo_judicial.indices.modelo import SERIES_ENCERRADAS, RepositorioSQLite, nome_curto

INDICES_DE_COMPARACAO = ("INPC", "IPCA", "IPCA_E", "IPCA_15", "IGPM", "IPC_R")
TOLERANCIA_PP = Decimal("0.01")  # pontos percentuais (fatores de tribunal costumam vir com 6 casas)
ORIGENS = {
    "bcb": "índice oficial (Banco Central)",
    "composta": "montada pela calculadora com índices oficiais (IBGE/BCB)",
    "ibge": "índice oficial (IBGE), série encerrada",
    "usuario": "tabela do tribunal importada neste computador",
    "pacote": "tabela oficial do tribunal, veio com a calculadora",
}


@dataclass
class TabelaDisponivel:
    codigo: str
    nome: str
    origem: str
    primeiro: date | None
    ultimo: date | None
    cadeia: str = ""  # tabelas compostas: os índices e as datas de troca


def tabelas_de_correcao(conn: sqlite3.Connection) -> list[TabelaDisponivel]:
    """Índices de correção que o cálculo pode usar: oficiais do BCB, tabelas de tribunal e cadeias montadas."""
    repo = RepositorioSQLite(conn=conn)
    procedencia = dict(conn.execute("SELECT codigo, origem FROM procedencia_tabelas").fetchall()) if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='procedencia_tabelas'").fetchone() else {}
    saida = []
    for codigo, nome, codigo_sgs in conn.execute(
            "SELECT codigo, nome, codigo_sgs FROM indices WHERE tipo = 'correcao' ORDER BY codigo_sgs IS NOT NULL, codigo"):
        if codigo in SERIES_ENCERRADAS:
            continue  # ex.: IPC-r (1994-1995): só entra dentro das tabelas de tribunal
        composta = repo.tabela_composta(codigo)
        primeiro, ultimo = conn.execute("SELECT MIN(mes_referencia), MAX(mes_referencia) FROM taxas_mensais "
                                        "WHERE indice_codigo = ? AND projecao = 0", (codigo,)).fetchone()
        if composta is not None and primeiro is None:
            origem = ORIGENS["composta"]
            cadeia = "; ".join(f"{nome_curto(p.indice)} desde {p.de:%d/%m/%Y}" for p in composta.periodos)
        else:
            origem = ORIGENS["bcb"] if codigo_sgs else ORIGENS["ibge"] if codigo == "IPC_R" else ORIGENS[
                procedencia.get(codigo, "pacote")]
            cadeia = ""
        saida.append(TabelaDisponivel(codigo, nome, origem, date.fromisoformat(primeiro) if primeiro else None,
                                      date.fromisoformat(ultimo) if ultimo else None, cadeia))
    return saida


@dataclass
class Ensaio:
    resumo: str
    trechos: list[str]  # períodos do arquivo e o índice oficial que cada um segue
    comparacao: list[str]  # últimos meses, lado a lado com os índices oficiais
    sugestao: str
    avisos: list[str] = field(default_factory=list)


def _taxas(conn: sqlite3.Connection, codigo: str) -> dict[date, Decimal]:
    return {date.fromisoformat(m): Decimal(str(t)) for m, t in conn.execute(
        "SELECT mes_referencia, taxa_pct FROM taxas_mensais WHERE indice_codigo = ? AND projecao = 0", (codigo,))}


def _coincidem(taxa: Decimal, oficial: Decimal | None) -> bool:
    return oficial is not None and abs(taxa - oficial) <= TOLERANCIA_PP


def _trechos(meses: list[date], importadas: dict[date, Decimal], oficiais: dict[str, dict[date, Decimal]],
             desloc: int) -> list[str]:
    """Agrupa meses seguidos que coincidem com o(s) mesmo(s) índice(s) oficial(is)."""
    saida, inicio, comuns, anterior = [], None, None, None

    def fechar():
        if inicio is not None:
            nome = " / ".join(nome_curto(i) for i in sorted(comuns)) if comuns else "nenhum índice oficial (regra própria)"
            saida.append(f"{inicio:%m/%Y} a {anterior:%m/%Y}: {nome}")

    for m in meses:
        bate = {i for i, serie in oficiais.items() if _coincidem(importadas[m], serie.get(somar_meses(m, desloc)))}
        if inicio is not None and ((comuns and bate & comuns) or (not comuns and not bate)):
            comuns = (comuns & bate) if comuns else comuns
        else:
            fechar()
            inicio, comuns = m, bate
        anterior = m
    fechar()
    return saida


def ensaiar_importacao(conn: sqlite3.Connection, caminho: Path, codigo: str, convencao_fator: str = "fator_fim_do_mes",
                       aceitar_fora_da_faixa: bool = False, substituir: bool = False) -> Ensaio:
    """Importa numa cópia do banco em memória (nada é gravado) e compara as taxas DO ARQUIVO com os índices oficiais.

    Levanta o erro da importação (arquivo inválido, lacuna, tabela montada que perderia cobertura...) como a
    importação real faria; a faixa de valores é conferida, mas o ensaio continua para mostrar a comparação.
    """
    if convencao_fator not in CONVENCOES_FATOR:
        raise ValueError(f"convenção inválida: {convencao_fator} (use {', '.join(CONVENCOES_FATOR)})")
    avisos = []

    def importar(aceitar: bool):
        memoria = sqlite3.connect(":memory:")
        conn.backup(memoria)
        with tempfile.TemporaryDirectory() as pasta:
            resultado = importar_arquivo(memoria, caminho, codigo, convencao_fator=convencao_fator, substituir=substituir,
                                         aceitar_fora_da_faixa=aceitar, caminho_alertas=Path(pasta) / "alertas.log")
        return memoria, resultado

    try:
        memoria, resultado = importar(aceitar_fora_da_faixa)
    except ErroImportacao as erro:
        if "fora da faixa" not in str(erro) or aceitar_fora_da_faixa:
            raise
        memoria, resultado = importar(True)  # para comparar mesmo assim; a gravação vai exigir a opção
        resultado.alertas = [a for a in resultado.alertas if "fora da faixa usual, aceita" not in a]
        avisos.append(str(erro))
    try:
        todas = _taxas(memoria, codigo)
        importadas = {m: todas[m] for m in resultado.meses_do_arquivo if m in todas}
        oficiais = {indice: _taxas(memoria, indice) for indice in INDICES_DE_COMPARACAO}
    finally:
        memoria.close()
    meses = sorted(importadas)
    acertos = {d: sum(1 for m in meses if any(_coincidem(importadas[m], s.get(somar_meses(m, d))) for s in oficiais.values()))
               for d in (-1, 0, 1)}
    desloc = max(acertos, key=lambda d: (acertos[d], d == 0))
    comparaveis = sum(1 for m in meses if any(somar_meses(m, desloc) in s for s in oficiais.values()))
    linhas = []
    for m in meses[-6:]:
        valores = " · ".join(f"{nome_curto(i)} {oficiais[i][m]:.2f}%" for i in INDICES_DE_COMPARACAO if m in oficiais[i])
        linhas.append(f"{m:%m/%Y}: arquivo {importadas[m]:.4f}% | {valores or 'sem índice oficial no mês'}")
    trechos = _trechos(meses, importadas, oficiais, desloc)
    minimo = min(comparaveis, max(3, comparaveis // 2))
    if comparaveis < 3:
        sugestao = (f"Só {comparaveis} mês(es) do arquivo têm índice oficial para comparar: poucos para confirmar a "
                    "convenção. Confira alguns valores com a tabela publicada antes de gravar.")
    elif acertos[desloc] < minimo:
        sugestao = ("As taxas do arquivo não seguem nenhum índice oficial do Banco Central/IBGE: a tabela tem regra própria "
                    "(ou o arquivo está em outra convenção). Confira os valores com a tabela publicada antes de gravar.")
    elif desloc == 0:
        sugestao = f"As taxas coincidem com índices oficiais em {acertos[0]} de {comparaveis} meses comparáveis: a leitura está certa."
    else:
        sentido = "para trás" if desloc == -1 else "para a frente"
        corrige = {("fator_fim_do_mes", -1): "fator_inicio_do_mes", ("fator_inicio_do_mes", 1): "fator_fim_do_mes"}
        if resultado.modo == "fator" and (convencao_fator, desloc) in corrige:
            sugestao = (f"As taxas só coincidem com os índices oficiais com um mês de deslocamento ({acertos[desloc]} de "
                        f"{comparaveis} meses): o fator deste arquivo está na outra convenção — repita com --convencao "
                        f"{corrige[(convencao_fator, desloc)]}.")
        else:
            sugestao = (f"As taxas só coincidem com os índices oficiais com um mês de deslocamento {sentido} "
                        f"({acertos[desloc]} de {comparaveis} meses): as datas da coluna mes_referencia parecem deslocadas. "
                        "Confira o arquivo (trocar a convenção não resolve).")
    resumo = resultado.resumo().replace("meses gravados", "meses lidos")
    return Ensaio(resumo, trechos, linhas, sugestao, avisos)
