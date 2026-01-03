import sqlite3
from datetime import date
import pandas as pd
import streamlit as st

DB = "finance.db"

# ================== CONFIG ==================
st.set_page_config(page_title="Controle Financeiro", page_icon="💰", layout="wide")


# ================== BANCO ==================
def conectar():
    return sqlite3.connect(DB)

def criar_tabelas():
    with conectar() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS lancamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL CHECK(tipo IN ('RECEBER','PAGAR')),
            pessoa TEXT,
            categoria TEXT,
            descricao TEXT,
            valor REAL NOT NULL,
            vencimento TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PENDENTE','PAGO')) DEFAULT 'PENDENTE',
            data_pagamento TEXT
        );
        """)
        con.commit()

def inserir_lancamento(tipo, pessoa, categoria, descricao, valor, vencimento_iso):
    with conectar() as con:
        con.execute("""
            INSERT INTO lancamentos
            (tipo, pessoa, categoria, descricao, valor, vencimento, status)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDENTE')
        """, (tipo, pessoa or None, categoria or None, descricao or None, float(valor), vencimento_iso))
        con.commit()

def marcar_como_pago(lancamento_id, data_pagamento_iso):
    with conectar() as con:
        con.execute("""
            UPDATE lancamentos
            SET status='PAGO', data_pagamento=?
            WHERE id=?
        """, (data_pagamento_iso, int(lancamento_id)))
        con.commit()

def carregar_df():
    with conectar() as con:
        df = pd.read_sql_query("""
            SELECT id, tipo, pessoa, categoria, descricao,
                   valor, vencimento, status, data_pagamento
            FROM lancamentos
            ORDER BY vencimento ASC, id ASC
        """, con)

    df["valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0.0)

    # ✅ PADRÃO DEFINITIVO: datas internas como datetime (Timestamp) normalizadas
    df["vencimento_dt"] = pd.to_datetime(df["vencimento"], errors="coerce").dt.normalize()
    df["data_pagamento_dt"] = pd.to_datetime(df["data_pagamento"], errors="coerce").dt.normalize()

    # Campos texto
    df["pessoa"] = df["pessoa"].fillna("")
    df["categoria"] = df["categoria"].fillna("")
    df["descricao"] = df["descricao"].fillna("")
    return df


# ================== REGRAS ==================
def periodo_mes(ano: int, mes: int):
    """
    Retorna (inicio_dt, fim_dt) como Timestamps.
    fim_dt é 'fim do dia' para incluir todo o dia.
    """
    inicio = pd.Timestamp(year=ano, month=mes, day=1).normalize()
    fim = (inicio + pd.offsets.MonthEnd(0)).normalize()  # último dia do mês, 00:00
    fim = fim + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)  # 23:59:59
    return inicio, fim

def resumo_mes(df, ano, mes):
    inicio_dt, fim_dt = periodo_mes(ano, mes)

    # Previsto: por vencimento
    previsto = df[df["vencimento_dt"].notna()]
    previsto = previsto[(previsto["vencimento_dt"] >= inicio_dt) & (previsto["vencimento_dt"] <= fim_dt)]
    previsto_receber = float(previsto[previsto["tipo"] == "RECEBER"]["valor"].sum())
    previsto_pagar = float(previsto[previsto["tipo"] == "PAGAR"]["valor"].sum())

    # Realizado: por data_pagamento
    realizado = df[(df["status"] == "PAGO") & (df["data_pagamento_dt"].notna())]
    realizado = realizado[(realizado["data_pagamento_dt"] >= inicio_dt) & (realizado["data_pagamento_dt"] <= fim_dt)]
    recebido = float(realizado[realizado["tipo"] == "RECEBER"]["valor"].sum())
    pago = float(realizado[realizado["tipo"] == "PAGAR"]["valor"].sum())

    return {
        "inicio": inicio_dt.date(),
        "fim": (fim_dt - pd.Timedelta(seconds=1)).date(),  # só pra exibir bonitinho
        "previsto_receber": previsto_receber,
        "previsto_pagar": previsto_pagar,
        "saldo_previsto": previsto_receber - previsto_pagar,
        "recebido": recebido,
        "pago": pago,
        "saldo_realizado": recebido - pago
    }

def projecao_saldo(df, dias=60, saldo_inicial=0.0):
    hoje_dt = pd.Timestamp.today().normalize()
    fim_dt = hoje_dt + pd.Timedelta(days=int(dias)) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

    pend = df[(df["status"] == "PENDENTE") & (df["vencimento_dt"].notna())].copy()
    pend = pend[(pend["vencimento_dt"] >= hoje_dt) & (pend["vencimento_dt"] <= fim_dt)]
    pend = pend.sort_values(["vencimento_dt", "id"])

    saldo = float(saldo_inicial)
    linhas = []
    for _, r in pend.iterrows():
        valor = float(r["valor"])
        saldo += valor if r["tipo"] == "RECEBER" else -valor
        linhas.append({
            "Vencimento": r["vencimento_dt"].date(),
            "Tipo": r["tipo"],
            "Valor": valor,
            "Saldo projetado": saldo,
            "Pessoa": r["pessoa"],
            "Categoria": r["categoria"],
            "Descrição": r["descricao"]
        })

    return pd.DataFrame(linhas)


# ================== APP ==================
criar_tabelas()
st.title("💰 Controle Financeiro")

df = carregar_df()

aba1, aba2, aba3 = st.tabs(["➕ Lançamentos", "📊 Resumo", "📈 Projeções"])

# -------- ABA 1 --------
with aba1:
    st.subheader("Novo lançamento")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        tipo = st.selectbox("Tipo", ["RECEBER", "PAGAR"])
    with c2:
        valor = st.number_input("Valor", min_value=0.0, step=50.0)
    with c3:
        vencimento = st.date_input("Vencimento", value=date.today())
    with c4:
        categoria = st.text_input("Categoria")

    c5, c6 = st.columns(2)
    with c5:
        pessoa = st.text_input("Cliente / Fornecedor")
    with c6:
        descricao = st.text_input("Descrição")

    if st.button("Salvar lançamento"):
        inserir_lancamento(tipo, pessoa, categoria, descricao, valor, vencimento.isoformat())
        st.success("Lançamento salvo!")
        st.rerun()

    st.divider()
    st.subheader("Pendentes")

    pend = df[df["status"] == "PENDENTE"].copy()
    pend_view = pend[["id","tipo","pessoa","categoria","descricao","valor","vencimento_dt","status"]].copy()
    pend_view["vencimento"] = pend_view["vencimento_dt"].dt.date
    pend_view = pend_view.drop(columns=["vencimento_dt"])
    st.dataframe(pend_view, use_container_width=True, hide_index=True)

    if not pend.empty:
        st.subheader("Marcar lançamento como pago")

        col1, col2, col3 = st.columns([2, 2, 3])
        with col1:
            lanc_id = st.selectbox("ID", pend["id"].tolist())
        with col2:
            data_pg = st.date_input("Data do pagamento", value=date.today())
        with col3:
            st.caption("Dica: marque como pago quando realmente entrou/saíu do caixa.")

        if st.button("Marcar como pago ✅"):
            marcar_como_pago(lanc_id, data_pg.isoformat())
            st.success("Pagamento registrado!")
            st.rerun()

# -------- ABA 2 --------
with aba2:
    st.subheader("Resumo mensal")

    hoje = date.today()
    col1, col2 = st.columns(2)
    with col1:
        ano = st.number_input("Ano", min_value=2000, max_value=2100, value=hoje.year)
    with col2:
        mes = st.number_input("Mês", min_value=1, max_value=12, value=hoje.month)

    r = resumo_mes(df, int(ano), int(mes))

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Previsto a receber", f"R$ {r['previsto_receber']:,.2f}")
    k2.metric("Previsto a pagar", f"R$ {r['previsto_pagar']:,.2f}")
    k3.metric("Saldo previsto", f"R$ {r['saldo_previsto']:,.2f}")
    k4.metric("Saldo realizado", f"R$ {r['saldo_realizado']:,.2f}")

    st.caption(f"Período: {r['inicio']} até {r['fim']}")

# -------- ABA 3 --------
with aba3:
    st.subheader("Projeção de saldo")

    col1, col2 = st.columns(2)
    with col1:
        saldo_ini = st.number_input("Saldo inicial", value=0.0, step=100.0)
    with col2:
        dias = st.slider("Dias para projeção", 15, 180, 60)

    proj_df = projecao_saldo(df, dias=dias, saldo_inicial=saldo_ini)

    if proj_df.empty:
        st.info("Sem lançamentos para o período.")
    else:
        st.dataframe(proj_df, use_container_width=True, hide_index=True)

        st.subheader("Linha do saldo projetado")
        linha = proj_df[["Vencimento", "Saldo projetado"]].copy()
        linha["Vencimento"] = pd.to_datetime(linha["Vencimento"], errors="coerce")
        linha = linha.sort_values("Vencimento").set_index("Vencimento")
        st.line_chart(linha)
