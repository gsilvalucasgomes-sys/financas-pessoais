import sqlite3
from datetime import date, datetime
import pandas as pd
import streamlit as st

DB = "finance_pessoal.db"

st.set_page_config(page_title="Finanças Pessoais", page_icon="💳", layout="wide")


# -------------------- DB --------------------
def conectar():
    return sqlite3.connect(DB)


def criar_tabelas():
    with conectar() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('BANK','CASH')),
            initial_balance REAL NOT NULL DEFAULT 0
        );
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            closing_day INTEGER NOT NULL CHECK(closing_day BETWEEN 1 AND 28),
            due_day INTEGER NOT NULL CHECK(due_day BETWEEN 1 AND 28),
            pay_account_id INTEGER,
            FOREIGN KEY(pay_account_id) REFERENCES accounts(id)
        );
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            monthly_target REAL NOT NULL DEFAULT 0
        );
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dt TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('INCOME','EXPENSE')),
            amount REAL NOT NULL,
            category TEXT,
            description TEXT,
            status TEXT NOT NULL CHECK(status IN ('PENDING','PAID')) DEFAULT 'PAID',
            method TEXT NOT NULL CHECK(method IN ('BANK','CASH','CARD','CARD_PAYMENT')),
            account_id INTEGER,
            card_id INTEGER,
            statement_month TEXT, -- YYYY-MM para compras no cartão
            FOREIGN KEY(account_id) REFERENCES accounts(id),
            FOREIGN KEY(card_id) REFERENCES cards(id)
        );
        """)
        con.commit()


def seed_if_empty():
    with conectar() as con:
        a = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        g = con.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
    if a == 0:
        with conectar() as con:
            con.execute("INSERT INTO accounts (name,type,initial_balance) VALUES (?,?,?)", ("Conta Principal", "BANK", 0))
            con.execute("INSERT INTO accounts (name,type,initial_balance) VALUES (?,?,?)", ("Carteira", "CASH", 0))
            con.commit()
    if g == 0:
        with conectar() as con:
            con.execute("INSERT INTO goals (name, monthly_target) VALUES (?,?)", ("Economia do mês", 0))
            con.commit()


# -------------------- Helpers --------------------
def to_dt(s):
    return pd.to_datetime(s, errors="coerce")


def compute_statement_month(purchase_date: date, closing_day: int) -> str:
    """
    Regra simples:
    - Se a compra for NO/ANTES do fechamento: entra na fatura do mês da compra
    - Se for DEPOIS do fechamento: entra na fatura do mês seguinte
    """
    y, m, d = purchase_date.year, purchase_date.month, purchase_date.day
    if d <= closing_day:
        return f"{y:04d}-{m:02d}"
    # mês seguinte
    if m == 12:
        return f"{y+1:04d}-01"
    return f"{y:04d}-{m+1:02d}"


def carregar_accounts():
    with conectar() as con:
        df = pd.read_sql_query("SELECT * FROM accounts ORDER BY id", con)
    return df


def carregar_cards():
    with conectar() as con:
        df = pd.read_sql_query("SELECT * FROM cards ORDER BY id", con)
    return df


def carregar_goals():
    with conectar() as con:
        df = pd.read_sql_query("SELECT * FROM goals ORDER BY id", con)
    return df


def carregar_transactions():
    with conectar() as con:
        df = pd.read_sql_query("SELECT * FROM transactions ORDER BY dt DESC, id DESC", con)
    df["dt"] = to_dt(df["dt"]).dt.date
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["category"] = df["category"].fillna("")
    df["description"] = df["description"].fillna("")
    df["statement_month"] = df["statement_month"].fillna("")
    return df


def add_transaction(dt_: date, kind: str, amount: float, category: str, description: str,
                    status: str, method: str, account_id=None, card_id=None, statement_month=None):
    with conectar() as con:
        con.execute("""
            INSERT INTO transactions
            (dt, kind, amount, category, description, status, method, account_id, card_id, statement_month)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            dt_.isoformat(),
            kind,
            float(amount),
            category or None,
            description or None,
            status,
            method,
            int(account_id) if account_id else None,
            int(card_id) if card_id else None,
            statement_month or None
        ))
        con.commit()


def delete_transaction(tx_id: int):
    with conectar() as con:
        con.execute("DELETE FROM transactions WHERE id=?", (int(tx_id),))
        con.commit()


def month_range(ym: str):
    # ym: YYYY-MM
    y, m = map(int, ym.split("-"))
    start = pd.Timestamp(y, m, 1).date()
    end = (pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)).date()
    return start, end


