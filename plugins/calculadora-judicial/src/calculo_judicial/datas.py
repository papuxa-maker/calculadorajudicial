"""Utilitários de datas e meses de competência."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta


def primeiro_dia(d: date) -> date:
    """Primeiro dia do mês de ``d``."""
    return date(d.year, d.month, 1)


def somar_meses(d: date, meses: int) -> date:
    """Soma ``meses`` a ``d`` mantendo o dia (limitado ao último dia do mês)."""
    total = d.year * 12 + (d.month - 1) + meses
    ano, mes = divmod(total, 12)
    mes += 1
    dia = min(d.day, calendar.monthrange(ano, mes)[1])
    return date(ano, mes, dia)


def proximo_mes(d: date) -> date:
    """Primeiro dia do mês seguinte ao de ``d``."""
    return somar_meses(primeiro_dia(d), 1)


def mes_anterior(d: date) -> date:
    """Primeiro dia do mês anterior ao de ``d``."""
    return somar_meses(primeiro_dia(d), -1)


def dias_no_mes(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def ultimo_dia(d: date) -> date:
    return date(d.year, d.month, dias_no_mes(d))


def indice_mes(d: date) -> int:
    """Número sequencial do mês (ano*12 + mês-1), útil para diferenças."""
    return d.year * 12 + d.month - 1


def meses_competencia(de: date, ate: date) -> list[date]:
    """Meses de competência entre ``de`` (inclusive) e o mês anterior a ``ate``.

    Convenção do projeto: corrigir de A até B acumula as taxas dos meses de A
    (inclusive) até o mês anterior a B. Devolve a lista de primeiros dias.
    """
    inicio = primeiro_dia(de)
    fim = primeiro_dia(ate)
    meses = []
    atual = inicio
    while atual < fim:
        meses.append(atual)
        atual = somar_meses(atual, 1)
    return meses


def diferenca_meses_dias(de: date, ate: date) -> tuple[int, int]:
    """Diferença de calendário em meses completos e dias restantes entre ``de`` e ``ate``.

    Ex.: 15/03/2023 -> 01/10/2026 = (42 meses, 16 dias). Se ``ate`` <= ``de``, (0, 0).
    """
    if ate <= de:
        return 0, 0
    meses = indice_mes(ate) - indice_mes(de)
    if somar_meses(de, meses) > ate:
        meses -= 1
    ancora = somar_meses(de, meses)
    return meses, (ate - ancora).days


MESES_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}


def parse_data(texto: str | date | datetime) -> date:
    """Lê data em 'aaaa-mm-dd', 'dd/mm/aaaa', 'mm/aaaa', 'aaaa-mm' ou 'jan/2024' ('jan/24')."""
    if isinstance(texto, datetime):
        return texto.date()
    if isinstance(texto, date):
        return texto
    t = str(texto).strip()
    partes = t.lower().replace("-", "/").replace(".", "/").split("/")
    if len(partes) == 2 and partes[0][:3] in MESES_PT and partes[1].isdigit():
        ano = int(partes[1])
        if len(partes[1]) == 2:
            ano += 2000 if ano < 50 else 1900
        return date(ano, MESES_PT[partes[0][:3]], 1)
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%y"):
        try:
            return datetime.strptime(t, formato).date()
        except ValueError:
            pass
    for formato in ("%m/%Y", "%Y-%m", "%m-%Y", "%Y/%m"):
        try:
            return datetime.strptime(t, formato).date().replace(day=1)
        except ValueError:
            pass
    raise ValueError(f"data inválida: {texto!r}")


def formatar_data(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def formatar_competencia(d: date) -> str:
    return d.strftime("%m/%Y")


def mais_dias(d: date, dias: int) -> date:
    return d + timedelta(days=dias)
