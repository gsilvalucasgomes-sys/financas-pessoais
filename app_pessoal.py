import sqlite3
from datetime import date
import pandas as pd
import streamlit as st

DB = "finance_pessoal.db"

st.set_page_config(page_title="Finanças Pessoais", page_icon="💳", layout="wide")


# =========================
# Helpers
# =========================
def key(prefix: str) -> str:
    if "_key_seq" not in st.session_state:
        st.session_state._key_seq = 0
    st.session_state._key_seq += 1
    return f"{prefix}_{st.session_state._key_seq}"


def to_dt(s):
    return pd.to_datetime(s, errors="coerce")


def add_months(year: int, month: int, add: int):
    m = month + add
    y = year + (m - 1) // 12
    m = ((m - 1) % 12) + 1
    return y, m


def ym_add(ym: str, add: int) -> str:
    y, m = map(int, ym.split("-"))
    y2, m2 = add_months(y, m, add)
    return f"{y2:04d}-{m2:02d}"


def month_range(ym: str):
    y, m = map(int, ym.split("-"))
    start = pd.Timestamp(y, m, 1).date()
    end = (pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)).date()
    return start, end


def compute_statement_month(purchase_date: date, closing_day: int) -> str:
    y, m, d = purchase_date.year, purchase_date.month, purchase_date.day
    if d <= closing_day:
        return f"{y:04d}-{m:02d}"
    if m == 12:
        return f"{y+1:04d}-01"
    return f"{y:04d}-{m+1:02d}"


def months_between(d1: date, d2: date) -> int:
    """Número de meses (incluindo o mês inicial) entre duas datas."""
    return (d2.year - d1.year) * 12 + (d2.month - d1.month) + 1


# =========================
# Login (Streamlit Secrets)
# =========================
def require_login():
    pw_secret = st.secrets.get("APP_PASSWORD", None)

    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False

    # Sem secret: não trava (útil em dev), mas avisa
    if not pw_secret:
        st.warning("⚠️ APP_PASSWORD não configurado nos Secrets. O app ficará sem login.")
        return

    if st.session_state.auth_ok:
        return

    st.title("🔐 Acesso")
    st.caption("Digite a senha para acessar o sistema.")
    pw = st.text_input("Senha", type="password", key="login_password")

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Entrar", use_container_width=True, key="login_btn"):
            if pw == pw_secret:
                st.session_state.auth_ok = True
                st.rerun()
            else:
                st.error("Senha incorreta.")
    with c2:
        st.success("✅ Acesso protegido por senha (Secrets configurado).")

    st.stop()


require_login()

# Top bar
top_left, top_right = st.columns([6, 2])
with top_right:
    mobile_mode = st.toggle("📱 Modo celular", value=False, key="mobile_mode")
    if st.button("Sair 🔒", use_container_width=True, key="logout_btn"):
        st.session_state.auth_ok = False
        st.rerun()


# =========================
# DB / Schema
# =========================
def conectar():
    return sqlite3.connect(DB)


def table_columns(con, table):
    rows = con.execute(f"PRAGMA table_info({table});").fetchall()
    return {r[1] for r in rows}


def ensure_schema():
    with conectar() as con:
        # Contas
        con.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('BANK','CASH')),
            initial_balance REAL NOT NULL DEFAULT 0
        );
        """)

        # Cartões
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
        cols_cards = table_columns(con, "cards")
        if "last4" not in cols_cards:
            con.execute("ALTER TABLE cards ADD COLUMN last4 TEXT;")

        # Metas mensais (mantida)
        con.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            monthly_target REAL NOT NULL DEFAULT 0
        );
        """)

        # Lançamentos
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
            statement_month TEXT,
            FOREIGN KEY(account_id) REFERENCES accounts(id),
            FOREIGN KEY(card_id) REFERENCES cards(id)
        );
        """)
        cols_tx = table_columns(con, "transactions")
        if "installments_total" not in cols_tx:
            con.execute("ALTER TABLE transactions ADD COLUMN installments_total INTEGER;")
        if "installment_no" not in cols_tx:
            con.execute("ALTER TABLE transactions ADD COLUMN installment_no INTEGER;")
        if "recurrence_id" not in cols_tx:
            con.execute("ALTER TABLE transactions ADD COLUMN recurrence_id INTEGER;")

        # Recorrências
        con.execute("""
        CREATE TABLE IF NOT EXISTS recurrences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('INCOME','EXPENSE')),
            amount REAL NOT NULL,
            category TEXT,
            description TEXT,
            method TEXT NOT NULL CHECK(method IN ('BANK','CASH','CARD')),
            account_id INTEGER,
            card_id INTEGER,
            day_of_month INTEGER NOT NULL CHECK(day_of_month BETWEEN 1 AND 28),
            active INTEGER NOT NULL DEFAULT 1
        );
        """)

        # Meta por prazo (ex: 1 ano)
        con.execute("""
        CREATE TABLE IF NOT EXISTS long_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            target_amount REAL NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            start_amount REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
        );
        """)

        # Regras de categorias (Essencial vs Discricionário)
        con.execute("""
        CREATE TABLE IF NOT EXISTS category_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL UNIQUE,
            class TEXT NOT NULL CHECK(class IN ('ESSENTIAL','DISCRETIONARY'))
        );
        """)

        con.commit()


def seed_if_empty():
    with conectar() as con:
        a = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        g = con.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
        c = con.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0]

    if a == 0:
        with conectar() as con:
            con.execute("INSERT INTO accounts (name,type,initial_balance) VALUES (?,?,?)", ("Conta Principal", "BANK", 0))
            con.execute("INSERT INTO accounts (name,type,initial_balance) VALUES (?,?,?)", ("Carteira", "CASH", 0))
            con.commit()

    if g == 0:
        with conectar() as con:
            con.execute("INSERT INTO goals (name, monthly_target) VALUES (?,?)", ("Economia do mês", 0))
            con.commit()

    # Categorias padrão: Discricionários (neutro, sem julgamento)
    if c == 0:
        default_discretionary = ["delivery", "bar", "compras", "streamings", "jogos"]
        with conectar() as con:
            for cat in default_discretionary:
                con.execute(
                    "INSERT OR IGNORE INTO category_rules (category, class) VALUES (?,?)",
                    (cat, "DISCRETIONARY")
                )
            con.commit()


# =========================
# Loaders
# =========================
def carregar_accounts():
    with conectar() as con:
        return pd.read_sql_query("SELECT * FROM accounts ORDER BY id", con)


def carregar_cards():
    with conectar() as con:
        return pd.read_sql_query("SELECT * FROM cards ORDER BY id", con)


def carregar_goals():
    with conectar() as con:
        return pd.read_sql_query("SELECT * FROM goals ORDER BY id", con)


def carregar_recurrences():
    with conectar() as con:
        return pd.read_sql_query("SELECT * FROM recurrences ORDER BY id DESC", con)


def carregar_long_goal():
    with conectar() as con:
        return pd.read_sql_query("SELECT * FROM long_goals WHERE active=1 ORDER BY id DESC LIMIT 1", con)


def carregar_category_rules():
    with conectar() as con:
        return pd.read_sql_query("SELECT category, class FROM category_rules ORDER BY category", con)


def carregar_transactions():
    with conectar() as con:
        df = pd.read_sql_query("SELECT * FROM transactions ORDER BY dt DESC, id DESC", con)

    df["dt"] = to_dt(df["dt"]).dt.date
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    for c in ["category", "description", "statement_month"]:
        df[c] = df[c].fillna("")
    for c in ["installments_total", "installment_no", "recurrence_id"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# =========================
# Core functions
# =========================
def add_transaction(dt_: date, kind: str, amount: float, category: str, description: str,
                    status: str, method: str, account_id=None, card_id=None, statement_month=None,
                    installments_total=None, installment_no=None, recurrence_id=None):
    with conectar() as con:
        con.execute("""
            INSERT INTO transactions
            (dt, kind, amount, category, description, status, method, account_id, card_id, statement_month,
             installments_total, installment_no, recurrence_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            statement_month or None,
            int(installments_total) if installments_total else None,
            int(installment_no) if installment_no else None,
            int(recurrence_id) if recurrence_id else None,
        ))
        con.commit()


