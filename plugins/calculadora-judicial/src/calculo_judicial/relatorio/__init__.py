"""Relatórios da memória de cálculo (etapa 4): PDF e Excel.

    relatorio.gerar_pdf(memoria, config, processo) -> bytes
    relatorio.gerar_xlsx(memoria, config, processo) -> bytes
    relatorio.gravar(memoria, pasta, config, processo) -> {"pdf": Path, "excel": Path}
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from calculo_judicial.motor.memoria import Memoria
from calculo_judicial.relatorio.memoria_xlsx import gerar_xlsx as _gerar_xlsx
from calculo_judicial.relatorio.tabelas import TabelasMemoria, tabelas_da_memoria, total_exibido

__all__ = ["gerar_pdf", "gerar_xlsx", "gravar", "nome_arquivo", "tabelas_da_memoria", "TabelasMemoria", "total_exibido"]


def _identificacao(memoria: Memoria, processo: dict | None = None) -> str:
    """Sem número de processo: o tipo informado ou a descrição da primeira parcela (letras, números e hífens)."""
    tipo = str((processo or {}).get("tipo") or "").strip()
    if not tipo and not memoria.parcelas:
        return "sem-numero"
    texto = unicodedata.normalize("NFKD", tipo or memoria.parcelas[0].parcela.descricao)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^A-Za-z0-9]+", "-", texto).strip("-")[:40].strip("-")
    return texto or "sem-numero"


def nome_arquivo(memoria: Memoria, processo: dict | None = None, extensao: str = "pdf") -> str:
    """``memoria_{numero_cnj}_{data}.pdf`` (SPEC, seção 8), com o número CNJ só com dígitos, pontos e hífens.

    Sem número de processo, usa a descrição da primeira parcela (``memoria_Nota-promissoria_2026-09-01.pdf``).
    """
    cnj = re.sub(r"[^0-9.\-]", "", (processo or {}).get("numero_cnj", "")) or _identificacao(memoria, processo)
    return f"memoria_{cnj}_{memoria.parametros.data_calculo.isoformat()}.{extensao}"


def gerar_pdf(memoria: Memoria, config=None, processo: dict | None = None, nome: str | None = None) -> bytes:
    """PDF da memória; ``nome`` é o nome do arquivo impresso no rodapé (padrão: ``nome_arquivo``)."""
    from calculo_judicial.relatorio.memoria_pdf import gerar_pdf as _gerar_pdf

    return _gerar_pdf(memoria, config, processo, nome or nome_arquivo(memoria, processo, "pdf"))


def _gravar_novo(caminho: Path, conteudo: bytes) -> bool:
    """Cria o arquivo só se ele não existir (modo "x", atômico) e grava; ``False`` se o nome já foi tomado."""
    try:
        with open(caminho, "xb") as saida:
            saida.write(conteudo)
    except FileExistsError:
        return False
    except BaseException:
        caminho.unlink(missing_ok=True)  # nada pela metade com o nome do relatório
        raise
    return True


def gravar(memoria: Memoria, pasta: Path, config=None, processo: dict | None = None) -> dict[str, Path]:
    """Grava o PDF e o Excel da memória na pasta sem sobrescrever nada — ``base``, ``base_2``, ``base_3``...

    O conteúdo é gerado antes de o arquivo existir (um cálculo interrompido não deixa arquivo vazio com o nome
    do relatório) e a criação exclusiva garante que dois cálculos gravados no mesmo instante, na tela e pelo
    Claude, nunca fiquem com o mesmo nome. O rodapé do PDF traz o nome final do arquivo.
    """
    pasta = Path(pasta).expanduser()
    pasta.mkdir(parents=True, exist_ok=True)
    base = nome_arquivo(memoria, processo, "pdf")[: -len(".pdf")]
    excel = gerar_xlsx(memoria, config, processo)
    n = 1
    while True:
        tronco = base if n == 1 else f"{base}_{n}"
        n += 1
        pdf, xlsx = pasta / f"{tronco}.pdf", pasta / f"{tronco}.xlsx"
        if pdf.exists() or xlsx.exists():
            continue
        if not _gravar_novo(pdf, gerar_pdf(memoria, config, processo, nome=pdf.name)):
            continue  # outro cálculo tomou o nome enquanto este PDF era gerado
        if not _gravar_novo(xlsx, excel):
            pdf.unlink(missing_ok=True)
            continue
        return {"pdf": pdf, "excel": xlsx}


def gerar_xlsx(memoria: Memoria, config=None, processo: dict | None = None) -> bytes:
    return _gerar_xlsx(memoria, config, processo)
