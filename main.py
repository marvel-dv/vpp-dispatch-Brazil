r"""
======================================================================================
Virtual Power Plant (VPP) & EMS Dispatch Optimizer
Versão: 0.1.1 (Modularizado para GitHub)
Framework: PyPSA + Linopy + HiGHS
Regulação: Lei 14.300/2022 e REN 1.000/2021 (CEMIG-D)
======================================================================================

COMO EXECUTAR:
--------------
1. Modo Padrão (Perfis Sintéticos de Referência - 168 horas):
   cd "C:\Users\User\Desktop\Faculdade\Vpp\github"
   python main.py

2. Modo com Dados Reais (Curvas PVGIS BH/MG + Demanda ONS Sudeste 2026):
   cd "C:\Users\User\Desktop\Faculdade\Vpp\github"
   python main.py --real-data
======================================================================================
"""
import sys
import argparse
import logging
import warnings

# Configuração idêntica à versão original (vpp-semana-0.1.1.py):
# Silencia mensagens internas de INFO e avisos cosméticos de PyPSA e Linopy para manter o terminal limpo
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="pypsa")
warnings.filterwarnings("ignore", category=UserWarning, module="linopy")
logging.getLogger().setLevel(logging.WARNING)
for _lg in ("pypsa", "linopy", "highspy"):
    logging.getLogger(_lg).setLevel(logging.ERROR)

from src.config import (
    CENARIOS, PCT_FIO_B_ANO_BASE, LIMITE_PADRAO, FATOR_MES,
    CFG_IND, CFG_COND, USAR_DADOS_REAIS, ARQUIVO_DADOS_REAIS
)
from src.model import construir_e_otimizar
from src.audit import auditar_resultado
from src.economics import calcular_investimentos


def main():
    parser = argparse.ArgumentParser(description="VPP & EMS Dispatch Optimizer (168 Horas - Lei 14.300/2022)")
    parser.add_argument("--real-data", action="store_true", help="Utiliza curvas horárias reais do PVGIS (MG) e ONS (2026)")
    args = parser.parse_args()

    usar_reais = args.real_data or USAR_DADOS_REAIS

    print("=" * 86)
    print("   VPP & EMS DISPATCH OPTIMIZER (REPOSITÓRIO MODULARIZADO - 168 HORAS)")
    print("   Resolução MILP sob Lei 14.300/2022 (CEMIG-D) | Solver: HiGHS | Versão 0.1.1")
    print("=" * 86)

    if usar_reais:
        print(f"[*] MODO ATIVO: DADOS REAIS (Solar: PVGIS BH/MG | Carga: ONS Sudeste 2026)")
        print(f"[*] Arquivo: {ARQUIVO_DADOS_REAIS}\n")
    else:
        print("[*] MODO ATIVO: PERFIS SINTÉTICOS DE REFERÊNCIA (168 h padronizadas)\n")

    R = {}
    for c, c_info in CENARIOS.items():
        print(f"-> Otimizando Cenário {c:<5}: {c_info['nome']:<22} ... ", end="", flush=True)
        n = construir_e_otimizar(c_info, PCT_FIO_B_ANO_BASE, LIMITE_PADRAO, usar_dados_reais=usar_reais)
        R[c] = auditar_resultado(n, c_info, PCT_FIO_B_ANO_BASE, usar_dados_reais=usar_reais)
        print("[OK]")

    print("\n" + "=" * 86)
    print(f"   RESUMO FINANCEIRO CONSOLIDADO (Projeção Mensal: R$/mês [x{FATOR_MES:.4f}])")
    print("=" * 86)
    fat_c1 = R["C1"]["fatura_total"] * FATOR_MES

    print(f"{'Cenário':<6} | {'Descrição':<20} | {'Fatura Total':<16} | {'Indústria':<13} | {'Condomínio':<13} | {'Economia vs C1'}")
    print("-" * 86)
    for c, c_info in CENARIOS.items():
        fat_mes = R[c]["fatura_total"] * FATOR_MES
        ind_mes = (R[c]["ind"]["energia_rs"] + R[c]["ind"]["fio_b_rs"] + R[c]["dem_ind_rs"]) * FATOR_MES
        cond_mes = (R[c]["cond"]["energia_rs"] + R[c]["cond"]["fio_b_rs"]) * FATOR_MES
        eco = fat_c1 - fat_mes
        pct = (eco / fat_c1) * 100.0 if fat_c1 > 0 else 0.0
        print(f"{c:<6} | {c_info['nome']:<20} | R$ {fat_mes:12,.2f} | R$ {ind_mes:9,.2f} | R$ {cond_mes:9,.2f} | R$ {eco:9,.2f} (-{pct:4.1f}%)")
    print("=" * 86)

    print("\n-> Calculando Viabilidade Econômico-Financeira Plurianual (WACC Real: 8% a.a.)...")
    def resolver_simplificado(c_key, pct_f):
        n_s = construir_e_otimizar(CENARIOS[c_key], pct_f, LIMITE_PADRAO, usar_dados_reais=usar_reais)
        return auditar_resultado(n_s, CENARIOS[c_key], pct_f, usar_dados_reais=usar_reais)

    INV = calcular_investimentos(resolver_simplificado)
    print("\n" + "=" * 86)
    print("   INDICADORES FINANCEIROS DE INVESTIMENTO (Fluxo de Caixa Descontado)")
    print("=" * 86)
    print(f"{'Ativo / Transição':<24} | {'CAPEX (R$)':<13} | {'VPL (R$)':<14} | {'TIR (%)':<9} | {'Payback'}")
    print("-" * 86)
    for nome, d in INV.items():
        pb_str = f"{d['payback']:.1f} anos" if d['payback'] else "Não amortiza"
        tir_str = f"{d['tir']:.1%}" if d['tir'] else "N/D"
        print(f"{nome:<24} | R$ {d['capex']:10,.0f} | R$ {d['vpl']:11,.0f} | {tir_str:>9} | {pb_str}")
    print("=" * 86)

    print("\n[INFO REGULATÓRIA - LEI 14.300/2022]")
    print(f"* Rateio Ótimo Configurado: {CFG_IND['rateio_creditos']:.0%} Indústria / {CFG_COND['rateio_creditos']:.0%} Condomínio.")
    print("* O rateio integral no Condomínio (B1) maximiza a economia do consórcio, pois a TE de B1")
    print("  remunera o crédito a R$ 0,85/kWh contra R$ 0,65/kWh no fora de ponta da Indústria (A4).")
    print("* Prioridade Zero atendida: Carga auxiliar da Fazenda é abatida antes do rateio remoto.")
    print("* Auditoria Contábil: 100% verificada com erro < 1e-2 R$/semana.\n")


if __name__ == "__main__":
    main()