def delete_transaction(tx_id: int):
    with conectar() as con:
        con.execute("DELETE FROM transactions WHERE id=?", (int(tx_id),))
        con.commit()


def calc_account_balance(account_id: int, tx: pd.DataFrame, accounts: pd.DataFrame) -> float:
    init = float(accounts.loc[accounts["id"] == account_id, "initial_balance"].iloc[0])
    df = tx[tx["status"] == "PAID"].copy()

    df_bank = df[(df["method"].isin(["BANK", "CASH"])) & (df["account_id"] == account_id)]
    incomes = df_bank[df_bank["kind"] == "INCOME"]["amount"].sum()
    expenses = df_bank[df_bank["kind"] == "EXPENSE"]["amount"].sum()

    df_pay = df[(df["method"] == "CARD_PAYMENT") & (df["account_id"] == account_id)]
    pay_out = df_pay["amount"].sum()

    return init + float(incomes) - float(expenses) - float(pay_out)


def card_statement_detail(card_id: int, statement_month: str, tx: pd.DataFrame) -> pd.DataFrame:
    df = tx[(tx["method"] == "CARD") & (tx["card_id"] == card_id) & (tx["statement_month"] == statement_month)].copy()
    return df


def card_statement_total(card_id: int, statement_month: str, tx: pd.DataFrame) -> float:
    return float(card_statement_detail(card_id, statement_month, tx)["amount"].sum())


def create_installments_on_card(dt_: date, total_amount: float, n: int, category: str, description: str,
                                card_id: int, closing_day: int, status: str):
    per = round(float(total_amount) / int(n), 2)
    amounts = [per] * n
    diff = round(float(total_amount) - sum(amounts), 2)
    amounts[-1] = round(amounts[-1] + diff, 2)

    first_stmt = compute_statement_month(dt_, closing_day)
    for i in range(1, n + 1):
        stmt = ym_add(first_stmt, i - 1)
        add_transaction(
            dt_=dt_,
            kind="EXPENSE",
            amount=amounts[i - 1],
            category=category,
            description=f"{description} ({i}/{n})",
            status=status,
            method="CARD",
            account_id=None,
            card_id=card_id,
            statement_month=stmt,
            installments_total=n,
            installment_no=i
        )


def run_recurrences_for_month(target_ym: str):
    rec = carregar_recurrences()
    if rec.empty:
        return 0

    tx = carregar_transactions()
    start, end = month_range(target_ym)
    existing_ids = set(tx[(tx["dt"] >= start) & (tx["dt"] <= end)]["recurrence_id"].dropna().astype(int).tolist())

    created = 0
    cards = carregar_cards()

    for _, r in rec[rec["active"] == 1].iterrows():
        rid = int(r["id"])
        if rid in existing_ids:
            continue

        y, m = map(int, target_ym.split("-"))
        day = int(r["day_of_month"])
        dt_ = date(y, m, day)

        method = r["method"]
        kind = r["kind"]
        amount = float(r["amount"])
        category = r["category"] or ""
        desc = r["description"] or r["name"] or "Recorrência"

        if method in ["BANK", "CASH"]:
            add_transaction(dt_, kind, amount, category, desc, "PAID", method,
                            account_id=int(r["account_id"]) if r["account_id"] else None,
                            recurrence_id=rid)
            created += 1
        else:
            if r["card_id"] and not cards.empty:
                cid = int(r["card_id"])
                closing_day = int(cards.loc[cards["id"] == cid, "closing_day"].iloc[0])
                stmt = compute_statement_month(dt_, closing_day)
                add_transaction(dt_, "EXPENSE", amount, category, desc, "PAID", "CARD",
                                card_id=cid, statement_month=stmt, recurrence_id=rid)
                created += 1

    return created


# =========================
# Meta por prazo (1 ano etc.)
# =========================
def salvar_long_goal(name: str, target_amount: float, start_date: date, end_date: date, start_amount: float):
    with conectar() as con:
        con.execute("UPDATE long_goals SET active=0 WHERE active=1")
        con.execute("""
            INSERT INTO long_goals (name, target_amount, start_date, end_date, start_amount, active)
            VALUES (?,?,?,?,?,1)
        """, (name.strip(), float(target_amount), start_date.isoformat(), end_date.isoformat(), float(start_amount)))
        con.commit()


