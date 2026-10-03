"""Importação de tabela de tribunal enviada pela tela Índices (testável sem Streamlit).

O arquivo é validado e importado a partir de uma cópia temporária; só depois de
aceito é guardado em ``dados/``, e nunca por cima de um arquivo existente (a pasta
guarda os arquivos oficiais fornecidos pelo escritório, que o sistema não altera).
"""

from __future__ import annotations

import re
import tempfile
from datetime import datetime
from pathlib import Path

from calculo_judicial.indices import coletor_bcb
from calculo_judicial.indices.importador_csv import importar_arquivo


class ErroImportacaoInterface(ValueError):
    """Importação recusada; nada foi gravado no banco nem em ``dados/``."""


def validar_codigo(indice: str) -> str:
    codigo = (indice or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,19}", codigo):
        raise ErroImportacaoInterface("código do índice inválido: use letras maiúsculas, números e _ (ex.: TJGO)")
    if codigo in {d.codigo for d in coletor_bcb.SERIES_BCB}:
        raise ErroImportacaoInterface(
            f"{codigo} vem da API do Banco Central (botão 'Atualizar agora'); a importação manual é para tabelas de tribunal"
        )
    return codigo


def nome_seguro(nome: str) -> str:
    return re.sub(r"[^a-z0-9_.-]", "_", Path(nome).name.lower()) or "tabela.csv"


def guardar_copia(pasta: Path, nome: str, conteudo: bytes) -> Path:
    """Cópia do arquivo importado sem sobrescrever: mesmo conteúdo reaproveita; outro conteúdo ganha sufixo."""
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / nome
    if destino.exists():
        if destino.read_bytes() == conteudo:
            return destino
        destino = pasta / f"{destino.stem}_importado_{datetime.now():%Y%m%d_%H%M%S}{destino.suffix}"
    destino.write_bytes(conteudo)
    return destino


def importar_enviado(conn, nome_arquivo: str, conteudo: bytes, indice: str, *, nome: str | None = None,
                     fonte: str | None = None, convencao_fator: str = "fator_fim_do_mes", substituir: bool = False,
                     aceitar_fora_da_faixa: bool = False, pasta_dados: Path | None = None):
    """Valida e importa; devolve (resultado da importação, caminho da cópia em dados/)."""
    codigo = validar_codigo(indice)
    nome_final = nome_seguro(nome_arquivo)
    with tempfile.TemporaryDirectory() as pasta:
        temporario = Path(pasta) / nome_final
        temporario.write_bytes(conteudo)
        try:
            resultado = importar_arquivo(conn, temporario, codigo, nome=nome or None, fonte=fonte or None,
                                         convencao_fator=convencao_fator, substituir=substituir,
                                         aceitar_fora_da_faixa=aceitar_fora_da_faixa)
        except Exception as erro:  # noqa: BLE001 - CSV/XLSX inválido de qualquer tipo: recusar sem gravar
            raise ErroImportacaoInterface(str(erro) or type(erro).__name__) from erro
    destino = guardar_copia(pasta_dados or coletor_bcb.PASTA_DADOS / "dados", nome_final, conteudo)
    return resultado, destino
