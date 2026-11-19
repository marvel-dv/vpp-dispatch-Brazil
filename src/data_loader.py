# ==============================================================================
# CARREGAMENTO E SÍNTESE DE CURVAS DE CARGA E GERAÇÃO SOLAR (168 HORAS)
# Versão: 0.1.1 (Modularizado para GitHub)
# ==============================================================================
import os
import numpy as np
import pandas as pd
from .config import (
    snapshots, HOURS, CFG_FAZ, USAR_DADOS_REAIS, ARQUIVO_DADOS_REAIS
)

def carregar_perfis_semanais(usar_dados_reais=None, caminho_csv=None):
    """
    Retorna os 5 vetores horários de 168 horas contínuas:
    (load_industria, load_condominio, demanda_total, pv_pu, load_aux_fazenda)
    """
    if usar_dados_reais is None:
        usar_dados_reais = USAR_DADOS_REAIS
    if caminho_csv is None:
        caminho_csv = ARQUIVO_DADOS_REAIS

    # 1. Perfil Industrial Sintético de Referência (A4 Verde)
    load_ind_util = [
        30, 30, 25, 25, 30, 40, 70, 110, 130, 140, 135, 130,
        125, 125, 130, 120, 110, 80, 60, 50, 40, 35, 30, 30
    ]
    load_ind_fds = [int(v * 0.40) for v in load_ind_util]
    load_industria = np.array([
        load_ind_util[ts.hour] if ts.dayofweek < 5 else load_ind_fds[ts.hour]
        for ts in snapshots
    ], dtype=float)

    # 2. Perfil de Condomínio e Solar (Sintético vs. Real)
    if usar_dados_reais:
        if not os.path.exists(caminho_csv):
            raise FileNotFoundError(f"Arquivo de dados reais não encontrado em: {caminho_csv}")
        df_r = pd.read_csv(caminho_csv)
        pv_pu = df_r["pv_pu_real_pvgis_bh"].values.astype(float)
        load_condominio = df_r["load_condominio_real_ons_kw"].values.astype(float)
        assert len(pv_pu) == HOURS and len(load_condominio) == HOURS, (
            f"Arquivo de dados reais deve conter {HOURS} horas (contém {len(pv_pu)})."
        )
    else:
        load_cond_util = [
            25, 20, 18, 18, 20, 25, 45, 50, 40, 35, 30, 30,
            30, 30, 35, 45, 65, 110, 130, 120, 95, 70, 50, 35
        ]
        load_cond_fds = [
            35, 28, 22, 20, 22, 30, 50, 65, 70, 65, 60, 60,
            55, 55, 60, 65, 75, 100, 115, 105, 85, 70, 55, 40
        ]
        load_condominio = np.array([
            load_cond_util[ts.hour] if ts.dayofweek < 5 else load_cond_fds[ts.hour]
            for ts in snapshots
        ], dtype=float)

        pv_base = np.array([
            0, 0, 0, 0, 0, 0.05, 0.15, 0.40, 0.65, 0.85, 0.95, 1.0,
            0.95, 0.85, 0.65, 0.40, 0.15, 0.05, 0, 0, 0, 0, 0, 0
        ])
        fatores_pv_dia = [1.00, 0.92, 0.98, 0.88, 0.96, 0.75, 0.82]
        pv_pu = np.array([
            min(1.0, pv_base[ts.hour] * fatores_pv_dia[i // 24])
            for i, ts in enumerate(snapshots)
        ], dtype=float)

    demanda_total = load_industria + load_condominio

    # 3. Carga Auxiliar Real da Fazenda Solar (Load_Aux_Fazenda):
    # Perfil contínuo: base 24h (1,20 kW) + ventilação proporcional à geração (até +1,20 kW -> pico 2,40 kW)
    pot_aux_base = CFG_FAZ["fator_carga_aux_noturna_pu"] * CFG_FAZ["pv_kwp"]
    pot_aux_diurna_adic = CFG_FAZ["fator_carga_aux_diurna_pu"] * CFG_FAZ["pv_kwp"]
    load_aux_fazenda = np.array([
        pot_aux_base + pot_aux_diurna_adic * pv_pu[i]
        for i in range(HOURS)
    ], dtype=float)

    return load_industria, load_condominio, demanda_total, pv_pu, load_aux_fazenda