def calc_long_goal_plan(goal_row: dict, tx: pd.DataFrame) -> dict:
    start_date = pd.to_datetime(goal_row["start_date"]).date()
    end_date = pd.to_datetime(goal_row["end_date"]).date()
    target_amount = float(goal_row["target_amount"])
    start_amount = float(goal_row["start_amount"])

    total_months = max(1, months_between(start_date, end_date))

    period = tx[(tx["dt"] >= start_date) & (tx["dt"] <= end_date) & (tx["status"] == "PAID")].copy()

    income = float(period[period["kind"] == "INCOME"]["amount"].sum())
    out_bank_cash = float(period[(period["kind"] == "EXPENSE") & (period["method"].isin(["BANK", "CASH"]))]["amount"].sum())
    card_pay = float(period[(period["method"] == "CARD_PAYMENT")]["amount"].sum())
    saved_so_far = income - out_bank_cash - card_pay

    current_amount = start_amount + saved_so_far
    remaining = max(0.0, target_amount - current_amount)
    need_per_month = remaining / total_months if total_months else remaining

    progress = 0.0 if target_amount <= 0 else min(1.0, max(0.0, current_amount / target_amount))

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_months": total_months,
        "target_amount": target_amount,
        "start_amount": start_amount,
        "saved_so_far": saved_so_far,
        "current_amount": current_amount,
        "remaining": remaining,
        "need_per_month": need_per_month,
        "progress": progress
    }


def current_month_savings(tx: pd.DataFrame, ym: str) -> float:
    start, end = month_range(ym)
    month_paid = tx[(tx["dt"] >= start) & (tx["dt"] <= end) & (tx["status"] == "PAID")].copy()
    income = float(month_paid[month_paid["kind"] == "INCOME"]["amount"].sum())
    out_bank_cash = float(month_paid[(month_paid["kind"] == "EXPENSE") & (month_paid["method"].isin(["BANK", "CASH"]))]["amount"].sum())
    card_pay = float(month_paid[(month_paid["method"] == "CARD_PAYMENT")]["amount"].sum())
    return income - out_bank_cash - card_pay


def is_discretionary(category: str, rules_df: pd.DataFrame) -> bool:
    cat = (category or "").strip().lower()
    if not cat:
        return False
    hit = rules_df[rules_df["category"].str.lower() == cat]
    if hit.empty:
        return False
    return hit.iloc[0]["class"] == "DISCRETIONARY"


# =========================
# Init
# =========================
ensure_schema()
seed_if_empty()

accounts = carregar_accounts()
cards = carregar_cards()
goals = carregar_goals()
tx = carregar_transactions()

st.title("💳 Finanças Pessoais")
st.caption("Contas, cartão de crédito, metas, recorrências, parcelamentos e relatórios.")

tabs = st.tabs([
    "🏠 Dashboard",
    "➕ Lançamentos",
    "💳 Cartões",
    "🔁 Recorrências",
    "📊 Relatórios",
    "🏦 Contas",
    "🎯 Metas",
    "⚙️ Exportar/Backup"
])


