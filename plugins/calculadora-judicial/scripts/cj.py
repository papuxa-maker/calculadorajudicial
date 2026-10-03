#!/usr/bin/env python3
"""Calculadora Judicial — ponto de entrada do plugin do Claude Code (só biblioteca padrão do Python).

    python cj.py --dados PASTA_DE_DADOS <comando> [opções]

Na primeira vez (e quando a versão do plugin muda as dependências), cria um ambiente Python
próprio e instala o necessário; depois só repassa o comando para ``python -m calculo_judicial.cli``.
A pasta de dados guarda índices, cálculos salvos e configurações do usuário e sobrevive às
atualizações do plugin; o código fica na pasta do plugin. No Windows o ambiente Python fica num
endereço curto (``%LOCALAPPDATA%\\CalculadoraJudicial``), porque a instalação estoura o limite de
260 caracteres do Windows quando a pasta de dados tem endereço longo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MINIMO = (3, 12)
SAIDA_PYTHON_ANTIGO = 4
SAIDA_PREPARACAO = 5


class FalhaPreparacao(RuntimeError):
    pass


def _erro(texto: str) -> None:
    print(texto, file=sys.stderr, flush=True)


def _separar_dados(argv: list[str]) -> tuple[Path, list[str]]:
    if len(argv) >= 2 and argv[0] == "--dados":
        bruto, resto = argv[1], argv[2:]
    else:
        bruto, resto = os.environ.get("CALCULADORA_DADOS") or os.environ.get("CLAUDE_PLUGIN_DATA") or "", argv
    if not bruto or "${" in bruto:  # placeholder não substituído (skill copiada fora do plugin)
        bruto = str(Path.home() / ".calculadora-judicial")
    return Path(bruto).expanduser(), resto


def _documentos() -> Path:
    """Pasta Documentos do usuário (no Windows, a real — inclusive quando o OneDrive a redireciona)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", wintypes.BYTE * 8)]

            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            pasta_id = GUID(0xFDD39AD0, 0x238F, 0x46AF, (wintypes.BYTE * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            caminho = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(pasta_id), 0, None, ctypes.byref(caminho)) == 0:
                resultado = Path(caminho.value)
                ctypes.windll.ole32.CoTaskMemFree(caminho)
                return resultado
        except Exception:  # noqa: BLE001 - sem a API, usa o caminho comum
            pass
    for nome in ("Documents", "Documentos"):
        if (Path.home() / nome).is_dir():
            return Path.home() / nome
    return Path.home()


def pasta_do_ambiente(dados: Path) -> Path:
    """Onde fica o ambiente Python: no Windows, num endereço curto e próprio de cada pasta de dados."""
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        codigo = hashlib.sha256(str(dados.resolve()).lower().encode("utf-8")).hexdigest()[:8]
        return Path(os.environ["LOCALAPPDATA"]) / "CalculadoraJudicial" / f"amb-{codigo}"
    return dados / "ambiente"


def _python_do_ambiente(ambiente: Path) -> Path:
    return ambiente / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _config_ambiente(ambiente: Path) -> dict[str, str]:
    cfg = {}
    try:
        for linha in (ambiente / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            chave, _, valor = linha.partition("=")
            cfg[chave.strip()] = valor.strip()
    except OSError:
        pass
    return cfg


def _assinatura(arquivos: list[Path], versao: str) -> str:
    h = hashlib.sha256(versao.encode())
    for arquivo in arquivos:
        if arquivo.exists():
            h.update(arquivo.read_bytes())
    return h.hexdigest()


def _ambiente_saudavel(ambiente: Path, python: Path) -> bool:
    """O ambiente existe e o Python em que ele se baseia ainda está instalado."""
    cfg = _config_ambiente(ambiente)
    return python.exists() and bool(cfg.get("home")) and Path(cfg["home"]).exists()


def _apagar(pasta: Path) -> None:
    def tornar_gravavel(funcao, caminho, _erro):
        os.chmod(caminho, 0o700)
        funcao(caminho)

    if pasta.exists():
        shutil.rmtree(pasta, onexc=tornar_gravavel)


def _pip_funciona(python: Path) -> bool:
    r = subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True)
    return r.returncode == 0


def preparar_ambiente(dados: Path, interface: bool) -> Path:
    """Ambiente Python da calculadora (criado e atualizado só quando preciso; refeito se ficou pela metade).

    As versões dos componentes são as testadas (``restricoes.txt``, gerado na montagem do plugin).
    """
    ambiente = pasta_do_ambiente(dados)
    python = _python_do_ambiente(ambiente)
    marca = ambiente / "instalado.json"
    restricoes = RAIZ / "restricoes.txt"
    conjuntos = {"basico": [RAIZ / "requisitos.txt"], "interface": [RAIZ / "requisitos-interface.txt"]}
    pedidos = ["basico"] + (["interface"] if interface else [])
    feito = {}
    if marca.exists():
        try:
            feito = json.loads(marca.read_text(encoding="utf-8"))
        except ValueError:
            feito = {}
    saudavel = _ambiente_saudavel(ambiente, python)
    versao = _config_ambiente(ambiente).get("version", "") if saudavel else ""
    faltando = [c for c in pedidos if feito.get(c) != _assinatura(conjuntos[c] + [restricoes], versao)]
    if saudavel and not faltando:
        return python
    novo = not (saudavel and marca.exists() and _pip_funciona(python))
    log = dados / "log" / "instalacao.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        if novo:
            _erro("Preparando a calculadora neste computador (só na primeira vez; leva de 1 a 3 minutos)...")
            _apagar(ambiente)  # ambiente pela metade, ou de um Python que foi desinstalado: refeito do zero
            ambiente.parent.mkdir(parents=True, exist_ok=True)
            # no app da loja da Microsoft a pasta é redirecionada e o venv avisa disso: aviso inofensivo, só confunde
            logging.getLogger("venv").setLevel(logging.ERROR)
            venv.EnvBuilder(with_pip=True).create(ambiente)
            feito, versao = {}, _config_ambiente(ambiente).get("version", "")
            faltando = pedidos
        elif faltando == ["interface"]:
            _erro("Baixando os componentes da tela visual (só na primeira vez; alguns minutos)...")
        else:
            _erro("Atualizando os componentes da calculadora (versão nova do plugin)...")
        for conjunto in faltando:
            comando = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--retries", "2",
                       "--timeout", "30"]
            for arquivo in conjuntos[conjunto]:
                comando += ["-r", str(arquivo)]
            if restricoes.exists():
                comando += ["-c", str(restricoes)]
            with open(log, "a", encoding="utf-8") as saida:
                if subprocess.run(comando, stdout=saida, stderr=saida).returncode != 0:
                    raise FalhaPreparacao(f"o download dos componentes do Python não funcionou (detalhes em {log})")
            feito[conjunto] = _assinatura(conjuntos[conjunto] + [restricoes], versao)
    except (OSError, subprocess.CalledProcessError, FalhaPreparacao) as erro:
        if novo:
            _apagar(ambiente)  # nada pela metade: a próxima tentativa começa limpa
        raise FalhaPreparacao(str(erro)) from erro
    marca.write_text(json.dumps(feito), encoding="utf-8")
    return python


