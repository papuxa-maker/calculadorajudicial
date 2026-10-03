"""Evolução de uma parcela no tempo: estado e linhas da memória.

Modelo (o mesmo das tabelas do TJSP, do Manual do CJF e do PJe-Calc), com o
estado separado em compartimentos:

- C    = principal corrigido;
- Jant = juros de períodos anteriores (já vencidos; só são corrigidos);
- Jper = juros do período corrente (desde o início dele).

Em cada período, a partir de uma "origem" o (início do período ou data do
último abatimento), com K = fator de correção e G = juros acumulados de o até t:

    C(t)    = C_o × K
    Jant(t) = Jant_o × K
    Jper(t) = Jper_o × K + C(t) × G                juros simples (sem anatocismo)
    Jper(t) = Jper_o × K × (1 + G) + C(t) × G      juros compostos (TR, "composto")

SELIC embutida (``tipo_juros='embutido'``): não há outro índice nem juros; o
acréscimo da SELIC é "juros SELIC" (como no Manual CJF e no PJe-Calc) e incide
por soma simples só sobre a base consolidada — principal (+ juros dos períodos
anteriores, se ``selic_sobre_juros``) —, nunca sobre a própria SELIC:

    C(t) = C_o;  Jant(t) = Jant_o;  Jper(t) = Jper_o + (C_o [+ Jant_o]) × S(o, t)

Com ``selic_como='correcao'`` (leitura literal do SPEC) o acréscimo vai para o
principal: C(t) = C_o × (1 + S), Jant(t) = Jant_o × (1 + S) se consolidado.

Na troca de período, Jper passa para Jant. Num abatimento, os juros pagos saem
de Jant e Jper na proporção dos saldos, o capital pago sai de C, e o estado vira
a nova origem — dividir um período ou pagar R$ 0,01 não altera o resultado
além do valor pago. Sem abatimentos, o total é C_final × (1 + Σ juros dos
períodos), como nas tabelas práticas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from calculo_judicial.datas import formatar_data, primeiro_dia, proximo_mes
from calculo_judicial.decimais import CEM, CONTEXTO, UM, ZERO, formatar_decimal_br, q10
from calculo_judicial.indices.modelo import RepositorioIndices
from calculo_judicial.motor import juros as mod_juros
from calculo_judicial.motor.correcao import ItemMes, detalhar, fator_de_itens, fracoes_meses
from calculo_judicial.motor.memoria import LinhaMemoria, MemoriaParcela, PeriodoAplicado
from calculo_judicial.motor.parcela import Parametros, Parcela


@dataclass
class _Acumulador:
    """Fator acumulado incremental de uma lista de meses a partir de uma origem."""

    itens: list[ItemMes]
    capitalizacao: str
    posicao: int = 0
    produto: Decimal = UM
    soma: Decimal = ZERO

    def ate(self, limite: date) -> Decimal:
        """Fator dos itens com mês anterior a ``limite`` (avança o ponteiro)."""
        with localcontext(CONTEXTO):
            while self.posicao < len(self.itens) and self.itens[self.posicao].mes < limite:
                item = self.itens[self.posicao]
                if self.capitalizacao == "simples":
                    self.soma += item.taxa_pct * item.fracao / CEM
                else:
                    base = UM + item.taxa_pct / CEM
                    self.produto = q10(self.produto * (base if item.fracao == UM else (item.fracao * base.ln()).exp()))
                self.posicao += 1
            return q10(UM + self.soma) if self.capitalizacao == "simples" else self.produto


@dataclass
class _Linha:
    competencia: date
    fim: date


@dataclass
class _Estado:
    C: Decimal
    Jant: Decimal
    Jper: Decimal
    F: Decimal

    @property
    def J(self) -> Decimal:
        return self.Jant + self.Jper


class SimuladorParcela:
    """Simula uma parcela ao longo dos seus períodos (já normalizados)."""

    def __init__(
        self,
        parcela: Parcela,
        periodos: list[PeriodoAplicado],
        params: Parametros,
        repo: RepositorioIndices,
        registrar_item=None,
    ):
        self.parcela = parcela
        self.periodos = periodos
        self.params = params
        self.repo = repo
        self._registrar_item = registrar_item or (lambda item: None)
        self.linhas: list[LinhaMemoria] = []
        self.avisos: list[str] = []
        self.capital_pago = ZERO
        self.juros_pago = ZERO
        valor = q10(parcela.valor_original)
        self.inicio: date | None = periodos[0].inicio if periodos else None
        self.t: date | None = None  # instante até onde a parcela foi simulada (None = ainda não começou)
        self._idx = 0
        self.estado = _Estado(valor, ZERO, ZERO, UM)
        self._origem: date | None = None
        self._estado_origem = _Estado(valor, ZERO, ZERO, UM)
        self._acc: dict[str, _Acumulador] = {}
        self._exibicao: dict[str, dict[date, ItemMes]] = {}
        self._linhas_pendentes: list[_Linha] = []
        self._observacao_pendente = ""

    # ------------------------------------------------------------------ utilidades

    @property
    def C(self) -> Decimal:
        return self.estado.C

    @property
    def J(self) -> Decimal:
        return self.estado.J

    @property
    def F(self) -> Decimal:
        return self.estado.F

    @property
    def ativa(self) -> bool:
        """A parcela já existe (o tempo simulado alcançou o início do primeiro período)."""
        return self.inicio is not None and self.t is not None and self.t >= self.inicio

    def saldo(self) -> Decimal:
        return self.C + self.J if self.ativa else ZERO

    def _periodo_atual(self) -> PeriodoAplicado | None:
        return self.periodos[self._idx] if self._idx < len(self.periodos) else None

    def _itens(self, indice: str, de: date, ate: date, pro_rata: bool | None = None) -> list[ItemMes]:
        pro_rata = self.params.pro_rata if pro_rata is None else pro_rata
        itens = detalhar(indice, de, ate, self.repo, pro_rata, self.params.permitir_projecao)
        for item in itens:
            self._registrar_item(item)
        return itens

    def _pro_rata(self, pa: PeriodoAplicado) -> bool:
        """Pró-rata do período. Com acréscimo final (tributário: SELIC até o mês anterior ao da
        restituição + 1% no mês dela — Lei 9.250/95, art. 39, §4º), a SELIC é sempre por mês cheio."""
        return False if pa.periodo.acrescimo_final_pct else self.params.pro_rata

    def _cap_juros(self, pa: PeriodoAplicado) -> str:
        p = pa.periodo
        if p.tipo_juros == "composto":
            return "composta"
        if p.tipo_juros == "simples":
            return "simples"
        return p.capitalizacao_juros or mod_juros.capitalizacao_padrao(p.indice_juros or "")

    def _capitaliza(self, pa: PeriodoAplicado) -> bool:
        return pa.periodo.tipo_juros in ("composto", "indice", "taxa_legal") and self._cap_juros(pa) == "composta"

    def _periodo_anterior_capitaliza(self, pa: PeriodoAplicado) -> bool:
        i = self.periodos.index(pa)
        if i == 0:
            return False
        anterior = self.periodos[i - 1]
        return anterior.fim_exclusivo == pa.inicio and self._capitaliza(anterior)

    def _indice_juros(self, pa: PeriodoAplicado) -> str | None:
        p = pa.periodo
        if p.tipo_juros == "taxa_legal":
            return p.indice_juros or "TAXA_LEGAL"
        if p.tipo_juros == "indice":
            return p.indice_juros
        return None

    # ------------------------------------------------------------------ períodos e origens

    def _entrar_periodo(self, pa: PeriodoAplicado, origem: date) -> None:
        """Fixa a origem (início do período ou data de abatimento) e prepara os acumuladores."""
        if origem == pa.inicio:
            # troca de período: os juros do período anterior passam a ser "juros anteriores",
            # salvo quando os dois períodos capitalizam juros (a capitalização continua)
            e = self.estado
            if not (self._capitaliza(pa) and self._periodo_anterior_capitaliza(pa)):
                self.estado = _Estado(e.C, q10(e.Jant + e.Jper), ZERO, e.F)
            self._linhas_pendentes = self._fins_de_linha(pa)
            self._exibicao = self._itens_exibicao(pa)
        self._origem = origem
        self._estado_origem = _Estado(self.estado.C, self.estado.Jant, self.estado.Jper, self.estado.F)
        p = pa.periodo
        self._acc = {}
        if p.tipo_juros == "embutido":
            self._acc["selic"] = _Acumulador(
                self._itens(p.indice_correcao or "SELIC", origem, pa.fim_exclusivo, self._pro_rata(pa)), p.capitalizacao_correcao
            )
        else:
            if p.indice_correcao:
                self._acc["correcao"] = _Acumulador(self._itens(p.indice_correcao, origem, pa.fim_exclusivo), p.capitalizacao_correcao)
            ij = self._indice_juros(pa)
            if ij:
                self._acc["juros"] = _Acumulador(self._itens(ij, origem, pa.fim_exclusivo), self._cap_juros(pa))

    def _itens_exibicao(self, pa: PeriodoAplicado) -> dict[str, dict[date, ItemMes]]:
        """Taxas do período inteiro (do início ao fim), para exibir nas linhas mesmo após abatimentos."""
        p = pa.periodo
        exib: dict[str, dict[date, ItemMes]] = {}
        if p.tipo_juros == "embutido":
            exib["selic"] = {i.mes: i for i in self._itens(p.indice_correcao or "SELIC", pa.inicio, pa.fim_exclusivo, self._pro_rata(pa))}
        else:
            if p.indice_correcao:
                exib["correcao"] = {i.mes: i for i in self._itens(p.indice_correcao, pa.inicio, pa.fim_exclusivo)}
            ij = self._indice_juros(pa)
            if ij:
                exib["juros"] = {i.mes: i for i in self._itens(ij, pa.inicio, pa.fim_exclusivo)}
        return exib

    def _fins_de_linha(self, pa: PeriodoAplicado) -> list[_Linha]:
        linhas = [
            _Linha(mes, min(proximo_mes(mes), pa.fim_exclusivo))
            for mes, _ in fracoes_meses(pa.inicio, pa.fim_exclusivo, self._pro_rata(pa))
        ]
        if not linhas or linhas[-1].fim < pa.fim_exclusivo:
            linhas.append(_Linha(primeiro_dia(pa.fim_exclusivo), pa.fim_exclusivo))  # fecho do período
        return linhas

    # ------------------------------------------------------------------ cálculo do estado

    def _G_simples(self, pa: PeriodoAplicado, de: date, ate: date) -> Decimal:
        p = pa.periodo
        taxa = p.taxa_juros_am or ZERO
        with localcontext(CONTEXTO):
            return q10(
                mod_juros.pct_simples(taxa, pa.inicio, ate, self.params.pro_rata)
                - mod_juros.pct_simples(taxa, pa.inicio, de, self.params.pro_rata)
            )

    def _estado_em(self, pa: PeriodoAplicado, e: date, incremental: bool) -> _Estado:
        """Estado no instante ``e`` dentro do período ``pa``, a partir da origem atual."""
        o = self._origem
        assert o is not None
        p = pa.periodo
        eo = self._estado_origem
        acrescimo_no_fim = bool(p.tipo_juros == "embutido" and p.acrescimo_final_pct and e >= pa.fim_exclusivo)
        if e <= o and not acrescimo_no_fim:
            return _Estado(eo.C, eo.Jant, eo.Jper, eo.F)

        def fator(chave: str, indice: str | None, cap: str) -> Decimal:
            if e <= o:
                return UM
            if incremental:
                return self._acc[chave].ate(e)
            return fator_de_itens(self._itens(indice, o, e, self._pro_rata(pa)), cap)

        with localcontext(CONTEXTO):
            if p.tipo_juros == "embutido":
                S = fator("selic", p.indice_correcao or "SELIC", p.capitalizacao_correcao) - UM
                if acrescimo_no_fim:
                    S += p.acrescimo_final_pct / CEM
                S = q10(S)
                if self.params.selic_como == "correcao":
                    C = q10(eo.C * (UM + S))
                    Jant = q10(eo.Jant * (UM + S)) if p.selic_sobre_juros else eo.Jant
                    return _Estado(C, Jant, eo.Jper, q10(eo.F * (UM + S)))
                base = eo.C + (eo.Jant if p.selic_sobre_juros else ZERO)
                return _Estado(eo.C, eo.Jant, q10(eo.Jper + base * S), eo.F)
            K = fator("correcao", p.indice_correcao, p.capitalizacao_correcao) if p.indice_correcao else UM
            if p.tipo_juros == "nenhum":
                G = ZERO
            elif p.tipo_juros == "simples":
                G = self._G_simples(pa, o, e)
            elif p.tipo_juros == "composto":
                G = mod_juros.pct_composto(p.taxa_juros_am or ZERO, o, e, self.params.pro_rata)
            else:
                G = fator("juros", self._indice_juros(pa), self._cap_juros(pa)) - UM
            C = q10(eo.C * K)
            Jant = q10(eo.Jant * K)
            if self._cap_juros(pa) == "composta" and p.tipo_juros != "nenhum":
                Jper = q10(eo.Jper * K * (UM + G) + C * G)
            else:
                Jper = q10(eo.Jper * K + C * G)
            return _Estado(C, Jant, Jper, q10(eo.F * K))

    # ------------------------------------------------------------------ linhas

    def _taxas_exibidas(self, pa: PeriodoAplicado, linha: _Linha, inicio_linha: date):
        """Taxa do índice e taxa de juros do mês, para exibição (independem da origem)."""
        p = pa.periodo
        taxa_indice = None
        fracao = UM
        indice_origem = None
        projecao = False
        taxa_juros = ZERO
        if p.tipo_juros == "embutido":
            item = self._exibicao.get("selic", {}).get(linha.competencia)
            acrescimo = p.acrescimo_final_pct if (p.acrescimo_final_pct and linha.fim >= pa.fim_exclusivo) else ZERO
            selic_mes = q10(item.taxa_pct * item.fracao) if item else ZERO
            if item:
                fracao, indice_origem, projecao = item.fracao, item.indice_origem, item.projecao
            if self.params.selic_como == "correcao":
                taxa_indice = (item.taxa_pct if item else ZERO) + acrescimo if (item or acrescimo) else None
            else:
                taxa_juros = selic_mes + acrescimo
            return taxa_indice, q10(fracao), indice_origem, projecao, taxa_juros
        item = self._exibicao.get("correcao", {}).get(linha.competencia)
        if item:
            taxa_indice, fracao, indice_origem, projecao = item.taxa_pct, item.fracao, item.indice_origem, item.projecao
        if p.tipo_juros == "simples":
            taxa_juros = q10(self._G_simples(pa, inicio_linha, linha.fim) * CEM)
        elif p.tipo_juros == "composto":
            with localcontext(CONTEXTO):
                taxa_juros = q10(
                    (mod_juros.pct_composto(p.taxa_juros_am or ZERO, pa.inicio, linha.fim, self.params.pro_rata)
                     - mod_juros.pct_composto(p.taxa_juros_am or ZERO, pa.inicio, inicio_linha, self.params.pro_rata)) * CEM
                )
        else:
            item_j = self._exibicao.get("juros", {}).get(linha.competencia)
            if item_j:
                taxa_juros = q10(item_j.taxa_pct * item_j.fracao)
                projecao = projecao or item_j.projecao
        return taxa_indice, q10(fracao), indice_origem, projecao, taxa_juros

    def _nova_linha(self, pa: PeriodoAplicado | None, competencia: date, fim: date, taxas, observacao: str,
                    tipo: str | None = None) -> LinhaMemoria:
        taxa_indice, fracao, origem, projecao, taxa_juros = taxas
        p = pa.periodo if pa else None
        with localcontext(CONTEXTO):
            base = q10(self.C / self.F) if self.F else self.C
        if p is None:
            indice = None
        elif p.tipo_juros == "embutido":
            indice = p.indice_correcao or "SELIC"
        else:
            indice = p.indice_correcao
        return LinhaMemoria(
            competencia=competencia,
            fim=fim,
            periodo=pa.numero if pa else (self.linhas[-1].periodo if self.linhas else 0),
            indice=indice,
            taxa_indice_pct=taxa_indice,
            fracao=fracao,
            fator_acumulado=self.F,
            base=base,
            corrigido=self.C,
            tipo_juros=tipo or (p.tipo_juros if p else "nenhum"),
            taxa_juros_pct=taxa_juros,
            juros_acumulados=self.J,
            subtotal=self.C + self.J,
            projecao=projecao,
            indice_origem=origem,
            observacao=observacao,
        )

    def _emitir(self, pa: PeriodoAplicado, linha: _Linha, inicio_linha: date) -> None:
        taxas = self._taxas_exibidas(pa, linha, inicio_linha)
        mudou = not self.linhas or self.linhas[-1].subtotal != self.C + self.J or self.linhas[-1].periodo != pa.numero
        eh_fecho = linha.competencia == primeiro_dia(pa.fim_exclusivo) and linha.fim == pa.fim_exclusivo and (
            not self._pro_rata(pa)
        )
        if eh_fecho and not mudou and not self._observacao_pendente:
            return
        observacao = self._observacao_pendente
        p = pa.periodo
        if p.tipo_juros == "embutido" and p.acrescimo_final_pct and linha.fim >= pa.fim_exclusivo:
            # o acréscimo do mês do cálculo (tributário: 1%, Lei 9.250/95, art. 39, §4º) é somado à SELIC desta linha
            onde = "índice do mês inclui" if self.params.selic_como == "correcao" else "juros do mês incluem"
            observacao = (observacao + "; " if observacao else "") + (
                f"{onde} + {formatar_decimal_br(p.acrescimo_final_pct, 2).rstrip('0').rstrip(',')}% do mês do cálculo")
        if eh_fecho and linha.fim != proximo_mes(linha.competencia):
            observacao = (observacao + "; " if observacao else "") + f"até {formatar_data(linha.fim)} (dias sem índice do mês)"
        self._observacao_pendente = ""
        self.linhas.append(self._nova_linha(pa, linha.competencia, linha.fim, taxas, observacao))

    # ------------------------------------------------------------------ avanço no tempo

    def avancar(self, t: date) -> None:
        """Leva a parcela até o instante ``t`` (exclusivo no sentido "até B"), emitindo as linhas completas."""
        if self.inicio is None:
            self.t = t if self.t is None else max(self.t, t)
            return
        if self.t is None:
            if t < self.inicio:
                return  # a parcela ainda não existe
            self.t = self.inicio
            self._entrar_periodo(self.periodos[0], self.inicio)
        while self.t < t:
            pa = self._periodo_atual()
            if pa is None:
                self.t = t
                break
            if self.t < pa.inicio:  # lacuna entre períodos: estado congelado
                self.t = min(pa.inicio, t)
                if self.t == pa.inicio:
                    self._entrar_periodo(pa, pa.inicio)
                continue
            alvo = min(t, pa.fim_exclusivo)
            while self._linhas_pendentes and self._linhas_pendentes[0].fim <= alvo:
                linha = self._linhas_pendentes.pop(0)
                inicio_linha = max(linha.competencia, pa.inicio)
                self.estado = self._estado_em(pa, linha.fim, incremental=True)
                self.t = linha.fim
                self._emitir(pa, linha, inicio_linha)
            if self.t < alvo:
                self.estado = self._estado_em(pa, alvo, incremental=False)
                self.t = alvo
            if self.t >= pa.fim_exclusivo:
                self._idx += 1
                proximo = self._periodo_atual()
                if proximo is not None and proximo.inicio <= self.t:
                    self._entrar_periodo(proximo, proximo.inicio)
        self._fechar_periodos_instantaneos(t)

    def _fechar_periodos_instantaneos(self, t: date) -> None:
        """Período de duração zero na data ``t`` (só o acréscimo final, ex.: 1% do mês da restituição)."""
        pa = self._periodo_atual()
        while pa is not None and pa.inicio == pa.fim_exclusivo == self.t == t:
            if self._origem != pa.inicio:
                self._entrar_periodo(pa, pa.inicio)
            self.estado = self._estado_em(pa, pa.fim_exclusivo, incremental=False)
            for linha in self._linhas_pendentes:
                self._emitir(pa, linha, pa.inicio)
            self._linhas_pendentes = []
            self._idx += 1
            pa = self._periodo_atual()

    def aplicar_pagamento(self, d: date, juros: Decimal, capital: Decimal, descricao: str) -> None:
        """Abate ``juros`` e ``capital`` no instante ``d`` (o estado já deve estar em ``d``).

        Os juros pagos saem dos juros anteriores e dos juros do período na proporção dos
        saldos (todos são juros vencidos — CC, art. 354); o capital pago sai do principal.
        """
        if not self.ativa or self.t != d:
            raise RuntimeError(f"{self.parcela.descricao}: abatimento em {d} aplicado a parcela fora da data simulada")
        e = self.estado
        with localcontext(CONTEXTO):
            total_juros = e.Jant + e.Jper
            if juros and total_juros > 0:
                tira_ant = q10(juros * e.Jant / total_juros)
                tira_per = juros - tira_ant
            else:
                tira_ant = tira_per = ZERO
            self.estado = _Estado(q10(e.C - capital), q10(e.Jant - tira_ant), q10(e.Jper - tira_per), e.F)
        self.juros_pago += juros
        self.capital_pago += capital
        if juros or capital:
            self._observacao_pendente = (
                (self._observacao_pendente + "; " if self._observacao_pendente else "")
                + f"abatimento em {formatar_data(d)} ({descricao})"
            )
        pa = self._periodo_atual()
        if pa is not None and pa.inicio <= d < pa.fim_exclusivo:
            self._entrar_periodo(pa, d)
        else:
            self._estado_origem = _Estado(self.estado.C, self.estado.Jant, self.estado.Jper, self.estado.F)

    # ------------------------------------------------------------------ atualização sem juros (multa 523)

    def fator_atualizacao(self, de: date, ate: date, indice_nos_periodos_selic: str) -> Decimal:
        """Fator de atualização monetária (sem juros) de ``de`` a ``ate`` pelos critérios da parcela.

        Nos períodos de SELIC embutida, que engloba juros, usa ``indice_nos_periodos_selic``
        (índice de correção pura da área) para não pôr juros na multa/honorários do art. 523.
        """
        fator = UM
        with localcontext(CONTEXTO):
            for pa in self.periodos:
                a = max(de, pa.inicio)
                b = min(ate, pa.fim_exclusivo)
                if a >= b:
                    continue
                p = pa.periodo
                if p.tipo_juros == "embutido":
                    fator = q10(fator * fator_de_itens(self._itens(indice_nos_periodos_selic, a, b), "composta"))
                elif p.indice_correcao:
                    fator = q10(fator * fator_de_itens(self._itens(p.indice_correcao, a, b), p.capitalizacao_correcao))
        return fator

    # ------------------------------------------------------------------ resultado

    def finalizar(self) -> MemoriaParcela:
        valor = q10(self.parcela.valor_original)
        if not self.ativa:
            C, J = valor, ZERO
        else:
            C, J = self.C, self.J
            if self._observacao_pendente or (self.linhas and self.linhas[-1].subtotal != C + J):
                # abatimento na data do cálculo ou depois do último período: a memória fecha no total
                ultima = self.linhas[-1] if self.linhas else None
                competencia = primeiro_dia(self.t) if self.t else (ultima.competencia if ultima else self.inicio)
                self.linhas.append(
                    self._nova_linha(self._periodo_atual(), competencia, self.t, (None, UM, None, False, ZERO),
                                     self._observacao_pendente or "saldo final")
                )
                self._observacao_pendente = ""
            if self.params.preservar_valor_nominal and self.F < UM:
                C, J = self._preservar_nominal(C, J)
        return MemoriaParcela(
            parcela=self.parcela,
            periodos=self.periodos,
            linhas=self.linhas,
            valor_original=valor,
            principal_corrigido=C,
            juros=J,
            capital_pago=self.capital_pago,
            juros_pago=self.juros_pago,
            avisos=self.avisos,
        )

    def _preservar_nominal(self, C: Decimal, J: Decimal) -> tuple[Decimal, Decimal]:
        """Deflação acumulada: prevalece o valor nominal do principal (STJ, REsp 1.265.580; Manual CJF 4.1.2.2).

        Sem abatimentos tudo é proporcional ao valor da parcela, então basta refazer o
        cálculo com fator de correção 1: C e J são divididos pelo fator acumulado (os juros
        passam a incidir sobre o nominal preservado). Com abatimentos o piso vale só para o
        principal remanescente.
        """
        with localcontext(CONTEXTO):
            if self.capital_pago or self.juros_pago:
                nominal = q10(C / self.F)
                novo_C, novo_J = nominal, J
                nota = "piso aplicado ao principal remanescente (houve abatimento)"
            else:
                novo_C, novo_J = q10(C / self.F), q10(J / self.F)
                nota = "correção considerada igual a 1; juros sobre o valor nominal"
        self.avisos.append(
            f"{self.parcela.descricao}: a correção acumulada ficou negativa (deflação); prevalece o valor nominal "
            f"do principal (STJ, REsp 1.265.580; Manual CJF 4.1.2.2) — {nota}"
        )
        ultima = self.linhas[-1] if self.linhas else None
        if ultima is not None:
            self.linhas.append(
                LinhaMemoria(
                    competencia=ultima.competencia, fim=ultima.fim, periodo=ultima.periodo, indice=None,
                    taxa_indice_pct=None, fracao=UM, fator_acumulado=UM, base=novo_C, corrigido=novo_C,
                    tipo_juros="ajuste", taxa_juros_pct=ZERO, juros_acumulados=novo_J, subtotal=novo_C + novo_J,
                    observacao=f"preservação do valor nominal (deflação acumulada) — {nota}",
                )
            )
        return novo_C, novo_J
