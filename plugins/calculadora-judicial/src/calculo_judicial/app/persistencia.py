"""Cálculos salvos em ``calculos.db`` (SQLite): JSON dos parâmetros + resultado (SPEC, seção 7)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from calculo_judicial.indices.modelo import PASTA_DADOS

CAMINHO_PADRAO = PASTA_DADOS / "calculos.db"

SQL = """
CREATE TABLE IF NOT EXISTS calculos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  criado_em TEXT NOT NULL,
  atualizado_em TEXT NOT NULL,
  numero_cnj TEXT,
  vara TEXT,
  autor TEXT,
  reu TEXT,
  area TEXT NOT NULL,
  tipo TEXT,
  data_calculo TEXT NOT NULL,
  total TEXT NOT NULL,
  entrada_json TEXT NOT NULL,
  resultado_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS configuracoes (
  chave TEXT PRIMARY KEY,
  valor TEXT NOT NULL
);
"""


@dataclass
class CalculoSalvo:
    id: int
    criado_em: str
    atualizado_em: str
    numero_cnj: str
    vara: str
    autor: str
    reu: str
    area: str
    tipo: str
    data_calculo: str
    total: str
    entrada_json: str
    resultado_json: str


def conectar(caminho: str | Path | None = None) -> sqlite3.Connection:
    destino = Path(caminho) if caminho else CAMINHO_PADRAO
    destino.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(destino))
    conn.executescript(SQL)
    return conn


def salvar(conn, processo: dict, area: str, data_calculo: str, total: str, entrada_json: str, resultado_json: str,
           id_existente: int | None = None) -> int:
    agora = datetime.now().isoformat(timespec="seconds")
    campos = (
        processo.get("numero_cnj", ""), processo.get("vara", ""), processo.get("autor", ""), processo.get("reu", ""),
        area, processo.get("tipo", ""), data_calculo, total, entrada_json, resultado_json,
    )
    if id_existente:
        cur = conn.execute(
            "UPDATE calculos SET atualizado_em=?, numero_cnj=?, vara=?, autor=?, reu=?, area=?, tipo=?, data_calculo=?, "
            "total=?, entrada_json=?, resultado_json=? WHERE id=?",
            (agora, *campos, id_existente),
        )
        if cur.rowcount:
            conn.commit()
            return id_existente
        # o registro foi excluído enquanto estava aberto: grava como cálculo novo em vez de "salvar" no vazio
    cur = conn.execute(
        "INSERT INTO calculos (criado_em, atualizado_em, numero_cnj, vara, autor, reu, area, tipo, data_calculo, total, "
        "entrada_json, resultado_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (agora, agora, *campos),
    )
    conn.commit()
    return int(cur.lastrowid)


def listar(conn) -> list[CalculoSalvo]:
    linhas = conn.execute(
        "SELECT id, criado_em, atualizado_em, numero_cnj, vara, autor, reu, area, tipo, data_calculo, total, entrada_json, "
        "resultado_json FROM calculos ORDER BY atualizado_em DESC, id DESC"
    ).fetchall()
    return [CalculoSalvo(*l) for l in linhas]


def abrir(conn, id_: int) -> CalculoSalvo | None:
    linha = conn.execute(
        "SELECT id, criado_em, atualizado_em, numero_cnj, vara, autor, reu, area, tipo, data_calculo, total, entrada_json, "
        "resultado_json FROM calculos WHERE id=?",
        (id_,),
    ).fetchone()
    return CalculoSalvo(*linha) if linha else None


def duplicar(conn, id_: int) -> int:
    """Cópia gravada do cálculo (a interface não usa: lá a cópia só é gravada no primeiro Salvar)."""
    original = abrir(conn, id_)
    if original is None:
        raise KeyError(id_)
    processo = json.loads(original.entrada_json)["processo"]
    return salvar(conn, processo, original.area, original.data_calculo, original.total, original.entrada_json, original.resultado_json)


def excluir(conn, id_: int) -> None:
    conn.execute("DELETE FROM calculos WHERE id=?", (id_,))
    conn.commit()


def ler_config(conn) -> dict:
    return {k: json.loads(v) for k, v in conn.execute("SELECT chave, valor FROM configuracoes")}


def gravar_config(conn, valores: dict) -> None:
    for chave, valor in valores.items():
        conn.execute(
            "INSERT INTO configuracoes (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
            (chave, json.dumps(valor, ensure_ascii=False)),
        )
    conn.commit()