# =========================
# Dashboard
# =========================
with tabs[0]:
    hoje = date.today()
    all_months = sorted(
        {d.strftime("%Y-%m") for d in pd.to_datetime(tx["dt"], errors="coerce").dropna()} | {hoje.strftime("%Y-%m")}
    )
    ym = st.selectbox(
        "Mês",
        options=all_months,
        index=all_months.index(hoje.strftime("%Y-%m")) if hoje.strftime("%Y-%m") in all_months else 0,
        key="dash_month"
    )

    start, end = month_range(ym)
    month_tx = tx[(tx["dt"] >= start) & (tx["dt"] <= end)].copy()
    month_paid = month_tx[month_tx["status"] == "PAID"].copy()

    income = float(month_paid[month_paid["kind"] == "INCOME"]["amount"].sum())
    expense_bank_cash = float(month_paid[(month_paid["kind"] == "EXPENSE") & (month_paid["method"].isin(["BANK", "CASH"]))]["amount"].sum())
    card_payments = float(month_paid[(month_paid["method"] == "CARD_PAYMENT")]["amount"].sum())
    savings = income - expense_bank_cash - card_payments

    if mobile_mode:
        c1, c2 = st.columns(2)
        c1.metric("Entradas", f"R$ {income:,.2f}")
        c2.metric("Saídas", f"R$ {expense_bank_cash:,.2f}")
        c3, c4 = st.columns(2)
        c3.metric("Pag. fatura", f"R$ {card_payments:,.2f}")
        c4.metric("Economia", f"R$ {savings:,.2f}")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Entradas (pagas)", f"R$ {income:,.2f}")
        c2.metric("Saídas (conta/carteira)", f"R$ {expense_bank_cash:,.2f}")
        c3.metric("Pagamentos de fatura", f"R$ {card_payments:,.2f}")
        c4.metric("Economia do mês", f"R$ {savings:,.2f}")

    st.divider()

    exp = month_paid[(month_paid["kind"] == "EXPENSE") & (month_paid["method"].isin(["BANK", "CASH", "CARD_PAYMENT"]))].copy()
    if exp.empty:
        st.info("Sem despesas pagas neste mês.")
    else:
        exp["category"] = exp["category"].replace("", "Sem categoria")
        cat = exp.groupby("category")["amount"].sum().sort_values(ascending=False)
        st.subheader("Despesas por categoria (pagas)")
        st.bar_chart(cat)

    st.subheader("Saldos das contas (pagos)")
    bal_rows = [{"Conta": a["name"], "Tipo": a["type"], "Saldo": calc_account_balance(int(a["id"]), tx, accounts)}
                for _, a in accounts.iterrows()]
    st.dataframe(pd.DataFrame(bal_rows), use_container_width=True, hide_index=True)

    st.divider()

    # -------- Cards de cartões + alertas por % da renda --------
    st.subheader("💳 Cartões – fatura do mês")

    WARN_PCT = 20.0
    HIGH_PCT = 30.0
    income_month = float(month_paid[month_paid["kind"] == "INCOME"]["amount"].sum())

    if income_month <= 0:
        st.warning("⚠️ Sem renda (entradas pagas) registrada neste mês. Os alertas por % ficarão desativados.")

    cards = carregar_cards()
    if cards.empty:
        st.info("Cadastre cartões para ver os valores aqui.")
    else:
        per_row = 1 if mobile_mode else 3
        grid = st.columns(per_row)

        for i, row in enumerate(cards.itertuples(index=False)):
            total = card_statement_total(row.id, ym, tx)
            last4 = getattr(row, "last4", "") or "----"

            if income_month > 0:
                pct = (total / income_month) * 100
                if pct >= HIGH_PCT:
                    badge = f"🔴 Alto ({pct:.1f}%)"
                elif pct >= WARN_PCT:
                    badge = f"🟡 Atenção ({pct:.1f}%)"
                else:
                    badge = f"🟢 Ok ({pct:.1f}%)"
            else:
                badge = "⚪ Sem renda"

            with grid[i % per_row]:
                st.markdown(
                    f"""
                    <div style="
                        border-radius: 14px;
                        padding: 14px;
                        border: 1px solid rgba(255,255,255,0.12);
                        background: rgba(255,255,255,0.03);
                        margin-bottom: 12px;
                    ">
                        <div style="display:flex; justify-content:space-between; align-items:center; gap:10px;">
                            <div style="font-weight:600; font-size:16px;">💳 {row.name}</div>
                            <div style="font-size:12px; opacity:0.9;">{badge}</div>
                        </div>
                        <div style="opacity:0.7; margin-bottom:8px;">Final •••• {last4}</div>
                        <div style="font-size:13px; opacity:0.8;">Fatura do mês</div>
                        <div style="font-size:20px; font-weight:700;">R$ {total:,.2f}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

        # -------- Insights Top 3 --------
        st.markdown("### 🧠 Insights do mês (cartões)")

        rows = []
        for row in cards.itertuples(index=False):
            total = float(card_statement_total(row.id, ym, tx))
            last4 = getattr(row, "last4", "") or "----"

            if income_month > 0:
                pct = (total / income_month) * 100
                limit_high = (HIGH_PCT / 100) * income_month
                limit_warn = (WARN_PCT / 100) * income_month
                reduce_to_high = max(0.0, total - limit_high)
                reduce_to_warn = max(0.0, total - limit_warn)
            else:
                pct = None
                reduce_to_high = None
                reduce_to_warn = None

            rows.append({
                "cartao": row.name,
                "final": last4,
                "total": total,
                "pct": pct,
                "reduce_to_high": reduce_to_high,
                "reduce_to_warn": reduce_to_warn,
            })

        df_cards = pd.DataFrame(rows).sort_values("total", ascending=False)

        if df_cards["total"].sum() <= 0:
            st.info("Sem gastos em cartão neste mês.")
        else:
            top3 = df_cards.head(3).copy()
            if mobile_mode:
                c1 = st.container()
                c2 = st.container()
            else:
                c1, c2 = st.columns([2, 3])

            with c1:
                st.subheader("Top 3 cartões do mês")
                view = top3.copy()
                view["Cartão"] = view.apply(lambda r: f"{r['cartao']} •••• {r['final']}", axis=1)
                view["Fatura"] = view["total"].map(lambda x: f"R$ {x:,.2f}")
                view["% da renda"] = view["pct"].map(lambda x: "—" if pd.isna(x) else f"{x:.1f}%")
                st.dataframe(view[["Cartão", "Fatura", "% da renda"]], use_container_width=True, hide_index=True)

            with c2:
                st.subheader("O que ajustar para voltar pro caminho certo")

                if income_month <= 0:
                    st.warning("Sem renda no mês: não dá para calcular alertas por %. Lance uma entrada (ex: Salário).")
                else:
                    total_cards = float(df_cards["total"].sum())
                    total_pct = (total_cards / income_month) * 100
                    st.caption(f"Total em cartões no mês: **R$ {total_cards:,.2f}**  |  **{total_pct:.1f}%** da renda")

                    high = df_cards[df_cards["pct"] >= HIGH_PCT].copy()
                    warn = df_cards[(df_cards["pct"] >= WARN_PCT) & (df_cards["pct"] < HIGH_PCT)].copy()

                    if high.empty and warn.empty:
                        st.success("✅ Seus cartões estão em zona verde/ok (nenhum acima de 20% da renda).")
                    else:
                        if not high.empty:
                            st.markdown("**🔴 Cartões em nível Alto (≥ 30%)**")
                            for _, r in high.iterrows():
                                st.write(
                                    f"- **{r['cartao']} •••• {r['final']}**: {r['pct']:.1f}%  "
                                    f"→ reduzir **R$ {r['reduce_to_high']:,.2f}** para ficar < 30% "
                                    f"(e **R$ {r['reduce_to_warn']:,.2f}** para ficar < 20%)."
                                )

                        if not warn.empty:
                            st.markdown("**🟡 Cartões em Atenção (≥ 20%)**")
                            for _, r in warn.iterrows():
                                st.write(
                                    f"- **{r['cartao']} •••• {r['final']}**: {r['pct']:.1f}%  "
                                    f"→ reduzir **R$ {r['reduce_to_warn']:,.2f}** para ficar < 20%."
                                )

                    st.caption("💡 'Reduzir' aqui = evitar novas compras no cartão neste mês (ou usar conta/dinheiro).")


# =========================
# Lançamentos
# =========================
with tabs[1]:
    st.subheader("Adicionar lançamento")

    accounts = carregar_accounts()
    cards = carregar_cards()
    rules = carregar_category_rules()
    tx = carregar_transactions()

    colA, colB, colC, colD = st.columns(4) if not mobile_mode else (st.columns(2) + st.columns(2))

    with colA:
        kind = st.selectbox("Tipo", ["INCOME", "EXPENSE"],
                            format_func=lambda x: "Entrada" if x == "INCOME" else "Saída",
                            key="tx_kind")
    with colB:
        method = st.selectbox("Meio", ["BANK", "CASH", "CARD"],
                              format_func=lambda x: {"BANK": "Conta", "CASH": "Dinheiro", "CARD": "Cartão"}[x],
                              key="tx_method")
    with colC:
        dt_ = st.date_input("Data", value=date.today(), key="tx_date")
    with colD:
        status = st.selectbox("Status", ["PAID", "PENDING"],
                              format_func=lambda x: "Pago" if x == "PAID" else "Pendente",
                              key="tx_status")

    if mobile_mode:
        amount = st.number_input("Valor", min_value=0.0, step=10.0, key="tx_amount")
        category = st.text_input("Categoria", placeholder="Ex: Mercado, Aluguel, Salário...", key="tx_category")
        description = st.text_input("Descrição", placeholder="Opcional", key="tx_desc")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            amount = st.number_input("Valor", min_value=0.0, step=10.0, key="tx_amount")
        with col2:
            category = st.text_input("Categoria", placeholder="Ex: Mercado, Aluguel, Salário...", key="tx_category")
        with col3:
            description = st.text_input("Descrição", placeholder="Opcional", key="tx_desc")

    # Dica de classificação
    cat_class = None
    if (category or "").strip():
        if is_discretionary(category, rules):
            cat_class = "DISCRETIONARY"
            st.info("🏷️ Categoria classificada como **Discricionária** (pode gerar alerta na meta por prazo).")
        else:
            # se existir como ESSENTIAL mostra, se não, neutro
            hit = rules[rules["category"].str.lower() == category.strip().lower()]
            if not hit.empty and hit.iloc[0]["class"] == "ESSENTIAL":
                cat_class = "ESSENTIAL"
                st.success("🏷️ Categoria classificada como **Essencial** (não gera alerta).")

    account_id = None
    card_id = None
    statement_month = None
    installments_total = None

    if method in ["BANK", "CASH"]:
        acc_opts = accounts[accounts["type"] == ("BANK" if method == "BANK" else "CASH")].copy()
        if acc_opts.empty:
            st.warning("Você não tem conta desse tipo cadastrada. Vá em 'Contas' e crie uma.")
        else:
            account_id = st.selectbox("Conta", acc_opts["id"].tolist(),
                                      format_func=lambda i: acc_opts.loc[acc_opts["id"] == i, "name"].iloc[0],
                                      key="tx_account")
    else:
        if cards.empty:
            st.warning("Você não tem cartão cadastrado. Vá em 'Cartões' e crie um.")
        else:
            card_id = st.selectbox("Cartão", cards["id"].tolist(),
                                   format_func=lambda i: cards.loc[cards["id"] == i, "name"].iloc[0],
                                   key="tx_card")
            closing_day = int(cards.loc[cards["id"] == card_id, "closing_day"].iloc[0])

            with st.expander("💳 Parcelamento (opcional)"):
                installments_total = st.number_input("Número de parcelas", min_value=1, max_value=36, value=1, step=1,
                                                     key="tx_installments")
                st.caption("Se parcelas > 1, o sistema cria automaticamente (1/n) em cada fatura.")

            statement_month = compute_statement_month(dt_, closing_day)
            st.caption(f"📌 Vai para a fatura: **{statement_month}** (fechamento dia {closing_day})")

    # Alertas da meta por prazo (só para gastos discricionários)
    lg = carregar_long_goal()
    will_warn = False
    warn_msg = ""

    if kind == "EXPENSE" and status == "PAID" and (category or "").strip() and not lg.empty:
        if is_discretionary(category, rules):
            goal_row = lg.iloc[0].to_dict()
            plan = calc_long_goal_plan(goal_row, tx)

            # Quanto deveria poupar por mês (média)
            required_per_month = float(plan["need_per_month"])

            # economia do mês atual (com base no mês do lançamento)
            ym_tx = dt_.strftime("%Y-%m")
            current_save = current_month_savings(tx, ym_tx)

            # Se já está abaixo do necessário, qualquer gasto discricionário “piora”
            if required_per_month > 0 and current_save < required_per_month:
                gap = required_per_month - current_save
                will_warn = True
                warn_msg = (
                    f"⚠️ Este gasto é **discricionário** e você está **abaixo do necessário** para a meta por prazo.\n\n"
                    f"- Meta por prazo: **{goal_row['name']}**\n"
                    f"- Necessário por mês (média): **R$ {required_per_month:,.2f}**\n"
                    f"- Você está economizando neste mês (estimado): **R$ {current_save:,.2f}**\n"
                    f"- Falta para bater a média: **R$ {gap:,.2f}**\n\n"
                    f"💡 Sugestão: evite novos discricionários ou compense economizando **+R$ {float(amount):,.2f}** até o fim do mês."
                )

    if will_warn:
        st.warning(warn_msg)

    if st.button("Salvar lançamento ✅", use_container_width=True, key="tx_save_btn"):
        if method == "CARD" and installments_total and int(installments_total) > 1:
            create_installments_on_card(dt_, amount, int(installments_total), category, description, int(card_id), int(closing_day), status)
        else:
            add_transaction(dt_, kind, amount, category, description, status, method, account_id, card_id, statement_month)

        st.success("Lançamento salvo!")
        st.rerun()

    st.divider()
    st.subheader("Últimos lançamentos")

    tx = carregar_transactions()
    tx_view = tx.copy()
    tx_view["tipo"] = tx_view["kind"].map({"INCOME": "Entrada", "EXPENSE": "Saída"})
    tx_view["meio"] = tx_view["method"].map({"BANK": "Conta", "CASH": "Dinheiro", "CARD": "Cartão", "CARD_PAYMENT": "Pag. Cartão"})
    tx_view["parcela"] = tx_view.apply(
        lambda r: f"{int(r['installment_no'])}/{int(r['installments_total'])}"
        if pd.notna(r.get("installments_total")) and pd.notna(r.get("installment_no")) else "",
        axis=1
    )
    st.dataframe(
        tx_view[["id", "dt", "tipo", "amount", "category", "description", "status", "meio", "account_id", "card_id", "statement_month", "parcela"]],
        use_container_width=True, hide_index=True
    )

    with st.expander("🗑️ Excluir lançamento"):
        del_id = st.number_input("ID para excluir", min_value=0, step=1, key="tx_del_id")
        if st.button("Excluir", type="secondary", key="tx_del_btn"):
            if del_id > 0:
                delete_transaction(int(del_id))
                st.success("Excluído.")
                st.rerun()


# =========================
# Cartões
# =========================
with tabs[2]:
    st.subheader("Cartões de crédito")

    accounts = carregar_accounts()
    cards = carregar_cards()

    st.markdown("### Cadastrar cartão")
    if mobile_mode:
        card_name = st.text_input("Nome do cartão", key="card_name")
        closing_day = st.number_input("Dia de fechamento (1-28)", min_value=1, max_value=28, value=10, key="card_close")
        due_day = st.number_input("Dia de vencimento (1-28)", min_value=1, max_value=28, value=15, key="card_due")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            card_name = st.text_input("Nome do cartão", key="card_name")
        with c2:
            closing_day = st.number_input("Dia de fechamento (1-28)", min_value=1, max_value=28, value=10, key="card_close")
        with c3:
            due_day = st.number_input("Dia de vencimento (1-28)", min_value=1, max_value=28, value=15, key="card_due")

    bank_accs = accounts[accounts["type"] == "BANK"]
    pay_acc = None
    if bank_accs.empty:
        st.warning("Crie uma conta bancária em 'Contas' primeiro.")
    else:
        pay_acc = st.selectbox("Conta para pagar fatura", bank_accs["id"].tolist(),
                               format_func=lambda i: bank_accs.loc[bank_accs["id"] == i, "name"].iloc[0],
                               key="card_pay_acc")

    last4 = st.text_input("Final do cartão (4 dígitos)", max_chars=4, placeholder="Ex: 1234", key="card_last4")

    if st.button("Salvar cartão", use_container_width=True, key="card_save"):
        if not card_name.strip():
            st.warning("Informe um nome para o cartão.")
        elif pay_acc is None:
            st.warning("Selecione uma conta bancária para pagar a fatura.")
        else:
            with conectar() as con:
                con.execute(
                    "INSERT INTO cards (name, closing_day, due_day, pay_account_id, last4) VALUES (?,?,?,?,?)",
                    (card_name.strip(), int(closing_day), int(due_day), int(pay_acc), last4.strip())
                )
                con.commit()
            st.success("Cartão criado!")
            st.rerun()

    st.divider()
    st.markdown("### Faturas")

    cards = carregar_cards()
    tx = carregar_transactions()
    accounts = carregar_accounts()

    if cards.empty:
        st.info("Cadastre um cartão para ver faturas.")
    else:
        if mobile_mode:
            cid = st.selectbox("Cartão", cards["id"].tolist(),
                               format_func=lambda i: cards.loc[cards["id"] == i, "name"].iloc[0],
                               key="stmt_card")
            months = sorted(set(tx[(tx["method"] == "CARD") & (tx["card_id"] == cid)]["statement_month"]) - {""})
            stmt = st.selectbox("Fatura (YYYY-MM)", months, index=len(months) - 1, key="stmt_month") if months else None
        else:
            colA, colB = st.columns(2)
            with colA:
                cid = st.selectbox("Cartão", cards["id"].tolist(),
                                   format_func=lambda i: cards.loc[cards["id"] == i, "name"].iloc[0],
                                   key="stmt_card")
            with colB:
                months = sorted(set(tx[(tx["method"] == "CARD") & (tx["card_id"] == cid)]["statement_month"]) - {""})
                stmt = st.selectbox("Fatura (YYYY-MM)", months, index=len(months) - 1, key="stmt_month") if months else None

        if stmt:
            total = card_statement_total(cid, stmt, tx)
            st.metric("Total da fatura", f"R$ {total:,.2f}")

            detail = card_statement_detail(cid, stmt, tx).sort_values(["dt", "id"])
            st.dataframe(detail[["dt", "amount", "category", "description", "status", "installment_no", "installments_total"]],
                         use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("### Pagar fatura (lança saída na conta bancária)")
            pay_acc = int(cards.loc[cards["id"] == cid, "pay_account_id"].iloc[0])
            acc_name = accounts.loc[accounts["id"] == pay_acc, "name"].iloc[0]
            st.caption(f"Conta de pagamento configurada: **{acc_name}**")

            pay_date = st.date_input("Data do pagamento", value=date.today(), key="pay_date")
            pay_amount = st.number_input("Valor a pagar", min_value=0.0, value=float(total), step=50.0, key="pay_amount")

            if st.button("Registrar pagamento de fatura ✅", use_container_width=True, key="pay_btn"):
                add_transaction(pay_date, "EXPENSE", pay_amount, "Cartão", f"Pagamento fatura {stmt}", "PAID",
                                "CARD_PAYMENT", account_id=pay_acc, card_id=cid, statement_month=stmt)
                st.success("Pagamento registrado!")
                st.rerun()


# =========================
# Recorrências
# =========================
with tabs[3]:
    st.subheader("🔁 Recorrências")
    st.caption("Ex: aluguel dia 05, internet dia 10, salário dia 01…")

    accounts = carregar_accounts()
    cards = carregar_cards()

    with st.expander("➕ Criar recorrência"):
        if mobile_mode:
            r_name = st.text_input("Nome", placeholder="Ex: Aluguel", key="rec_name")
            r_kind = st.selectbox("Tipo", ["INCOME", "EXPENSE"],
                                  format_func=lambda x: "Entrada" if x == "INCOME" else "Saída",
                                  key="rec_kind")
            r_amount = st.number_input("Valor", min_value=0.0, step=10.0, key="rec_amount")
            r_day = st.number_input("Dia do mês (1-28)", min_value=1, max_value=28, value=5, key="rec_day")
        else:
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                r_name = st.text_input("Nome", placeholder="Ex: Aluguel", key="rec_name")
            with c2:
                r_kind = st.selectbox("Tipo", ["INCOME", "EXPENSE"],
                                      format_func=lambda x: "Entrada" if x == "INCOME" else "Saída",
                                      key="rec_kind")
            with c3:
                r_amount = st.number_input("Valor", min_value=0.0, step=10.0, key="rec_amount")
            with c4:
                r_day = st.number_input("Dia do mês (1-28)", min_value=1, max_value=28, value=5, key="rec_day")

        r_method = st.selectbox("Meio", ["BANK", "CASH", "CARD"],
                                format_func=lambda x: {"BANK": "Conta", "CASH": "Dinheiro", "CARD": "Cartão"}[x],
                                key="rec_method")
        r_category = st.text_input("Categoria", placeholder="Ex: Moradia", key="rec_cat")
        r_desc = st.text_input("Descrição", placeholder="Opcional", key="rec_desc")

        r_account_id = None
        r_card_id = None
        if r_method in ["BANK", "CASH"]:
            opts = accounts[accounts["type"] == ("BANK" if r_method == "BANK" else "CASH")]
            if not opts.empty:
                r_account_id = st.selectbox("Conta", opts["id"].tolist(),
                                            format_func=lambda i: opts.loc[opts["id"] == i, "name"].iloc[0],
                                            key="rec_account")
        else:
            if not cards.empty:
                r_card_id = st.selectbox("Cartão", cards["id"].tolist(),
                                         format_func=lambda i: cards.loc[cards["id"] == i, "name"].iloc[0],
                                         key="rec_card")

        if st.button("Salvar recorrência", use_container_width=True, key="rec_save"):
            if not r_name.strip():
                st.warning("Informe o nome.")
            else:
                with conectar() as con:
                    con.execute("""
                        INSERT INTO recurrences (name, kind, amount, category, description, method, account_id, card_id, day_of_month, active)
                        VALUES (?,?,?,?,?,?,?,?,?,1)
                    """, (r_name.strip(), r_kind, float(r_amount), r_category or None, r_desc or None, r_method,
                          int(r_account_id) if r_account_id else None,
                          int(r_card_id) if r_card_id else None,
                          int(r_day)))
                    con.commit()
                st.success("Recorrência criada!")
                st.rerun()

    st.divider()
    rec = carregar_recurrences()
    if rec.empty:
        st.info("Nenhuma recorrência cadastrada.")
    else:
        st.dataframe(rec, use_container_width=True, hide_index=True)

    st.divider()
    target_ym = st.text_input("Gerar recorrências para o mês (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="rec_target_ym")
    if st.button("Gerar recorrências do mês ✅", use_container_width=True, key="rec_run_btn"):
        created = run_recurrences_for_month(target_ym)
        st.success(f"Criados {created} lançamentos recorrentes para {target_ym}.")
        st.rerun()


# =========================
# Relatórios
# =========================
with tabs[4]:
    st.subheader("📊 Relatórios")

    tx = carregar_transactions()
    accounts = carregar_accounts()
    cards = carregar_cards()

    hoje = date.today()
    all_months = sorted(
        {d.strftime("%Y-%m") for d in pd.to_datetime(tx["dt"], errors="coerce").dropna()} | {hoje.strftime("%Y-%m")}
    )
    ym = st.selectbox(
        "Mês (filtro)",
        options=all_months,
        index=all_months.index(hoje.strftime("%Y-%m")) if hoje.strftime("%Y-%m") in all_months else 0,
        key="rep_month"
    )

    start, end = month_range(ym)
    f = tx[(tx["dt"] >= start) & (tx["dt"] <= end) & (tx["status"] == "PAID")].copy()

    f_exp = f[(f["kind"] == "EXPENSE") & (f["method"].isin(["BANK", "CASH", "CARD_PAYMENT"]))].copy()
    group = st.selectbox("Agrupar por", ["Categoria", "Conta", "Cartão"], key="rep_group")

    if f_exp.empty:
        st.info("Sem despesas pagas nesse mês.")
    else:
        if group == "Categoria":
            key_series = f_exp["category"].replace("", "Sem categoria")
        elif group == "Conta":
            mp = {int(r["id"]): r["name"] for _, r in accounts.iterrows()}
            key_series = f_exp["account_id"].fillna(0).astype(int).map(lambda i: mp.get(i, "—"))
        else:
            mp = {int(r["id"]): r["name"] for _, r in cards.iterrows()}
            key_series = f_exp["card_id"].fillna(0).astype(int).map(lambda i: mp.get(i, "—"))

        tab = f_exp.groupby(key_series)["amount"].sum().sort_values(ascending=False)
        st.bar_chart(tab)
        st.dataframe(tab.reset_index().rename(columns={"index": group, "amount": "Total"}), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Detalhamento do mês (pagos)")
    st.dataframe(f.sort_values(["dt", "id"]), use_container_width=True, hide_index=True)


# =========================
# Contas
# =========================
with tabs[5]:
    st.subheader("🏦 Contas")

    st.markdown("### Cadastrar conta")
    if mobile_mode:
        acc_name = st.text_input("Nome da conta", key="acc_name")
        acc_type = st.selectbox("Tipo", ["BANK", "CASH"],
                                format_func=lambda x: "Conta bancária" if x == "BANK" else "Dinheiro",
                                key="acc_type")
        init_bal = st.number_input("Saldo inicial", value=0.0, step=100.0, key="acc_init")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            acc_name = st.text_input("Nome da conta", key="acc_name")
        with c2:
            acc_type = st.selectbox("Tipo", ["BANK", "CASH"],
                                    format_func=lambda x: "Conta bancária" if x == "BANK" else "Dinheiro",
                                    key="acc_type")
        with c3:
            init_bal = st.number_input("Saldo inicial", value=0.0, step=100.0, key="acc_init")

    if st.button("Salvar conta", use_container_width=True, key="acc_save"):
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
    rows = [{"Conta": a["name"], "Tipo": a["type"], "Saldo": calc_account_balance(int(a["id"]), tx, accounts)}
            for _, a in accounts.iterrows()]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# =========================
# Metas (mensal + prazo + regras)
# =========================
with tabs[6]:
    st.subheader("🎯 Metas")

    # ---- Meta mensal (mantida) ----
    goals = carregar_goals()
    tx = carregar_transactions()

    goal = goals.iloc[0]
    st.markdown(f"### 🗓️ Meta mensal — {goal['name']}")

    new_target = st.number_input("Meta mensal (R$)", min_value=0.0, value=float(goal["monthly_target"]), step=50.0, key="goal_target")
    if st.button("Salvar meta mensal", use_container_width=True, key="goal_save"):
        with conectar() as con:
            con.execute("UPDATE goals SET monthly_target=? WHERE id=?", (float(new_target), int(goal["id"])))
            con.commit()
        st.success("Meta mensal atualizada!")
        st.rerun()

    st.divider()

    # ---- Meta por prazo (ex: 1 ano) ----
    st.subheader("📅 Meta por prazo (ex: 1 ano)")

    lg = carregar_long_goal()

    with st.expander("➕ Criar/atualizar meta por prazo"):
        if mobile_mode:
            g_name = st.text_input("Nome da meta", value="Reserva / Objetivo", key="lg_name")
            g_target = st.number_input("Valor alvo (R$)", min_value=0.0, step=100.0, key="lg_target")
            g_start_amount = st.number_input("Já tenho (R$)", min_value=0.0, step=100.0, key="lg_start_amount")
        else:
            c1, c2, c3 = st.columns(3)
            with c1:
                g_name = st.text_input("Nome da meta", value="Reserva / Objetivo", key="lg_name")
            with c2:
                g_target = st.number_input("Valor alvo (R$)", min_value=0.0, step=100.0, key="lg_target")
            with c3:
                g_start_amount = st.number_input("Já tenho (R$)", min_value=0.0, step=100.0, key="lg_start_amount")

        if mobile_mode:
            g_start = st.date_input("Data início", value=date.today(), key="lg_start")
            default_end = date(date.today().year + 1, date.today().month, 1)
            g_end = st.date_input("Data fim", value=default_end, key="lg_end")
        else:
            c4, c5 = st.columns(2)
            with c4:
                g_start = st.date_input("Data início", value=date.today(), key="lg_start")
            with c5:
                default_end = date(date.today().year + 1, date.today().month, 1)
                g_end = st.date_input("Data fim", value=default_end, key="lg_end")

        if st.button("Salvar meta por prazo ✅", use_container_width=True, key="lg_save"):
            if g_end < g_start:
                st.error("A data fim precisa ser maior que a data início.")
            else:
                salvar_long_goal(g_name, g_target, g_start, g_end, g_start_amount)
                st.success("Meta por prazo salva!")
                st.rerun()

    lg = carregar_long_goal()
    if lg.empty:
        st.info("Nenhuma meta por prazo ativa ainda. Crie uma acima.")
    else:
        goal_row = lg.iloc[0].to_dict()
        plan = calc_long_goal_plan(goal_row, tx)

        st.markdown(f"**Meta ativa:** {goal_row['name']}")
        st.write(f"Período: **{plan['start_date']}** até **{plan['end_date']}**  |  Meses: **{plan['total_months']}**")

        if mobile_mode:
            st.metric("Valor alvo", f"R$ {plan['target_amount']:,.2f}")
            st.metric("Valor atual estimado", f"R$ {plan['current_amount']:,.2f}")
            st.metric("Falta", f"R$ {plan['remaining']:,.2f}")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Valor alvo", f"R$ {plan['target_amount']:,.2f}")
            c2.metric("Valor atual estimado", f"R$ {plan['current_amount']:,.2f}")
            c3.metric("Falta", f"R$ {plan['remaining']:,.2f}")

        st.progress(plan["progress"])
        st.caption(f"{plan['progress']*100:.1f}% da meta")

        st.info(f"📌 Para bater a meta, você precisa poupar em média **R$ {plan['need_per_month']:,.2f} / mês** daqui pra frente.")

    st.divider()

    # ---- Regras de categorias: Essencial vs Discricionário ----
    st.subheader("🏷️ Categorias: Essenciais x Discricionários")
    st.caption("Padrão já vem com: delivery, bar, compras, streamings, jogos como Discricionários.")

    rules = carregar_category_rules()
    st.dataframe(rules, use_container_width=True, hide_index=True)

    with st.expander("➕ Adicionar/alterar categoria"):
        cat = st.text_input("Categoria", placeholder="Ex: mercado, aluguel, internet", key="rule_cat").strip().lower()
        cls = st.selectbox("Classificação", ["ESSENTIAL", "DISCRETIONARY"],
                           format_func=lambda x: "Essencial" if x == "ESSENTIAL" else "Discricionário",
                           key="rule_cls")
        if st.button("Salvar categoria", use_container_width=True, key="rule_save"):
            if not cat:
                st.warning("Informe a categoria.")
            else:
                with conectar() as con:
                    con.execute("INSERT OR REPLACE INTO category_rules (category, class) VALUES (?,?)", (cat, cls))
                    con.commit()
                st.success("Categoria salva!")
                st.rerun()

    with st.expander("🗑️ Remover categoria"):
        if rules.empty:
            st.info("Sem categorias para remover.")
        else:
            cat_del = st.selectbox("Escolha a categoria", rules["category"].tolist(), key="rule_del_sel")
            if st.button("Remover", type="secondary", use_container_width=True, key="rule_del_btn"):
                with conectar() as con:
                    con.execute("DELETE FROM category_rules WHERE category=?", (cat_del,))
                    con.commit()
                st.success("Removida.")
                st.rerun()


# =========================
# Export / Backup
# =========================
with tabs[7]:
    st.subheader("⚙️ Exportar / Backup")
    st.caption("Baixe seus dados em CSV (recomendado fazer 1x por mês).")

    tx = carregar_transactions()
    rec = carregar_recurrences()
    accounts = carregar_accounts()
    cards = carregar_cards()
    rules = carregar_category_rules()
    lg = carregar_long_goal()

    if mobile_mode:
        st.download_button("⬇️ Lançamentos (CSV)", tx.to_csv(index=False).encode("utf-8"),
                           file_name="lancamentos.csv", mime="text/csv", use_container_width=True, key="dl_tx")
        st.download_button("⬇️ Recorrências (CSV)", rec.to_csv(index=False).encode("utf-8"),
                           file_name="recorrencias.csv", mime="text/csv", use_container_width=True, key="dl_rec")
        st.download_button("⬇️ Contas (CSV)", accounts.to_csv(index=False).encode("utf-8"),
                           file_name="contas.csv", mime="text/csv", use_container_width=True, key="dl_acc")
        st.download_button("⬇️ Cartões (CSV)", cards.to_csv(index=False).encode("utf-8"),
                           file_name="cartoes.csv", mime="text/csv", use_container_width=True, key="dl_cards")
        st.download_button("⬇️ Categorias (CSV)", rules.to_csv(index=False).encode("utf-8"),
                           file_name="categorias.csv", mime="text/csv", use_container_width=True, key="dl_rules")
        st.download_button("⬇️ Meta por prazo (CSV)", lg.to_csv(index=False).encode("utf-8"),
                           file_name="meta_prazo.csv", mime="text/csv", use_container_width=True, key="dl_lg")
    else:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.download_button("⬇️ Lançamentos (CSV)", tx.to_csv(index=False).encode("utf-8"),
                               file_name="lancamentos.csv", mime="text/csv", use_container_width=True, key="dl_tx")
        with col2:
            st.download_button("⬇️ Recorrências (CSV)", rec.to_csv(index=False).encode("utf-8"),
                               file_name="recorrencias.csv", mime="text/csv", use_container_width=True, key="dl_rec")
        with col3:
            st.download_button("⬇️ Contas (CSV)", accounts.to_csv(index=False).encode("utf-8"),
                               file_name="contas.csv", mime="text/csv", use_container_width=True, key="dl_acc")
        with col4:
            st.download_button("⬇️ Cartões (CSV)", cards.to_csv(index=False).encode("utf-8"),
                               file_name="cartoes.csv", mime="text/csv", use_container_width=True, key="dl_cards")

        col5, col6 = st.columns(2)
        with col5:
            st.download_button("⬇️ Categorias (CSV)", rules.to_csv(index=False).encode("utf-8"),
                               file_name="categorias.csv", mime="text/csv", use_container_width=True, key="dl_rules")
        with col6:
            st.download_button("⬇️ Meta por prazo (CSV)", lg.to_csv(index=False).encode("utf-8"),
                               file_name="meta_prazo.csv", mime="text/csv", use_container_width=True, key="dl_lg")

    st.divider()
    st.warning("⚠️ No Streamlit Cloud o armazenamento pode resetar em updates. Faça backup com frequência.")
