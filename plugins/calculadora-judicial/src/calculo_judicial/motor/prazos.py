"""Contagem de prazo processual em dias úteis (art. 523 do CPC).

ESTIMATIVA: considera sábados, domingos, feriados nacionais (Lei 662/1949, Lei
6.802/1980, Lei 14.759/2023 — 20 de novembro desde 2024), segunda e terça de
Carnaval, Sexta-feira Santa, Corpus Christi, 8 de dezembro (Dia da Justiça) e a
suspensão de 20/12 a 20/01 (CPC, art. 220). Feriados locais e suspensões do
tribunal NÃO são conhecidos: a memória sempre avisa, e o usuário deve informar
``data_decurso_523`` quando houver diferença.

Contagem (CPC, arts. 219 e 224): exclui o dia do começo (a intimação) e inclui o
do vencimento; só dias úteis.
"""

from __future__ import annotations

from datetime import date, timedelta

PRAZO_ART_523 = 15


def pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def feriados(ano: int) -> set[date]:
    fixos = {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 8), (12, 25)}
    if ano >= 2024:
        fixos.add((11, 20))
    datas = {date(ano, m, d) for m, d in fixos}
    p = pascoa(ano)
    datas |= {p - timedelta(days=48), p - timedelta(days=47), p - timedelta(days=2), p + timedelta(days=60)}
    return datas


def suspenso(d: date) -> bool:
    """CPC, art. 220: prazos suspensos de 20 de dezembro a 20 de janeiro, inclusive."""
    return (d.month == 12 and d.day >= 20) or (d.month == 1 and d.day <= 20)


def eh_dia_util(d: date) -> bool:
    return d.weekday() < 5 and d not in feriados(d.year) and not suspenso(d)


def somar_dias_uteis(inicio: date, dias: int) -> date:
    """Último dia do prazo de ``dias`` úteis contado a partir de ``inicio`` (dia excluído)."""
    atual = inicio
    contados = 0
    while contados < dias:
        atual += timedelta(days=1)
        if eh_dia_util(atual):
            contados += 1
    return atual


def primeiro_dia_util(d: date) -> date:
    while not eh_dia_util(d):
        d += timedelta(days=1)
    return d


def data_decurso_523(data_intimacao: date) -> date:
    """Último dia dos 15 dias úteis para pagamento voluntário (CPC, art. 523, caput; art. 219).

    Intimação em dia não útil ou durante a suspensão de 20/12 a 20/01 considera-se
    feita no primeiro dia útil seguinte (Lei 11.419/2006, art. 5º, §2º; CPC, arts.
    220 e 224), e a contagem começa no dia útil posterior a ele.
    """
    return somar_dias_uteis(primeiro_dia_util(data_intimacao), PRAZO_ART_523)