def calc_account_balance(account_id: int, tx: pd.DataFrame, accounts: pd.DataFrame) -> float:
    init = float(accounts.loc[accounts["id"] == account_id, "initial_balance"].iloc[0])

    df = tx[tx["status"] == "PAID"].copy()

    # Entradas/saídas pagas por BANK/CASH impactam a conta
    df_bank = df[(df["method"].isin(["BANK", "CASH"])) & (df["account_id"] == account_id)]
    incomes = df_bank[df_bank["kind"] == "INCOME"]["amount"].sum()
    expenses = df_bank[df_bank["kind"] == "EXPENSE"]["amount"].sum()

    # Pagamento de fatura (CARD_PAYMENT) sai da conta
    df_pay = df[(df["method"] == "CARD_PAYMENT") & (df["account_id"] == account_id)]
    pay_out = df_pay["amount"].sum()

    return init + float(incomes) - float(expenses) - float(pay_out)


def card_statement_total(card_id: int, statement_month: str, tx: pd.DataFrame) -> float:
    df = tx[(tx["method"] == "CARD") & (tx["card_id"] == card_id) & (tx["statement_month"] == statement_month)]
    return float(df["amount"].sum())


# -------------------- Init --------------------
criar_tabelas()
seed_if_empty()

accounts = carregar_accounts()
cards = carregar_cards()
goals = carregar_goals()
tx = carregar_transactions()

st.title("💳 Finanças Pessoais")
st.caption("Contas, cartão de crédito, metas e controle mensal.")

tabs = st.tabs(["🏠 Dashboard", "➕ Lançamentos", "💳 Cartões", "🏦 Contas", "🎯 Metas"])

# ==================== Dashboard ====================
with tabs[0]:
    hoje = date.today()
    ym = st.selectbox(
        "Mês",
        options=sorted({d.strftime("%Y-%m") for d in pd.to_datetime(tx["dt"], errors="coerce").dropna()} | {hoje.strftime("%Y-%m")}),
        index=0
    )
    start, end = month_range(ym)

    month_tx = tx[(tx["dt"] >= start) & (tx["dt"] <= end)].copy()
    month_paid = month_tx[month_tx["status"] == "PAID"]

    income = float(month_paid[month_paid["kind"] == "INCOME"]["amount"].sum())
    expense_bank_cash = float(month_paid[(month_paid["kind"] == "EXPENSE") & (month_paid["method"].isin(["BANK", "CASH"]))]["amount"].sum())
    card_purchases = float(month_paid[(month_paid["method"] == "CARD")]["amount"].sum())
    card_payments = float(month_paid[(month_paid["method"] == "CARD_PAYMENT")]["amount"].sum())

    # Economia do mês: entradas - (saídas bank/cash) - pagamento fatura
    # (compras no cartão entram quando paga a fatura, então aqui consideramos o pagamento)
    savings = income - expense_bank_cash - card_payments

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entradas (pagas)", f"R$ {income:,.2f}")
    c2.metric("Saídas (conta/carteira)", f"R$ {expense_bank_cash:,.2f}")
    c3.metric("Pagamentos de fatura", f"R$ {card_payments:,.2f}")
    c4.metric("Economia do mês", f"R$ {savings:,.2f}")

    st.divider()

    # Gráfico: entradas x saídas
    agg = month_paid.copy()
    agg["grupo"] = agg.apply(lambda r: "Entradas" if r["kind"] == "INCOME" else ("Saídas Conta" if r["method"] in ["BANK","CASH"] else ("Compras Cartão" if r["method"]=="CARD" else "Pagamento Cartão")), axis=1)
    chart = agg.groupby("grupo")["amount"].sum().reset_index()
    st.bar_chart(chart.set_index("grupo"))

    # Saldo por conta
    st.subheader("Saldos das contas (pagos)")
    bal_rows = []
    for _, a in accounts.iterrows():
        bal = calc_account_balance(int(a["id"]), tx, accounts)
        bal_rows.append({"Conta": a["name"], "Tipo": a["type"], "Saldo": bal})
    st.dataframe(pd.DataFrame(bal_rows), use_container_width=True, hide_index=True)

