"""Memória de cálculo em PDF (SPEC, seção 8) com ReportLab.

A4 retrato, fonte sem serifa (Helvetica) 10 pt e tabelas em 9 pt (a memória
analítica usa 8 pt para caber nas margens). Seções: cabeçalho, Resumo, Critérios
adotados, Memória analítica (uma tabela por parcela, cabeçalho repetido em cada
página, última linha em negrito), Abatimentos, Fontes dos índices e Ressalva com
campo de assinatura. Rodapé: "Página X de Y" e o nome do arquivo.
"""

from __future__ import annotations

import io
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Image, KeepTogether, LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from calculo_judicial.datas import formatar_data
from calculo_judicial.decimais import arredondar_exibicao, formatar_brl
from calculo_judicial.motor.memoria import Memoria
from calculo_judicial.relatorio.tabelas import tabelas_da_memoria

MARGEM = 1.5 * cm
LARGURA_UTIL = A4[0] - 2 * MARGEM
COR_CABECALHO = colors.HexColor("#E8EDF3")
COR_DESTAQUE = colors.HexColor("#FFF3CD")
COLUNAS_PDF = ["Competência", "Período e índice", "Valor base", "Índice %", "Fator acumulado", "Valor corrigido", "Juros %",
               "Juros acumulados", "Subtotal"]
LARGURAS_ANALITICA = [2.0, 1.9, 2.3, 1.5, 1.8, 2.4, 1.5, 2.4, 2.2]  # cm — soma 18 cm (largura útil)
PADDING = 6  # pt, padding lateral padrão das células do ReportLab (esquerda + direita)
FONTE_MINIMA = 5.5


def _estilos():
    base = getSampleStyleSheet()
    return {
        "normal": ParagraphStyle("normal", parent=base["Normal"], fontName="Helvetica", fontSize=10, leading=12.5),
        "pequeno": ParagraphStyle("pequeno", parent=base["Normal"], fontName="Helvetica", fontSize=9, leading=11),
        "recuado": ParagraphStyle("recuado", parent=base["Normal"], fontName="Helvetica", fontSize=9, leading=11, leftIndent=12,
                                  spaceAfter=1),
        "negrito": ParagraphStyle("negrito", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=11,
                                  spaceBefore=4),
        "celula": ParagraphStyle("celula", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=9.5),
        "cabecalho": ParagraphStyle("cabecalho", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7, leading=8),
        "nota": ParagraphStyle("nota", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8, leading=10,
                               spaceBefore=3, textColor=colors.HexColor("#444444")),
        "titulo": ParagraphStyle("titulo", parent=base["Title"], fontName="Helvetica-Bold", fontSize=15, leading=18, spaceAfter=4),
        "secao": ParagraphStyle("secao", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=14,
                                spaceBefore=10, spaceAfter=5, keepWithNext=1),
        "subsecao": ParagraphStyle("subsecao", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10, leading=12,
                                   spaceBefore=6, spaceAfter=3, keepWithNext=1),
        "secao_solta": ParagraphStyle("secao_solta", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5,
                                      leading=14, spaceBefore=10, spaceAfter=5),
        # título da parcela na memória analítica: sem keepWithNext (a tabela longa iria inteira para a página seguinte)
        "parcela": ParagraphStyle("parcela", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10, leading=12,
                                  spaceBefore=6, spaceAfter=3),
        "escritorio": ParagraphStyle("escritorio", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=11, leading=13),
        "centro": ParagraphStyle("centro", parent=base["Normal"], fontName="Helvetica", fontSize=10, alignment=TA_CENTER),
    }


def _p(texto: str, estilo) -> Paragraph:
    return Paragraph(escape(str(texto)).replace("\n", "<br/>"), estilo)


class _CanvasNumerado(rl_canvas.Canvas):
    """Canvas em duas passagens para escrever "Página X de Y"."""

    def __init__(self, *args, nome_arquivo: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._paginas = []
        self._nome_arquivo = nome_arquivo

    def showPage(self):  # noqa: N802 - API do ReportLab
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.grey)
            self.drawString(MARGEM, 0.9 * cm, self._nome_arquivo)
            self.drawRightString(A4[0] - MARGEM, 0.9 * cm, f"Página {self._pageNumber} de {total}")
            super().showPage()
        super().save()


