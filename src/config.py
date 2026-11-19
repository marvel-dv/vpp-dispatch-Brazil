# ==============================================================================
# CONFIGURAÇÃO DE PARÂMETROS DO SISTEMA, TARIFAS E ATIVOS (Lei 14.300 / CEMIG-D)
# Versão: 0.1.1 (Modularizado para GitHub)
# ==============================================================================
import os
import numpy as np
import pandas as pd

# 1. HORIZONTE DE SIMULAÇÃO (Semanal: 168 horas contínuas)
DIAS = 7
HOURS = DIAS * 24
DATA_INICIO = "2026-01-05"  # Primeira segunda-feira útil de 2026
snapshots = pd.date_range(f"{DATA_INICIO} 00:00", periods=HOURS, freq="h")
DIAS_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]

# Horário de Ponta CEMIG: 17h às 20h estritamente em dias úteis (Seg–Sex)
HORAS_PONTA_DIA = [17, 18, 19]
PONTA_APENAS_DIAS_UTEIS = True

is_ponta = np.array([
    (ts.hour in HORAS_PONTA_DIA) and (ts.dayofweek < 5 or not PONTA_APENAS_DIAS_UTEIS)
    for ts in snapshots
], dtype=bool)

MASCARA = {"P": is_ponta, "FP": ~is_ponta}
SNAPS = {"P": snapshots[MASCARA["P"]], "FP": snapshots[MASCARA["FP"]]}
POSTOS_A = ("P", "FP")

# Transição do Fio B (Art. 27 da Lei 14.300/2022)
ANO_BASE = 2026
TRANSICAO_FIO_B = {2023: 0.15, 2024: 0.30, 2025: 0.45, 2026: 0.60, 2027: 0.75, 2028: 0.90}
PCT_FIO_B_POS_2028 = 1.00
PCT_FIO_B_ANO_BASE = TRANSICAO_FIO_B[ANO_BASE]  # 60% em 2026

# 2. PARÂMETROS OPERACIONAIS, CARGA AUXILIAR E CUSD
FATOR_CARGA_AUX_FAZ_PU = 0.006              # Base contínua 24h: 0,6% de pv_kwp (1,20 kW p/ CFTV, alarme, telemetria 4G e standby)
FATOR_CARGA_AUX_DIURNA_PU = 0.006           # +0,6% no pico solar p/ ventilação forçada dos inversores (pico total 2,40 kW)
MODO_CONVERSAO_GRUPO_B = "te"               # "te" = razão TE_A4[posto]/TE_B1 (Art. 655-G §5 REN 1.000) | "1para1" = 1:1 volumétrico
CUSD_MINIMO_FAZENDA_KW = 30.0               # Demanda mínima de consumo Grupo A4 exigida pela ANEEL (kW)
TARIFA_CUSD_CONSUMO_FAZ_RS_KW_MES = 38.50   # Tarifa de demanda de consumo A4 Verde CEMIG (R$/kW/mês)
OM_FAZENDA_DESASSISTIDA_RS_MES = 2200.00    # O&M mensal remoto/desassistido da Fazenda Solar (R$/mês)
OM_BESS_HVAC_RS_MES = 850.00                # O&M mensal do BESS incluindo climatização HVAC (R$/mês)

# 3. PARTICIPANTES DA MICRORREDE
CONFIG_PARTICIPANTES = {
    "Industria": {
        "nome": "Indústria Metalmecânica",
        "subgrupo": "A4 (13.8 kV)",
        "modalidade_tarifaria": "Horossazonal Verde",
        "tarifa_energia": {"P": 1.80, "FP": 0.65},   # R$/kWh (TE + TUSD energia)
        "te": {"P": 0.47, "FP": 0.29},               # R$/kWh (somente TE)
        "fio_b_bruto": {"P": 0.2476, "FP": 0.2476},  # R$/kWh
        "tarifa_demanda_rs_kw_mes": 38.50,           # TUSD demanda A4 Verde CEMIG
        "demanda_minima_kw": 30.0,
        "demanda_contratada_atual_kw": 110.0,
        "otimizar_demanda": True,
        "pv_local_kwp": 50.0,
        "rateio_creditos": 0.0,                      # Ótimo global: 0% Ind / 100% Cond
    },
    "Condominio": {
        "nome": "Condomínio Residencial (UC agregada)",
        "subgrupo": "B1 (220/127 V)",
        "modalidade_tarifaria": "Convencional Monômia",
        "tarifa_energia": {"U": 0.85},               # R$/kWh monômia
        "te": {"U": 0.35},                           # R$/kWh TE homologada CEMIG B1
        "fio_b_bruto": {"U": 0.2476},                # R$/kWh Fio B homologado B1
        "rateio_creditos": 1.0,                      # Absorve 100% dos créditos remotos
    },
    "FazendaSolar": {
        "nome": "Fazenda Solar Compartilhada",
        "subgrupo": "A4 (13.8 kV)",
        "modalidade_tarifaria": "Horossazonal Verde",
        "pv_kwp": 200.0,
        "inversor_kw_ac": 200.0,
        "tarifa_tusdg_rs_kw_mes": 12.50,             # TUSDg injeção A4
        "tarifa_energia": {"P": 1.80, "FP": 0.65},
        "te": {"P": 0.47, "FP": 0.29},
        "fio_b_bruto": {"P": 0.2476, "FP": 0.2476},
        "fator_carga_aux_noturna_pu": FATOR_CARGA_AUX_FAZ_PU,
        "fator_carga_aux_diurna_pu": FATOR_CARGA_AUX_DIURNA_PU,
        "cusd_minimo_kw": CUSD_MINIMO_FAZENDA_KW,
        "tarifa_cusd_consumo_rs_kw_mes": TARIFA_CUSD_CONSUMO_FAZ_RS_KW_MES,
        "om_mensal_rs": OM_FAZENDA_DESASSISTIDA_RS_MES,
    },
}