# ==================== Lançamentos ====================
with tabs[1]:
    st.subheader("Adicionar lançamento")

    colA, colB, colC, colD = st.columns(4)
    with colA:
        kind = st.selectbox("Tipo", ["INCOME", "EXPENSE"], format_func=lambda x: "Entrada" if x=="INCOME" else "Saída")
    with colB:
        method = st.selectbox("Meio", ["BANK", "CASH", "CARD"], format_func=lambda x: {"BANK":"Conta bancária","CASH":"Dinheiro","CARD":"Cartão"}[x])
    with colC:
        dt_ = st.date_input("Data", value=date.today())
    with colD:
        status = st.selectbox("Status", ["PAID", "PENDING"], format_func=lambda x: "Pago" if x=="PAID" else "Pendente")

    col1, col2, col3 = st.columns(3)
    with col1:
        amount = st.number_input("Valor", min_value=0.0, step=10.0)
    with col2:
        category = st.text_input("Categoria", placeholder="Ex: Mercado, Aluguel, Salário...")
    with col3:
        description = st.text_input("Descrição", placeholder="Opcional")

    account_id = None
    card_id = None
    statement_month = None

    if method in ["BANK", "CASH"]:
        acc_opts = accounts[accounts["type"] == ("BANK" if method == "BANK" else "CASH")].copy()
        if acc_opts.empty:
            st.warning("Você não tem conta desse tipo cadastrada. Vá em 'Contas' e crie uma.")
        else:
            account_id = st.selectbox("Conta", acc_opts["id"].tolist(), format_func=lambda i: acc_opts.loc[acc_opts["id"]==i, "name"].iloc[0])
    else:  # CARD
        if cards.empty:
            st.warning("Você não tem cartão cadastrado. Vá em 'Cartões' e crie um.")
        else:
            card_id = st.selectbox("Cartão", cards["id"].tolist(), format_func=lambda i: cards.loc[cards["id"]==i, "name"].iloc[0])
            closing_day = int(cards.loc[cards["id"]==card_id, "closing_day"].iloc[0])
            statement_month = compute_statement_month(dt_, closing_day)
            st.caption(f"📌 Esta compra vai para a fatura: **{statement_month}** (fechamento dia {closing_day})")

    if st.button("Salvar lançamento ✅", use_container_width=True):
        add_transaction(dt_, kind, amount, category, description, status, method, account_id, card_id, statement_month)
        st.success("Lançamento salvo!")
        st.rerun()

    st.divider()
    st.subheader("Últimos lançamentos")
    view = tx.copy()
    view = view.rename(columns={
        "id":"ID","dt":"Data","kind":"Tipo","amount":"Valor","category":"Categoria",
        "description":"Descrição","status":"Status","method":"Meio","statement_month":"Fatura"
    })
    st.dataframe(view[["ID","Data","Tipo","Valor","Categoria","Descrição","Status","Meio","account_id","card_id","Fatura"]],
                 use_container_width=True, hide_index=True)

    with st.expander("🗑️ Excluir lançamento"):
        del_id = st.number_input("ID para excluir", min_value=0, step=1)
        if st.button("Excluir", type="secondary"):
            if del_id > 0:
                delete_transaction(int(del_id))
                st.success("Excluído.")
                st.rerun()

# ==================== Cartões ====================
with tabs[2]:
    st.subheader("Cartões de crédito")

    st.markdown("### Cadastrar cartão")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_name = st.text_input("Nome do cartão", key="card_name")
    with c2:
        closing_day = st.number_input("Dia de fechamento (1-28)", min_value=1, max_value=28, value=10)
    with c3:
        due_day = st.number_input("Dia de vencimento (1-28)", min_value=1, max_value=28, value=15)
    with c4:
        bank_accs = accounts[accounts["type"] == "BANK"]
        pay_acc = st.selectbox("Conta para pagar fatura", bank_accs["id"].tolist(),
                               format_func=lambda i: bank_accs.loc[bank_accs["id"]==i, "name"].iloc[0])

    if st.button("Salvar cartão", use_container_width=True):
        if card_name.strip():
            with conectar() as con:
                con.execute("INSERT INTO cards (name, closing_day, due_day, pay_account_id) VALUES (?,?,?,?)",
                            (card_name.strip(), int(closing_day), int(due_day), int(pay_acc)))
                con.commit()
            st.success("Cartão criado!")
            st.rerun()
        else:
            st.warning("Informe um nome para o cartão.")

    st.divider()
    st.markdown("### Faturas")

    cards = carregar_cards()
    tx = carregar_transactions()

    if cards.empty:
        st.info("Cadastre um cartão para ver faturas.")
    else:
        colA, colB = st.columns(2)
        with colA:
            card_id = st.selectbox("Cartão", cards["id"].tolist(),
                                   format_func=lambda i: cards.loc[cards["id"]==i, "name"].iloc[0])
        with colB:
            months = sorted(set(tx[(tx["method"]=="CARD") & (tx["card_id"]==card_id)]["statement_month"]) - {""})
            if not months:
                st.info("Sem compras nesse cartão ainda.")
            else:
                stmt = st.selectbox("Fatura (YYYY-MM)", months)

                total = card_statement_total(card_id, stmt, tx)
                st.metric("Total da fatura", f"R$ {total:,.2f}")

                detail = tx[(tx["method"]=="CARD") & (tx["card_id"]==card_id) & (tx["statement_month"]==stmt)].copy()
                st.dataframe(detail[["dt","amount","category","description","status"]], use_container_width=True, hide_index=True)

                st.divider()
                st.markdown("### Pagar fatura (lança saída na conta bancária)")

                pay_acc = int(cards.loc[cards["id"]==card_id, "pay_account_id"].iloc[0])
                acc_name = accounts.loc[accounts["id"]==pay_acc, "name"].iloc[0]
                st.caption(f"Conta de pagamento configurada: **{acc_name}**")

                pay_date = st.date_input("Data do pagamento", value=date.today(), key="pay_date_card")
                pay_amount = st.number_input("Valor a pagar", min_value=0.0, value=float(total), step=50.0, key="pay_amount_card")

                if st.button("Registrar pagamento de fatura ✅", use_container_width=True):
                    # Saída na conta (CARD_PAYMENT)
                    add_transaction(pay_date, "EXPENSE", pay_amount, "Cartão", f"Pagamento fatura {stmt}", "PAID",
                                    "CARD_PAYMENT", account_id=pay_acc, card_id=card_id, statement_month=stmt)
                    st.success("Pagamento registrado!")
                    st.rerun()

