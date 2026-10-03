"""Índices que chegam com o pacote do plugin (tabelas de tribunal e tabelas compostas).

Os índices do Banco Central se atualizam sozinhos pela internet (``atualizacao_automatica``).
As tabelas de tribunal (TJSP, JF, JF_PREV) vêm de arquivos oficiais e chegam aos advogados
dentro do pacote do plugin: a cada versão nova, os meses que faltam são acrescentados e um mês
corrigido no pacote (valor diferente) é corrigido também no banco de cada um — mas só nas
tabelas que vieram do pacote (``procedencia_tabelas``). Uma tabela importada pelo próprio usuário
nunca é alterada.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from calculo_judicial.indices.fatores import recalcular_fatores


def sincronizar_com_pacote(conn: sqlite3.Connection, caminho_pacote: str | Path) -> list[str]:
    """Acrescenta ao banco do usuário o que o banco do pacote tem a mais. Devolve o que mudou, em frases."""
    pacote = sqlite3.connect(f"file:{Path(caminho_pacote).as_posix()}?mode=ro", uri=True)
    mudancas: list[str] = []
    try:
        # tabelas de tribunal: índices sem série do Banco Central (codigo_sgs vazio) que têm taxas no pacote
        tribunais = pacote.execute(
            "SELECT codigo, nome, fonte, tipo, codigo_sgs, url_fonte FROM indices WHERE codigo_sgs IS NULL "
            "AND codigo IN (SELECT DISTINCT indice_codigo FROM taxas_mensais)"
        ).fetchall()
        for info in tribunais:
            codigo = info[0]
            origem = conn.execute("SELECT origem FROM procedencia_tabelas WHERE codigo=?", (codigo,)).fetchone()
            if origem and origem[0] == "usuario":
                continue  # tabela que o próprio usuário importou: a dele vale
            conn.execute(
                "INSERT OR IGNORE INTO indices (codigo, nome, fonte, tipo, codigo_sgs, url_fonte) VALUES (?, ?, ?, ?, ?, ?)", info
            )
            existentes = {m: (t, p) for m, t, p in conn.execute(
                "SELECT mes_referencia, taxa_pct, projecao FROM taxas_mensais WHERE indice_codigo=?", (codigo,))}
            novos, corrigidos = [], []
            for linha in pacote.execute(
                    "SELECT indice_codigo, mes_referencia, taxa_pct, projecao, data_coleta FROM taxas_mensais "
                    "WHERE indice_codigo=? AND projecao=0 ORDER BY mes_referencia", (codigo,)):
                atual = existentes.get(linha[1])
                if atual is None:
                    novos.append(linha)
                elif atual[0] != linha[2] or atual[1]:
                    corrigidos.append(linha)
            if novos or corrigidos:
                conn.executemany(
                    "INSERT OR REPLACE INTO taxas_mensais (indice_codigo, mes_referencia, taxa_pct, fator_acumulado, projecao, "
                    "data_coleta) VALUES (?, ?, ?, NULL, ?, ?)", novos + corrigidos)
                recalcular_fatores(conn, codigo)
            conn.execute("INSERT OR REPLACE INTO procedencia_tabelas (codigo, origem) VALUES (?, 'pacote')", (codigo,))
            if novos:
                mudancas.append(f"{codigo}: {len(novos)} meses acrescentados ({novos[0][1][:7]} a {novos[-1][1][:7]})")
            if corrigidos:
                mudancas.append(f"{codigo}: {len(corrigidos)} meses corrigidos pelo pacote "
                                f"({', '.join(l[1][:7] for l in corrigidos[:6])}) — refaça os cálculos que usaram essa tabela")
        # tabelas compostas (ex.: TJGO, POUPANCA): o pacote atualiza a cadeia, salvo se o usuário importou a tabela real
        for nome, periodos_json in pacote.execute("SELECT nome, periodos_json FROM tabelas_compostas"):
            atual = conn.execute("SELECT periodos_json FROM tabelas_compostas WHERE nome=?", (nome,)).fetchone()
            tabela_real = conn.execute("SELECT 1 FROM taxas_mensais WHERE indice_codigo=? LIMIT 1", (nome,)).fetchone()
            if tabela_real:
                continue
            info = pacote.execute("SELECT codigo, nome, fonte, tipo, codigo_sgs, url_fonte FROM indices WHERE codigo=?", (nome,)).fetchone()
            info_atual = conn.execute("SELECT codigo, nome, fonte, tipo, codigo_sgs, url_fonte FROM indices WHERE codigo=?", (nome,)).fetchone()
            if info and tuple(info) != (tuple(info_atual) if info_atual else None):
                conn.execute(
                    "INSERT OR REPLACE INTO indices (codigo, nome, fonte, tipo, codigo_sgs, url_fonte) VALUES (?, ?, ?, ?, ?, ?)", info
                )  # descrição da cadeia (vai para a memória de cálculo)
            if atual and atual[0] == periodos_json:
                continue
            conn.execute("INSERT OR REPLACE INTO tabelas_compostas (nome, periodos_json) VALUES (?, ?)", (nome, periodos_json))
            mudancas.append(f"{nome}: {'tabela atualizada pelo pacote' if atual else 'tabela incluída'}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pacote.close()
    return mudancas