CFG_IND = CONFIG_PARTICIPANTES["Industria"]
CFG_COND = CONFIG_PARTICIPANTES["Condominio"]
CFG_FAZ = CONFIG_PARTICIPANTES["FazendaSolar"]

assert abs(CFG_IND["rateio_creditos"] + CFG_COND["rateio_creditos"] - 1.0) < 1e-9, \
    "A soma dos percentuais de rateio deve ser 100%."

# Conversão de créditos A4 -> B1 por posto (Art. 655-G §5 REN 1.000)
if MODO_CONVERSAO_GRUPO_B == "te":
    FATOR_CONVERSAO_GRUPO_B = {po: CFG_FAZ["te"][po] / CFG_COND["te"]["U"] for po in ("P", "FP")}
elif MODO_CONVERSAO_GRUPO_B == "1para1":
    FATOR_CONVERSAO_GRUPO_B = {"P": 1.0, "FP": 1.0}
else:
    raise ValueError(f"MODO_CONVERSAO_GRUPO_B inválido: {MODO_CONVERSAO_GRUPO_B}")

K_POSTO = {
    ("FP", "P"): CFG_IND["te"]["FP"] / CFG_IND["te"]["P"],
    ("P", "FP"): CFG_IND["te"]["P"] / CFG_IND["te"]["FP"],
}

# 4. RESTRIÇÃO DE INVERSÃO DE FLUXO (Art. 73 REN 1.000/2021)
RESTRICAO_INVERSAO_FLUXO = {"ativa": True, "horas": list(range(8, 16)), "limite_kw": 120.0}

def obter_vetor_limite(limite_kw, horas):
    if limite_kw is None:
        return None
    return np.array([limite_kw if ts.hour in horas else np.inf for ts in snapshots], dtype=float)

LIMITE_PADRAO = (obter_vetor_limite(RESTRICAO_INVERSAO_FLUXO["limite_kw"], RESTRICAO_INVERSAO_FLUXO["horas"])
                 if RESTRICAO_INVERSAO_FLUXO["ativa"] else None)

# 5. SISTEMA DE ARMAZENAMENTO POR BATERIA (BESS)
CAPACIDADE_BESS_KWH = 200.0
POTENCIA_BESS_KW = 50.0
EFICIENCIA_CARGA = 0.92
EFICIENCIA_DESCARGA = 0.92
CUSTO_DEGRADACAO_KWH = 0.30
SOC_RESERVA_KWH = 0.15 * CAPACIDADE_BESS_KWH

# 6. CENÁRIOS MODELADOS
CENARIOS = {
    "C1":   {"nome": "Isoladas",         "fazenda": False, "bess": None},
    "C1.5": {"nome": "EMS Ind. (s/VPP)", "fazenda": False, "bess": "Industria"},
    "C2":   {"nome": "VPP s/BESS",       "fazenda": True,  "bess": None},
    "C3":   {"nome": "VPP+BESS Ind.",    "fazenda": True,  "bess": "Industria"},
    "C4":   {"nome": "VPP+BESS Faz.",    "fazenda": True,  "bess": "Fazenda"},
}

# 7. PREMISSAS ECONÔMICAS E TEMPORAIS
SEMANAS_ANO = 52.142857  # 365 / 7 semanas por ano exatas
FATOR_MES = SEMANAS_ANO / 12.0  # 4.345238 sem/mês (30.4375 dias/mês)

def custo_semana(tarifa_mensal):
    return tarifa_mensal / FATOR_MES

ECON = {
    "wacc_real": 0.08,
    "semanas_ano": SEMANAS_ANO,
    "fator_mes": FATOR_MES,
    "capex_fazenda_rs_wp": 3.30,
    "om_fazenda_mensal_rs": OM_FAZENDA_DESASSISTIDA_RS_MES,
    "vida_fazenda_anos": 25,
    "capex_bess_rs_kwh": 2800.0,
    "om_bess_mensal_rs": OM_BESS_HVAC_RS_MES,
    "vida_bess_anos": 10,
}

# 8. CONFIGURAÇÃO DE DADOS DE ENTRADA (Sintéticos vs. Reais)
USAR_DADOS_REAIS = False
DIRETORIO_ATUAL = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_DADOS_REAIS = os.path.abspath(os.path.join(DIRETORIO_ATUAL, "..", "data", "perfis_reais_exemplo_168h.csv"))
