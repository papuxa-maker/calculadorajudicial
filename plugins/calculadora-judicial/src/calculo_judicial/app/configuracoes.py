"""Configurações do escritório (cabeçalho, ressalva, pasta de saída) e SMTP dos alertas.

Cabeçalho e ressalva ficam em ``calculos.db`` (tabela ``configuracoes``); o SMTP
fica no ``.env`` da raiz, que é onde o agendador o lê (SPEC, seção 5).
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from calculo_judicial.app import persistencia
from calculo_judicial.indices.modelo import PASTA_DADOS

RESSALVA_PADRAO = "Valores calculados até {data}, sujeitos a atualização até o efetivo pagamento."
CAMINHO_ENV = PASTA_DADOS / ".env"
# fora de dados/ (pasta só dos arquivos oficiais fornecidos, que o sistema nunca altera)
CAMINHO_LOGO = PASTA_DADOS / "configuracao" / "logo_escritorio.png"
CHAVES_SMTP = ("SMTP_HOST", "SMTP_PORTA", "SMTP_USUARIO", "SMTP_SENHA", "SMTP_TLS", "SMTP_SSL", "ALERTA_REMETENTE", "ALERTA_DESTINATARIOS")


@dataclass
class Configuracao:
    nome_escritorio: str = ""
    oab: str = ""
    endereco: str = ""
    logo: str = ""
    ressalva: str = RESSALVA_PADRAO
    pasta_saida: str = os.environ.get("CALCULADORA_SAIDA", "").strip() or str(PASTA_DADOS / "saida")


def carregar(caminho_db: str | Path | None = None) -> Configuracao:
    conn = persistencia.conectar(caminho_db)
    try:
        dados = persistencia.ler_config(conn).get("escritorio", {})
    finally:
        conn.close()
    base = asdict(Configuracao())
    base.update({k: v for k, v in dados.items() if k in base})
    return Configuracao(**base)


def salvar(config: Configuracao, caminho_db: str | Path | None = None) -> None:
    conn = persistencia.conectar(caminho_db)
    try:
        persistencia.gravar_config(conn, {"escritorio": asdict(config)})
    finally:
        conn.close()


def validar_logo(conteudo: bytes) -> None:
    """Confere que o arquivo é uma imagem PNG legível (ValueError se não for)."""
    import io

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(conteudo)) as img:
            if img.format != "PNG":
                raise ValueError(f"o logo deve ser PNG (recebido {img.format})")
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as erro:
        raise ValueError(f"arquivo de logo inválido: {erro}") from None


def salvar_logo(conteudo: bytes, destino: Path = CAMINHO_LOGO) -> Path:
    validar_logo(conteudo)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)
    return destino


def validar_smtp(valores: dict[str, str]) -> list[str]:
    """Erros de preenchimento do SMTP (lista vazia = ok). Campos vazios são permitidos (alerta por e-mail desligado)."""
    erros = []
    porta = valores.get("SMTP_PORTA", "").strip()
    if porta and (not porta.isdigit() or not 0 < int(porta) < 65536):
        erros.append(f"porta SMTP inválida: {porta!r}")
    if valores.get("SMTP_TLS") == "1" and valores.get("SMTP_SSL") == "1":
        erros.append("marque STARTTLS ou SSL, não os dois")
    enderecos = [valores.get("ALERTA_REMETENTE", "")] + valores.get("ALERTA_DESTINATARIOS", "").split(",")
    for endereco in (e.strip() for e in enderecos):
        if endereco and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", endereco):
            erros.append(f"e-mail inválido: {endereco!r}")
    if valores.get("SMTP_HOST", "").strip() and not valores.get("ALERTA_DESTINATARIOS", "").strip():
        erros.append("informe ao menos um destinatário dos alertas")
    return erros


def ler_smtp(caminho: Path = CAMINHO_ENV) -> dict[str, str]:
    valores: dict[str, str] = {}
    if caminho.exists():
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                chave, _, valor = linha.partition("=")
                valores[chave.strip()] = valor.strip()
    return {k: v for k, v in valores.items() if k in CHAVES_SMTP}


def gravar_smtp(valores: dict[str, str], caminho: Path = CAMINHO_ENV) -> None:
    """Atualiza as chaves SMTP no ``.env`` preservando as demais linhas."""
    linhas = caminho.read_text(encoding="utf-8").splitlines() if caminho.exists() else []
    restantes = dict(valores)
    saida = []
    for linha in linhas:
        chave = linha.partition("=")[0].strip()
        if "=" in linha and not linha.lstrip().startswith("#") and chave in restantes:
            saida.append(f"{chave}={restantes.pop(chave)}")
        else:
            saida.append(linha)
    saida += [f"{k}={v}" for k, v in restantes.items()]
    caminho.write_text("\n".join(saida) + "\n", encoding="utf-8")
    if caminho == CAMINHO_ENV:  # vale já para esta calculadora aberta (sem reiniciar)
        for chave, valor in valores.items():
            os.environ[chave] = valor