def _tabela(dados, larguras, estilo_extra=None, repetir=1, alinhar_direita_desde=1, negrito_ultima=False) -> LongTable:
    t = LongTable(dados, colWidths=larguras, repeatRows=repetir)
    corpo = tamanho_que_cabe(dados, larguras, tamanho=9)
    estilo = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", corpo),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", corpo),
        ("BACKGROUND", (0, 0), (-1, 0), COR_CABECALHO),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C2CC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if alinhar_direita_desde is not None:
        estilo.append(("ALIGN", (alinhar_direita_desde, 1), (-1, -1), "RIGHT"))
    if negrito_ultima:
        estilo.append(("FONT", (0, -1), (-1, -1), "Helvetica-Bold", corpo))
        estilo.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F4F6F8")))
    if estilo_extra:
        estilo.extend(estilo_extra)
    t.setStyle(TableStyle(estilo))
    return t


def tamanho_que_cabe(dados: list[list], larguras: list[float], fonte: str = "Helvetica-Bold", tamanho: float = 8) -> float:
    """Maior corpo de fonte (até ``tamanho``) em que todo texto simples das células cabe na sua coluna.

    Valores muito grandes (ex.: R$ 100.000.000,00) reduzem o corpo da tabela em vez
    de invadir a coluna vizinha. Calculado com a fonte em negrito (a mais larga).
    """
    corpo = tamanho
    for linha in dados:
        for texto, largura in zip(linha, larguras):
            if not isinstance(texto, str) or not texto:
                continue
            necessario = stringWidth(texto, fonte, 1)
            if necessario:
                corpo = min(corpo, (largura - PADDING) / necessario)
    return max(FONTE_MINIMA, int(corpo * 10) / 10)