# ==================== Contas ====================
with tabs[3]:
    st.subheader("Contas")

    st.markdown("### Cadastrar conta")
    c1, c2, c3 = st.columns(3)
    with c1:
        acc_name = st.text_input("Nome da conta", key="acc_name")
    with c2:
        acc_type = st.selectbox("Tipo", ["BANK","CASH"], format_func=lambda x: "Conta bancária" if x=="BANK" else "Dinheiro")
    with c3:
        init_bal = st.number_input("Saldo inicial", value=0.0, step=100.0)

    if st.button("Salvar conta", use_container_width=True):
        if acc_name.strip():
            with conectar() as con:
                con.execute("INSERT INTO accounts (name,type,initial_balance) VALUES (?,?,?)",
                            (acc_name.strip(), acc_type, float(init_bal)))
                con.commit()
            st.success("Conta criada!")
            st.rerun()
        else:
            st.warning("Informe um nome para a conta.")

    st.divider()
    st.markdown("### Saldos (considerando lançamentos pagos)")
    accounts = carregar_accounts()
    tx = carregar_transactions()

    rows = []
    for _, a in accounts.iterrows():
        bal = calc_account_balance(int(a["id"]), tx, accounts)
        rows.append({"Conta": a["name"], "Tipo": a["type"], "Saldo": bal})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ==================== Metas ====================
with tabs[4]:
    st.subheader("Metas")

    goals = carregar_goals()
    tx = carregar_transactions()

    goal = goals.iloc[0]  # v1: 1 meta principal
    st.markdown(f"### 🎯 {goal['name']}")

    new_target = st.number_input("Meta mensal (R$)", min_value=0.0, value=float(goal["monthly_target"]), step=50.0)
    if st.button("Salvar meta", use_container_width=True):
        with conectar() as con:
            con.execute("UPDATE goals SET monthly_target=? WHERE id=?", (float(new_target), int(goal["id"])))
            con.commit()
        st.success("Meta atualizada!")
        st.rerun()

    st.divider()
    st.markdown("### Progresso no mês atual")

    hoje = date.today()
    ym = hoje.strftime("%Y-%m")
    start, end = month_range(ym)
    month_paid = tx[(tx["dt"] >= start) & (tx["dt"] <= end) & (tx["status"]=="PAID")].copy()

    income = float(month_paid[month_paid["kind"]=="INCOME"]["amount"].sum())
    expense_bank_cash = float(month_paid[(month_paid["kind"]=="EXPENSE") & (month_paid["method"].isin(["BANK","CASH"]))]["amount"].sum())
    card_payments = float(month_paid[(month_paid["method"]=="CARD_PAYMENT")]["amount"].sum())

    savings = income - expense_bank_cash - card_payments
    target = float(new_target)

    st.metric("Economia do mês (estimada)", f"R$ {savings:,.2f}")

    if target <= 0:
        st.info("Defina uma meta mensal para ver o progresso.")
    else:
        progress = max(0.0, min(1.0, savings / target))
        st.progress(progress)
        st.caption(f"{progress*100:.1f}% da meta (meta: R$ {target:,.2f})")