def _pasta_de_copia(saida: str) -> Path:
    """Fica junto dos relatórios (padrão: Documentos/Calculadora Judicial)."""
    return Path(saida) / "Cópia de segurança (não apague)"


def _calculos(banco: Path) -> set[tuple] | None:
    """Cálculos salvos no banco (id, criado_em), ou None se o arquivo não for um banco válido."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return None
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calculos'").fetchone():
                return set()
            return set(conn.execute("SELECT id, criado_em FROM calculos").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _copiar_banco(origem: Path, destino: Path) -> None:
    """Cópia consistente (API de backup do SQLite), mesmo com a tela aberta gravando."""
    import sqlite3

    fonte, alvo = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True), sqlite3.connect(destino)
    try:
        fonte.backup(alvo)
    finally:
        fonte.close()
        alvo.close()


def _guardar_a_parte(arquivo: Path, rotulo: str) -> Path:
    """Renomeia ``arquivo`` para um nome com data e hora (nunca apaga uma cópia)."""
    from datetime import datetime

    novo = arquivo.with_name(f"calculos {rotulo} {datetime.now():%Y-%m-%d %H%M%S}.db")
    arquivo.replace(novo)
    return novo


def restaurar_copia(dados: Path, saida: str) -> None:
    """Plugin reinstalado (pasta de dados nova): traz de volta os cálculos salvos e a configuração do escritório.

    Só preenche o que falta (pasta sem cálculos salvos ou sem configuração); uma cópia danificada não é usada.
    """
    copia = _pasta_de_copia(saida)
    recuperado = []
    try:
        atuais = _calculos(dados / "calculos.db") if (dados / "calculos.db").exists() else set()
        guardados = _calculos(copia / "calculos.db") if (copia / "calculos.db").exists() else set()
        if guardados is None:
            _erro(f"A cópia de segurança dos cálculos ({copia / 'calculos.db'}) está danificada e não foi usada.")
        elif guardados and not atuais and atuais is not None:
            _copiar_banco(copia / "calculos.db", dados / "calculos.db")
            recuperado.append(f"{len(guardados)} cálculo(s) salvo(s)")
        if not (dados / "configuracao").exists() and (copia / "configuracao").is_dir():
            shutil.copytree(copia / "configuracao", dados / "configuracao")
            recuperado.append("a configuração do escritório")
    except Exception as erro:  # noqa: BLE001 - a recuperação nunca impede o comando; tenta de novo no próximo
        _erro(f"Não consegui recuperar a cópia de segurança ({copia}): {erro}. Tento de novo no próximo uso.")
        return
    if recuperado:
        _erro(f"Recuperei {' e '.join(recuperado)} da cópia de segurança ({copia}).")


def fazer_copia(dados: Path, saida: str) -> None:
    """Cópia dos cálculos salvos e da configuração fora da pasta do plugin (desinstalar apaga a pasta do plugin).

    Nunca perde uma cópia: se a existente tiver cálculos que não estão nesta instalação (outro computador, ou
    uma recuperação que falhou) ou estiver danificada, ela é guardada com outro nome antes da cópia nova.
    """
    copia = _pasta_de_copia(saida)
    origem = dados / "calculos.db"
    destino = copia / "calculos.db"
    try:
        if origem.exists() and (not destino.exists() or destino.stat().st_mtime < origem.stat().st_mtime):
            atuais = _calculos(origem)
            if atuais is not None:
                copia.mkdir(parents=True, exist_ok=True)
                if destino.exists():
                    guardados = _calculos(destino)
                    if guardados is None:
                        _erro(f"A cópia de segurança anterior estava danificada; guardei-a como "
                              f"'{_guardar_a_parte(destino, 'danificada').name}' e fiz uma nova.")
                    elif guardados - atuais:
                        _erro(f"A cópia de segurança tinha {len(guardados - atuais)} cálculo(s) que não estão mais "
                              "nesta instalação (apagados aqui ou salvos em outro computador); guardei-a como "
                              f"'{_guardar_a_parte(destino, 'anterior').name}' na mesma pasta.")
                _copiar_banco(origem, destino)
        if (dados / "configuracao").is_dir() and any((dados / "configuracao").iterdir()):
            copia.mkdir(parents=True, exist_ok=True)
            shutil.copytree(dados / "configuracao", copia / "configuracao", dirs_exist_ok=True)
    except Exception as erro:  # noqa: BLE001 - a cópia nunca atrapalha o comando
        _erro(f"Aviso: não consegui atualizar a cópia de segurança em {copia} ({erro}).")


def preparar_indices(dados: Path, python: Path, env: dict) -> None:
    """Primeiro uso: copia o banco de índices do pacote. Versão nova do pacote: acrescenta tabelas de tribunal."""
    pacote = RAIZ / "dados_iniciais" / "indices.db"
    banco = dados / "indices.db"
    if not pacote.exists():
        return
    if not banco.exists():
        shutil.copy2(pacote, banco)
    marca = dados / "pacote.json"
    versao = (RAIZ / "VERSAO").read_text(encoding="utf-8").strip() if (RAIZ / "VERSAO").exists() else ""
    try:
        anterior = json.loads(marca.read_text(encoding="utf-8")).get("versao") if marca.exists() else None
    except ValueError:
        anterior = None
    if anterior != versao:
        r = subprocess.run([str(python), "-m", "calculo_judicial.cli", "_sincronizar", str(pacote)], env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            if r.stdout.strip():
                print(r.stdout.strip(), flush=True)
            marca.write_text(json.dumps({"versao": versao}), encoding="utf-8")
        else:  # banco ocupado (tela aberta gravando, backup...): tenta de novo no próximo comando
            _erro("Não consegui acrescentar agora as tabelas de tribunal da versão nova do plugin; tento de novo no "
                  "próximo uso.")


def main(argv: list[str]) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")
    if sys.version_info < MINIMO:
        _erro(f"A calculadora precisa do Python {MINIMO[0]}.{MINIMO[1]} ou mais novo; este é o "
              f"{sys.version_info.major}.{sys.version_info.minor}. Instale o Python 3.12 e chame de novo com ele.")
        return SAIDA_PYTHON_ANTIGO
    dados, argumentos = _separar_dados(argv)
    if not argumentos:
        argumentos = ["--help"]
    dados.mkdir(parents=True, exist_ok=True)
    try:
        python = preparar_ambiente(dados, interface=argumentos[0] == "abrir")
    except FalhaPreparacao as erro:
        _erro("Não foi possível preparar a calculadora neste computador: " + str(erro) + ".\n"
              "Causas comuns: sem internet na primeira vez, antivírus ou firewall bloqueando o download de "
              "componentes do Python (pypi.org). Nada ficou pela metade: tente de novo depois de conferir a internet.")
        return SAIDA_PREPARACAO
    env = os.environ.copy()
    env.update(
        CALCULADORA_DADOS=str(dados),
        PYTHONPATH=str(RAIZ / "src"),
        PYTHONIOENCODING="utf-8",
        CALCULADORA_VERSAO=(RAIZ / "VERSAO").read_text(encoding="utf-8").strip() if (RAIZ / "VERSAO").exists() else "?",
    )
    env.setdefault("CALCULADORA_SAIDA", str(_documentos() / "Calculadora Judicial"))
    env.pop("INDICES_DB", None)  # o banco é sempre o da pasta de dados
    restaurar_copia(dados, env["CALCULADORA_SAIDA"])
    preparar_indices(dados, python, env)
    codigo = subprocess.run([str(python), "-m", "calculo_judicial.cli", *argumentos], env=env).returncode
    fazer_copia(dados, env["CALCULADORA_SAIDA"])
    return codigo


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