def _cabecalho(memoria: Memoria, config, processo: dict, e) -> list:
    elementos = []
    blocos = []
    logo = getattr(config, "logo", "") if config else ""
    if logo and Path(logo).exists():
        try:
            img = Image(logo)
            proporcao = img.imageHeight / float(img.imageWidth or 1)
            img.drawWidth = 3.0 * cm
            img.drawHeight = 3.0 * cm * proporcao
            if img.drawHeight > 2.2 * cm:
                img.drawHeight = 2.2 * cm
                img.drawWidth = 2.2 * cm / proporcao
            blocos.append(img)
        except Exception:  # noqa: BLE001 - logo inválido não impede o relatório
            blocos.append("")
    texto = []
    if config and getattr(config, "nome_escritorio", ""):
        texto.append(_p(config.nome_escritorio, e["escritorio"]))
    if config and getattr(config, "oab", ""):
        texto.append(_p(config.oab, e["pequeno"]))
    if config and getattr(config, "endereco", ""):
        texto.append(_p(config.endereco, e["pequeno"]))
    if blocos or texto:
        t = Table([[blocos[0] if blocos else "", texto]], colWidths=[3.4 * cm, LARGURA_UTIL - 3.4 * cm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        elementos.append(t)
        elementos.append(Spacer(1, 6))
    elementos.append(_p("Memória de Cálculo", e["titulo"]))
    dados = []
    if processo.get("numero_cnj"):
        dados.append(("Processo", processo["numero_cnj"]))
    if processo.get("vara"):
        dados.append(("Vara/tribunal", processo["vara"]))
    if processo.get("autor") or processo.get("reu"):
        dados.append(("Partes", f"{processo.get('autor', '')} × {processo.get('reu', '')}".strip(" ×")))
    if processo.get("tipo"):
        dados.append(("Tipo", processo["tipo"]))
    dados.append(("Data do cálculo", formatar_data(memoria.parametros.data_calculo)))
    t = Table([[_p(a, e["pequeno"]), _p(b, e["normal"])] for a, b in dados], colWidths=[3.4 * cm, LARGURA_UTIL - 3.4 * cm])
    t.setStyle(TableStyle([("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1)]))
    elementos.append(t)
    return elementos


def _resumo(memoria: Memoria, tabelas, e) -> list:
    dados = [["Item", "Valor"]] + [[nome, valor] for nome, valor in tabelas.resumo]
    t = _tabela(dados, [9 * cm, 5 * cm], negrito_ultima=True)
    elementos = [_p("Resumo", e["secao"]), t]
    if memoria.resumo.saldo_credor:
        elementos.append(_p(f"Saldo credor (pagamento acima do devido): {formatar_brl(memoria.resumo.saldo_credor)}", e["pequeno"]))
    for nota in notas_do_resumo(memoria):
        elementos.append(_p(nota, e["nota"]))
    return elementos


def notas_do_resumo(memoria: Memoria) -> list[str]:
    """Explicações que evitam leituras erradas do resumo (as mesmas no PDF e no Excel)."""
    notas = []
    linhas = memoria.resumo.linhas()
    soma = sum((arredondar_exibicao(v) for _, v in linhas[:-1]), arredondar_exibicao(linhas[0][1]) * 0)
    if soma != arredondar_exibicao(memoria.resumo.total):
        notas.append("Os valores são calculados com 10 casas decimais e arredondados só na exibição; por isso a soma das "
                     "linhas arredondadas pode diferir do total em alguns centavos.")
    embutida = any(l.tipo_juros == "embutido" for mp in memoria.parcelas for l in mp.linhas)
    if embutida and memoria.parametros.selic_como == "juros":
        notas.append("Nos períodos de SELIC, a taxa engloba correção monetária e juros e aparece em \"Juros de mora\" "
                     "(por isso a correção monetária pode ser pequena ou zero).")
    if memoria.abatimentos:
        notas.append("Com pagamentos: a correção e os juros do resumo são os do período todo, inclusive a parte já quitada; "
                     "o pagamento aparece em \"Abatimentos\" e a imputação (juros e capital) está na seção Abatimentos. "
                     "Na memória analítica, cada parcela mostra o saldo depois dos pagamentos.")
    return notas


def _criterios(memoria: Memoria, e) -> list:
    elementos = [_p("Critérios adotados", e["secao"])]
    for linha in memoria.criterios:
        if linha.lstrip().startswith(("Período ", "Marcos:", "Meses de mudança")):  # recuados sob o nome da parcela
            elementos.append(_p(f"- {linha.strip()}", e["recuado"]))
        elif linha.endswith(":"):
            elementos.append(_p(linha, e["negrito"]))
        else:
            elementos.append(_p(linha, e["pequeno"]))
    if memoria.acessorios.detalhes:
        elementos.append(_p("Multa e honorários: como foram calculados", e["subsecao"]))
        for detalhe in memoria.acessorios.detalhes:
            elementos.append(_p(f"- {detalhe}", e["recuado"]))
    if memoria.avisos:
        elementos.append(_p("Observações", e["subsecao"]))
        for aviso in memoria.avisos:
            elementos.append(_p(f"- {aviso}", e["pequeno"]))
    elementos.append(_p("Parâmetros que alteram o resultado", e["subsecao"]))
    dados = [["Parâmetro", "Valor"]] + [[_p(p.descricao, e["celula"]), _p(p.valor, e["celula"])]
                                        for p in memoria.parametros_relevantes if p.aplica]
    elementos.append(_tabela(dados, [9.5 * cm, LARGURA_UTIL - 9.5 * cm], alinhar_direita_desde=None))
    return elementos


def _analitica(memoria: Memoria, tabelas, e) -> list:
    elementos = [_p("Memória analítica", e["secao_solta"]),
                 _p("\"Período e índice\": número do período (ver Critérios adotados) e índice de correção do mês.", e["nota"])]
    larguras = [w * cm for w in LARGURAS_ANALITICA]
    for mp, parcela in zip(memoria.parcelas, tabelas.parcelas):
        elementos.append(_p(parcela["titulo"], e["parcela"]))
        dados = [[_p(c, e["cabecalho"]) for c in COLUNAS_PDF]]
        destaque = []
        for i, linha in enumerate(parcela["linhas"], start=1):
            competencia = linha["Competência"] + (" *" if linha["Obs."] else "")
            dados.append([competencia, linha["Período/índice"]] + [linha[c] for c in COLUNAS_PDF[2:]])
            if linha["Obs."]:
                destaque.append(("BACKGROUND", (0, i), (-1, i), COR_DESTAQUE))
        dados.append(["Total da parcela", "", "", "", "", parcela["principal_corrigido"].replace("R$ ", ""), "",
                      parcela["juros"].replace("R$ ", ""), parcela["total"].replace("R$ ", "")])
        # a linha de total tem o rótulo mesclado nas 5 primeiras colunas
        total = dados[-1]
        corpo = min(tamanho_que_cabe(dados[1:-1], larguras),
                    tamanho_que_cabe([[total[0]]], [sum(larguras[:5])]),
                    tamanho_que_cabe([total[5:]], larguras[5:]))
        t = _tabela(dados, larguras, estilo_extra=destaque + [("FONT", (0, 1), (-1, -2), "Helvetica", corpo),
                                                               ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", corpo),
                                                               ("ALIGN", (1, 1), (1, -1), "LEFT"),
                                                               ("SPAN", (0, -1), (4, -1))],
                    negrito_ultima=True)
        elementos.append(t)
        notas = [f"{l['Competência']}: {l['Obs.']}" for l in parcela["linhas"] if l["Obs."]]
        if notas:
            elementos.append(_p("* " + " | ".join(notas), e["celula"]))
    return elementos


def _abatimentos(tabelas, e) -> list:
    if not tabelas.abatimentos:
        return []
    cab = ["Data", "Descrição", "Saldo antes", "Valor abatido", "Imputação", "Saldo após"]
    dados = [cab] + [[a["Data"], _p(a["Descrição"], e["celula"]), a["Saldo antes"], a["Valor abatido"],
                      _p(f"{a['Imputação']} (juros {a['Juros']}; capital {a['Capital']})".replace("R$ ", "R$\u00a0"),
                         e["celula"]), a["Saldo após"]]
                     for a in tabelas.abatimentos]
    larguras = [1.9 * cm, 3.4 * cm, 2.8 * cm, 2.6 * cm, 4.5 * cm, 2.8 * cm]
    return [_p("Abatimentos", e["secao"]), _tabela(dados, larguras, alinhar_direita_desde=2)]


def _fontes(tabelas, e) -> list:
    cab = ["Índice", "Nome e fonte", "URL", "Coleta", "Meses"]
    dados = [cab]
    destaque = []
    for i, f in enumerate(tabelas.fontes, start=1):
        meses = f["Meses"] + (f" — PROJEÇÃO: {f['Projeção']}" if f["Projeção"] else "")
        dados.append([f["Índice"], _p(f"{f['Nome']} — {f['Órgão/fonte']}", e["celula"]), _p(f["URL"], e["celula"]),
                      f["Data de coleta"], _p(meses, e["celula"])])
        if f["Projeção"]:
            destaque.append(("BACKGROUND", (0, i), (-1, i), COR_DESTAQUE))
    larguras = [2.4 * cm, 6.2 * cm, 4.6 * cm, 1.9 * cm, 2.9 * cm]
    return [_p("Fontes dos índices", e["secao"]), _tabela(dados, larguras, estilo_extra=destaque, alinhar_direita_desde=None)]


def _ressalva(memoria: Memoria, config, e) -> list:
    texto = (getattr(config, "ressalva", "") if config else "") or "Valores calculados até {data}, sujeitos a atualização até o efetivo pagamento."
    texto = texto.replace("{data}", formatar_data(memoria.parametros.data_calculo))
    responsavel = " — ".join(x for x in (getattr(config, "nome_escritorio", ""), getattr(config, "oab", "")) if x) if config else ""
    assinatura = Table([["", ""], ["_" * 45, ""], [responsavel or "Responsável pelo cálculo", ""]],
                       colWidths=[9 * cm, LARGURA_UTIL - 9 * cm])
    assinatura.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("TOPPADDING", (0, 0), (-1, 0), 24)]))
    return [KeepTogether([_p("Ressalva", e["secao"]), _p(texto, e["normal"]), Spacer(1, 18), assinatura])]


def gerar_pdf(memoria: Memoria, config=None, processo: dict | None = None, nome_arquivo: str = "") -> bytes:
    """Gera o PDF da memória de cálculo e devolve os bytes."""
    processo = processo or {}
    e = _estilos()
    tabelas = tabelas_da_memoria(memoria)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGEM, rightMargin=MARGEM, topMargin=MARGEM, bottomMargin=1.6 * cm,
        title="Memória de Cálculo", author=getattr(config, "nome_escritorio", "") if config else "",
        subject=processo.get("numero_cnj", ""),
    )
    elementos = []
    elementos += _cabecalho(memoria, config, processo, e)
    elementos += _resumo(memoria, tabelas, e)
    elementos += _criterios(memoria, e)
    elementos += _analitica(memoria, tabelas, e)
    elementos += _abatimentos(tabelas, e)
    elementos += _fontes(tabelas, e)
    elementos += _ressalva(memoria, config, e)
    doc.build(elementos, canvasmaker=lambda *a, **k: _CanvasNumerado(*a, nome_arquivo=nome_arquivo, **k))
    return buffer.getvalue()
