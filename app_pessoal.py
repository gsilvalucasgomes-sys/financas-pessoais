import sqlite3
from datetime import date
import pandas as pd
import streamlit as st
# --- Helpers PT-BR (mês) ---
MESES_PT = [
    "Janeiro","Fevereiro","Março","Abril","Maio","Junho",
    "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"
]

def mes_label_pt(ano: int, mes: int) -> str:
    return f"{MESES_PT[mes-1]}/{ano}"

def parse_mes_key(key: str) -> tuple[int, int]:
    # key no formato "YYYY-MM"
    a, m = key.split("-")
    return int(a), int(m)



DB = "finance_pessoal.db"

st.set_page_config(page_title="Finanças Pessoais", page_icon="💳", layout="wide")


# =========================
# Helpers
# =========================
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
    return (d2.year - d1.year) * 12 + (d2.month - d1.month) + 1


def fmt_currency(v) -> str:
    try:
        return f"R$ {float(v):,.2f}"
    except Exception:
        return "R$ 0,00"


def fmt_date_br(d) -> str:
    """15/01/2026"""
    if d is None or pd.isna(d):
        return "—"
    if isinstance(d, pd.Timestamp):
        d = d.date()
    if isinstance(d, str):
        d = pd.to_datetime(d, errors="coerce")
        if pd.isna(d):
            return "—"
        d = d.date()
    return d.strftime("%d/%m/%Y")


def fmt_month_br(ym: str) -> str:
    """2026-02 -> Fevereiro/2026"""
    if not ym:
        return "—"
    d = pd.to_datetime(f"{ym}-01", errors="coerce")
    if pd.isna(d):
        return "—"
    return d.strftime("%B/%Y").capitalize()


def fmt_installment(installment_no, installments_total, ym) -> str:
    """3ª de 6 • Março"""
    if pd.isna(installment_no) or pd.isna(installments_total):
        return "—"
    month = fmt_month_br(ym).split("/")[0]
    return f"{int(installment_no)}ª de {int(installments_total)} • {month}"


def map_accounts(accounts_df: pd.DataFrame) -> dict:
    return {int(r["id"]): str(r["name"]) for _, r in accounts_df.iterrows()}


def map_cards(cards_df: pd.DataFrame) -> dict:
    mp = {}
    for _, r in cards_df.iterrows():
        last4 = (r.get("last4", "") or "----")
        mp[int(r["id"])] = f'{r["name"]} •••• {last4}'
    return mp


def tx_signature(dt_, kind, amount, category, description, status, method,
                 account_id, card_id, statement_month, installments_total):
    return str((
        str(dt_), kind, round(float(amount or 0), 2),
        (category or "").strip().lower(),
        (description or "").strip().lower(),
        status, method,
        int(account_id) if account_id else None,
        int(card_id) if card_id else None,
        statement_month or "",
        int(installments_total) if installments_total else 1
    ))


# =========================
# Login (Streamlit Secrets)
# =========================
def require_login():
    pw_secret = st.secrets.get("APP_PASSWORD", None)

    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False

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
        cols_cards = table_columns(con, "cards")
        if "last4" not in cols_cards:
            con.execute("ALTER TABLE cards ADD COLUMN last4 TEXT;")

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

        con.execute("""
        CREATE TABLE IF NOT EXISTS category_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL UNIQUE,
            class TEXT NOT NULL CHECK(class IN ('ESSENTIAL','DISCRETIONARY'))
        );
        """)

        # Transferências (Conta -> Conta)
        con.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dt TEXT NOT NULL,
            amount REAL NOT NULL,
            from_account_id INTEGER NOT NULL,
            to_account_id INTEGER NOT NULL,
            description TEXT,
            status TEXT NOT NULL CHECK(status IN ('PENDING','PAID')) DEFAULT 'PAID',
            FOREIGN KEY(from_account_id) REFERENCES accounts(id),
            FOREIGN KEY(to_account_id) REFERENCES accounts(id)
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

    # cria conta Reserva/Investimentos se não existir
    with conectar() as con:
        exists = con.execute(
            "SELECT COUNT(*) FROM accounts WHERE LOWER(name)=LOWER(?)",
            ("Reserva/Investimentos",)
        ).fetchone()[0]
        if exists == 0:
            con.execute(
                "INSERT INTO accounts (name,type,initial_balance) VALUES (?,?,?)",
                ("Reserva/Investimentos", "BANK", 0)
            )
        con.commit()

    if g == 0:
        with conectar() as con:
            con.execute("INSERT INTO goals (name, monthly_target) VALUES (?,?)", ("Economia do mês", 0))
            con.commit()

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

    df["dt"] = to_dt(df["dt"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    for c in ["category", "description", "statement_month"]:
        df[c] = df[c].fillna("")
    for c in ["installments_total", "installment_no", "recurrence_id"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def carregar_transfers():
    with conectar() as con:
        df = pd.read_sql_query("SELECT * FROM transfers ORDER BY dt DESC, id DESC", con)
    df["dt"] = to_dt(df["dt"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["description"] = df["description"].fillna("")
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


def add_transfer(dt_: date, amount: float, from_account_id: int, to_account_id: int, description: str, status: str):
    with conectar() as con:
        con.execute("""
            INSERT INTO transfers (dt, amount, from_account_id, to_account_id, description, status)
            VALUES (?,?,?,?,?,?)
        """, (dt_.isoformat(), float(amount), int(from_account_id), int(to_account_id), description or None, status))
        con.commit()


def delete_transfer(transfer_id: int):
    with conectar() as con:
        con.execute("DELETE FROM transfers WHERE id=?", (int(transfer_id),))
        con.commit()


def atualizar_cartao(card_id: int, name: str, closing_day: int, due_day: int, pay_account_id: int, last4: str):
    with conectar() as con:
        con.execute("""
            UPDATE cards
            SET name=?, closing_day=?, due_day=?, pay_account_id=?, last4=?
            WHERE id=?
        """, (name.strip(), int(closing_day), int(due_day), int(pay_account_id), (last4 or "").strip(), int(card_id)))
        con.commit()


def calc_account_balance(account_id: int, tx: pd.DataFrame, accounts: pd.DataFrame) -> float:
    init = float(accounts.loc[accounts["id"] == account_id, "initial_balance"].iloc[0])
    df = tx[tx["status"] == "PAID"].copy()

    df_bank = df[(df["method"].isin(["BANK", "CASH"])) & (df["account_id"] == account_id)]
    incomes = df_bank[df_bank["kind"] == "INCOME"]["amount"].sum()
    expenses = df_bank[df_bank["kind"] == "EXPENSE"]["amount"].sum()

    df_pay = df[(df["method"] == "CARD_PAYMENT") & (df["account_id"] == account_id)]
    pay_out = df_pay["amount"].sum()

    tr = carregar_transfers()
    tr_paid = tr[tr["status"] == "PAID"]
    out_tr = tr_paid[tr_paid["from_account_id"] == account_id]["amount"].sum()
    in_tr = tr_paid[tr_paid["to_account_id"] == account_id]["amount"].sum()

    return init + float(incomes) - float(expenses) - float(pay_out) - float(out_tr) + float(in_tr)


def card_statement_detail(card_id: int, statement_month: str, tx: pd.DataFrame) -> pd.DataFrame:
    return tx[(tx["method"] == "CARD") & (tx["card_id"] == card_id) & (tx["statement_month"] == statement_month)].copy()


def card_statement_total(card_id: int, statement_month: str, tx: pd.DataFrame) -> float:
    return float(card_statement_detail(card_id, statement_month, tx)["amount"].sum())


def create_installments_on_card(dt_: date, total_amount: float, n: int, category: str, description: str,
                                card_id: int, closing_day: int, status: str):
    # Divide total em n parcelas, ajustando centavos na última
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
            description=f"{description} ({i}/{n})" if description else f"Parcela ({i}/{n})",
            status=status,
            method="CARD",
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
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)  # exclusivo

    existing_ids = set(
        tx[(tx["dt"] >= start_ts) & (tx["dt"] < end_ts)]["recurrence_id"].dropna().astype(int).tolist()
    )

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
# Long goal + category rules
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

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    period = tx[(tx["dt"] >= start_ts) & (tx["dt"] < end_ts) & (tx["status"] == "PAID")].copy()

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
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)

    month_paid = tx[(tx["dt"] >= start_ts) & (tx["dt"] < end_ts) & (tx["status"] == "PAID")].copy()
    income = float(month_paid[month_paid["kind"] == "INCOME"]["amount"].sum())
    out_bank_cash = float(month_paid[(month_paid["kind"] == "EXPENSE") & (month_paid["method"].isin(["BANK", "CASH"]))]["amount"].sum())
    card_pay = float(month_paid[(month_paid["method"] == "CARD_PAYMENT")]["amount"].sum())
    return income - out_bank_cash - card_pay


def is_discretionary(category: str, rules_df: pd.DataFrame) -> bool:
    cat = (category or "").strip().lower()
    if not cat or rules_df.empty:
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

st.title("💳 Finanças Pessoais")
st.caption("Contas, cartão de crédito, metas, recorrências, parcelamentos, transferências e relatórios.")

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
# Dashboard (modelo 1.0)
# =========================
with tabs[0]:
    tx = carregar_transactions()
    cards = carregar_cards()
    accounts = carregar_accounts()

    hoje = date.today()
    hoje_ym = hoje.strftime("%Y-%m")

    # meses do filtro: meses por dt + meses por statement_month + mês atual
    all_months = sorted(
        {d.strftime("%Y-%m") for d in pd.to_datetime(tx["dt"], errors="coerce").dropna()}
        | set(tx.loc[(tx["method"] == "CARD") & (tx["statement_month"] != ""), "statement_month"].astype(str).tolist())
        | {hoje_ym}
    )

    ym = st.selectbox(
        "📅 Mês",
        all_months,
        index=all_months.index(hoje_ym) if hoje_ym in all_months else 0,
        format_func=fmt_month_br,
        key="dash_month"
    )

    # Fluxo de caixa do mês (por dt)
    start, end = month_range(ym)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)

    month_paid = tx[(tx["status"] == "PAID") & (tx["dt"] >= start_ts) & (tx["dt"] < end_ts)].copy()

    income = float(month_paid[month_paid["kind"] == "INCOME"]["amount"].sum())
    expense_bank = float(
        month_paid[(month_paid["kind"] == "EXPENSE") & (month_paid["method"].isin(["BANK", "CASH"]))]["amount"].sum()
    )
    card_pay = float(month_paid[month_paid["method"] == "CARD_PAYMENT"]["amount"].sum())
    economy = income - expense_bank - card_pay

    # BLOCO 1 — métricas
    if mobile_mode:
        c1, c2 = st.columns(2)
        c1.metric("💰 Entradas", fmt_currency(income))
        c2.metric("💸 Saídas", fmt_currency(expense_bank))
        c3, c4 = st.columns(2)
        c3.metric("💳 Pag. fatura", fmt_currency(card_pay))
        c4.metric("📈 Economia", fmt_currency(economy))
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("💰 Entradas", fmt_currency(income))
        c2.metric("💸 Saídas", fmt_currency(expense_bank))
        c3.metric("💳 Pagamentos de fatura", fmt_currency(card_pay))
        c4.metric("📈 Economia", fmt_currency(economy))

    st.divider()

    # BLOCO 2 — cartões
    st.subheader("💳 Cartões do mês")

    WARN_PCT = 20.0
    HIGH_PCT = 30.0

    if income <= 0:
        st.warning("⚠️ Nenhuma renda registrada neste mês (as porcentagens dos cartões ficam desativadas).")

    if cards.empty:
        st.info("Nenhum cartão cadastrado.")
    else:
        per_row = 1 if mobile_mode else 3
        grid = st.columns(per_row)

        for i, row in enumerate(cards.itertuples(index=False)):
            total_stmt = card_statement_total(row.id, ym, tx)
            last4 = getattr(row, "last4", "") or "----"

            if income > 0:
                pct = (total_stmt / income) * 100
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
                        border-radius: 16px;
                        padding: 16px;
                        border: 1px solid rgba(255,255,255,0.10);
                        background: rgba(255,255,255,0.035);
                        margin-bottom: 12px;
                    ">
                        <div style="display:flex; justify-content:space-between; align-items:center; gap:10px;">
                            <div style="font-weight:700; font-size:16px;">💳 {row.name}</div>
                            <div style="font-size:12px; opacity:0.95;">{badge}</div>
                        </div>
                        <div style="opacity:0.70; margin-top:2px;">Final •••• {last4}</div>
                        <div style="margin-top:10px; font-size:13px; opacity:0.75;">Fatura do mês</div>
                        <div style="font-size:22px; font-weight:800;">{fmt_currency(total_stmt)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

    st.divider()

    # BLOCO 3 — insights
    st.subheader("🧠 Insights do mês (cartões)")

    if cards.empty:
        st.info("Cadastre cartões para ver insights.")
    else:
        rows = []
        for row in cards.itertuples(index=False):
            total_stmt = float(card_statement_total(row.id, ym, tx))
            last4 = getattr(row, "last4", "") or "----"
            pct = (total_stmt / income) * 100 if income > 0 else None
            rows.append({
                "Cartão": f"{row.name} •••• {last4}",
                "Total": total_stmt,
                "% da renda": pct
            })

        df_cards = pd.DataFrame(rows).sort_values("Total", ascending=False)
        if df_cards["Total"].sum() <= 0:
            st.info("Sem gastos em cartão neste mês.")
        else:
            top3 = df_cards.head(3).copy()
            top3_view = top3.copy()
            top3_view["Total"] = top3_view["Total"].map(fmt_currency)
            top3_view["% da renda"] = top3_view["% da renda"].map(lambda x: "—" if pd.isna(x) else f"{x:.1f}%")
            st.dataframe(top3_view, use_container_width=True, hide_index=True)

            if income > 0:
                high = df_cards[df_cards["% da renda"] >= HIGH_PCT]
                warn = df_cards[(df_cards["% da renda"] >= WARN_PCT) & (df_cards["% da renda"] < HIGH_PCT)]

                if high.empty and warn.empty:
                    st.success("✅ Seus cartões estão em zona verde/ok (nenhum acima de 20% da renda).")
                else:
                    if not high.empty:
                        st.markdown("**🔴 Cartões em nível Alto (≥ 30%)**")
                        for _, r in high.iterrows():
                            limit_high = (HIGH_PCT / 100) * income
                            limit_warn = (WARN_PCT / 100) * income
                            reduce_to_high = max(0.0, float(r["Total"]) - limit_high)
                            reduce_to_warn = max(0.0, float(r["Total"]) - limit_warn)
                            st.write(
                                f"- **{r['Cartão']}**: {r['% da renda']:.1f}% "
                                f"→ reduzir **{fmt_currency(reduce_to_high)}** para ficar < 30% "
                                f"(e **{fmt_currency(reduce_to_warn)}** para ficar < 20%)."
                            )
                    if not warn.empty:
                        st.markdown("**🟡 Cartões em Atenção (≥ 20%)**")
                        for _, r in warn.iterrows():
                            limit_warn = (WARN_PCT / 100) * income
                            reduce_to_warn = max(0.0, float(r["Total"]) - limit_warn)
                            st.write(
                                f"- **{r['Cartão']}**: {r['% da renda']:.1f}% "
                                f"→ reduzir **{fmt_currency(reduce_to_warn)}** para ficar < 20%."
                            )
            else:
                st.info("Lance uma entrada (ex: salário) para habilitar alertas por % da renda.")

    st.divider()

    # BLOCO 4 — saldos
    st.subheader("🏦 Saldos das contas")
    bal_rows = [{"Conta": a["name"], "Tipo": a["type"], "Saldo": calc_account_balance(int(a["id"]), tx, accounts)}
                for _, a in accounts.iterrows()]
    df_bal = pd.DataFrame(bal_rows)
    df_bal["Saldo"] = df_bal["Saldo"].map(fmt_currency)
    st.dataframe(df_bal, use_container_width=True, hide_index=True)

    # Aviso final do mês
    if economy < 0:
        st.error("🚨 Você gastou mais do que ganhou neste mês.")
    elif economy == 0:
        st.info("ℹ️ Mês zerado. Não houve economia.")
    else:
        st.success("✅ Você conseguiu economizar neste mês.")


# =========================
# Lançamentos + Transferências
# =========================
with tabs[1]:
    st.subheader("Adicionar lançamento")

    accounts = carregar_accounts()
    cards = carregar_cards()
    rules = carregar_category_rules()
    tx = carregar_transactions()

    acc_map = map_accounts(accounts)
    card_map = map_cards(cards)

    with st.form("form_lancamento", clear_on_submit=False):
        colA, colB, colC, colD = st.columns(4) if not mobile_mode else (st.columns(2) + st.columns(2))

        with colA:
            kind = st.selectbox("Tipo", ["INCOME", "EXPENSE"],
                                format_func=lambda x: "Entrada" if x == "INCOME" else "Saída")
        with colB:
            method = st.selectbox("Meio", ["BANK", "CASH", "CARD"],
                                  format_func=lambda x: {"BANK": "Conta", "CASH": "Dinheiro", "CARD": "Cartão"}[x])
        with colC:
            dt_ = st.date_input("Data", value=date.today())
        with colD:
            status = st.selectbox("Status", ["PAID", "PENDING"],
                                  format_func=lambda x: "Pago" if x == "PAID" else "Pendente")

        if mobile_mode:
            amount = st.number_input("Valor", min_value=0.0, step=10.0)
            category = st.text_input("Categoria", placeholder="Ex: Moradia, Mercado, Salário...")
            description = st.text_input("Descrição", placeholder="Opcional")
        else:
            col1, col2, col3 = st.columns(3)
            with col1:
                amount = st.number_input("Valor", min_value=0.0, step=10.0)
            with col2:
                category = st.text_input("Categoria", placeholder="Ex: Moradia, Mercado, Salário...")
            with col3:
                description = st.text_input("Descrição", placeholder="Opcional")

        if (category or "").strip() and is_discretionary(category, rules):
            st.info("🏷️ Categoria classificada como **Discricionária** (pode gerar alerta na meta por prazo).")

        account_id = None
        card_id = None
        statement_month = None
        installments_total = 1
        closing_day = None

        if method in ["BANK", "CASH"]:
            acc_opts = accounts[accounts["type"] == ("BANK" if method == "BANK" else "CASH")].copy()
            if acc_opts.empty:
                st.warning("Você não tem conta desse tipo cadastrada. Vá em 'Contas' e crie uma.")
            else:
                account_id = st.selectbox(
                    "Conta",
                    acc_opts["id"].tolist(),
                    format_func=lambda i: acc_map.get(int(i), str(i))
                )
        else:
            if cards.empty:
                st.warning("Você não tem cartão cadastrado. Vá em 'Cartões' e crie um.")
            else:
                card_id = st.selectbox(
                    "Cartão",
                    cards["id"].tolist(),
                    format_func=lambda i: card_map.get(int(i), str(i))
                )
                closing_day = int(cards.loc[cards["id"] == card_id, "closing_day"].iloc[0])
                installments_total = st.number_input("Parcelas", min_value=1, max_value=36, value=1, step=1)
                statement_month = compute_statement_month(dt_, closing_day)
                st.caption(f"📌 Vai para a fatura: **{fmt_month_br(statement_month)}** (fechamento dia {closing_day})")

        # alerta meta por prazo (gastos discricionários)
        lg = carregar_long_goal()
        if kind == "EXPENSE" and status == "PAID" and (category or "").strip() and not lg.empty and is_discretionary(category, rules):
            goal_row = lg.iloc[0].to_dict()
            plan = calc_long_goal_plan(goal_row, tx)
            required_per_month = float(plan["need_per_month"])
            ym_tx = dt_.strftime("%Y-%m")
            current_save = current_month_savings(tx, ym_tx)

            if required_per_month > 0 and current_save < required_per_month:
                gap = required_per_month - current_save
                st.warning(
                    f"⚠️ Gasto **discricionário** e você está abaixo do necessário para a meta.\n\n"
                    f"- Meta: **{goal_row['name']}**\n"
                    f"- Necessário/mês (média): **{fmt_currency(required_per_month)}**\n"
                    f"- Economia do mês (estimada): **{fmt_currency(current_save)}**\n"
                    f"- Falta para bater a média: **{fmt_currency(gap)}**\n\n"
                    f"💡 Sugestão: compense economizando **+{fmt_currency(float(amount))}** até o fim do mês."
                )

        submitted = st.form_submit_button("Salvar lançamento ✅", use_container_width=True)

    if submitted:
        sig = tx_signature(dt_, kind, amount, category, description, status, method,
                           account_id, card_id, statement_month, installments_total)

        if st.session_state.get("last_tx_sig") == sig:
            st.warning("Esse lançamento parece ter sido enviado duas vezes. Evitei duplicar ✅")
        else:
            st.session_state["last_tx_sig"] = sig

            if method == "CARD" and installments_total and int(installments_total) > 1:
                create_installments_on_card(dt_, amount, int(installments_total), category, description,
                                            int(card_id), int(closing_day), status)
                st.success(
                    f"✅ Lançamento parcelado salvo\n\n"
                    f"💳 Cartão: **{card_map.get(int(card_id))}**\n"
                    f"📆 **{int(installments_total)}x** (veja em 'Cartões' → faturas)\n"
                    f"🧾 Começa em: **{fmt_month_br(statement_month)}**"
                )
            else:
                add_transaction(dt_, kind, amount, category, description, status, method,
                                account_id, card_id, statement_month)
                st.success("✅ Lançamento salvo!")

        st.rerun()

    # Transferências
    st.divider()
    st.subheader("🔁 Transferência (Conta → Conta)")
    st.caption("Transferência não conta como despesa. Use para mover dinheiro para Reserva/Investimentos, poupança, etc.")

    accounts = carregar_accounts()
    tr = carregar_transfers()
    acc_map = map_accounts(accounts)

    if accounts.empty or len(accounts) < 2:
        st.warning("Você precisa de pelo menos 2 contas cadastradas para transferir.")
    else:
        with st.form("form_transfer", clear_on_submit=True):
            if mobile_mode:
                tr_date = st.date_input("Data", value=date.today(), key="tr_date")
                tr_status = st.selectbox("Status", ["PAID", "PENDING"],
                                         format_func=lambda x: "Pago" if x == "PAID" else "Pendente",
                                         key="tr_status")
                tr_amount = st.number_input("Valor", min_value=0.0, step=50.0, key="tr_amount")
            else:
                c1, c2, c3 = st.columns(3)
                with c1:
                    tr_date = st.date_input("Data", value=date.today(), key="tr_date")
                with c2:
                    tr_status = st.selectbox("Status", ["PAID", "PENDING"],
                                             format_func=lambda x: "Pago" if x == "PAID" else "Pendente",
                                             key="tr_status")
                with c3:
                    tr_amount = st.number_input("Valor", min_value=0.0, step=50.0, key="tr_amount")

            ids = accounts["id"].tolist()

            if mobile_mode:
                from_id = st.selectbox("De (origem)", ids, format_func=lambda i: acc_map.get(int(i), str(i)), key="tr_from")
                to_id = st.selectbox("Para (destino)", ids, format_func=lambda i: acc_map.get(int(i), str(i)), key="tr_to")
                tr_desc = st.text_input("Descrição", placeholder="Ex: aporte do mês", key="tr_desc")
            else:
                c4, c5 = st.columns(2)
                with c4:
                    from_id = st.selectbox("De (origem)", ids, format_func=lambda i: acc_map.get(int(i), str(i)), key="tr_from")
                with c5:
                    to_id = st.selectbox("Para (destino)", ids, format_func=lambda i: acc_map.get(int(i), str(i)), key="tr_to")
                tr_desc = st.text_input("Descrição", placeholder="Ex: aporte do mês", key="tr_desc")

            tr_submit = st.form_submit_button("Salvar transferência ✅", use_container_width=True)

        if tr_submit:
            if from_id == to_id:
                st.error("Origem e destino não podem ser a mesma conta.")
            elif tr_amount <= 0:
                st.error("Informe um valor maior que zero.")
            else:
                add_transfer(tr_date, tr_amount, int(from_id), int(to_id), tr_desc, tr_status)
                st.success("Transferência registrada!")
                st.rerun()

        st.markdown("### Últimas transferências")
        tr = carregar_transfers()
        if tr.empty:
            st.info("Nenhuma transferência registrada ainda.")
        else:
            view = tr.copy()
            view["Data"] = view["dt"].apply(fmt_date_br)
            view["De"] = view["from_account_id"].astype(int).map(lambda i: acc_map.get(i, "—"))
            view["Para"] = view["to_account_id"].astype(int).map(lambda i: acc_map.get(i, "—"))
            view["Status"] = view["status"].map({"PAID": "Pago", "PENDING": "Pendente"})
            view["Valor"] = view["amount"].map(fmt_currency)
            st.dataframe(view[["id", "Data", "Valor", "De", "Para", "Status", "description"]],
                         use_container_width=True, hide_index=True)

        with st.expander("🗑️ Excluir transferência"):
            tr_del_id = st.number_input("ID da transferência", min_value=0, step=1, key="tr_del_id")
            if st.button("Excluir transferência", type="secondary", key="tr_del_btn"):
                if tr_del_id > 0:
                    delete_transfer(int(tr_del_id))
                    st.success("Transferência excluída.")
                    st.rerun()

    # Últimos lançamentos (checkbox + ações)
    st.divider()
    st.subheader("Últimos lançamentos")

    tx = carregar_transactions()
    accounts = carregar_accounts()
    cards = carregar_cards()
    acc_map = map_accounts(accounts)
    card_map = map_cards(cards)

    tx_view = tx.copy()
    tx_view["Data"] = tx_view["dt"].apply(fmt_date_br)
    tx_view["Tipo"] = tx_view["kind"].map({"INCOME": "Entrada", "EXPENSE": "Saída"})
    tx_view["Status"] = tx_view["status"].map({"PAID": "Pago", "PENDING": "Pendente"})
    tx_view["Meio"] = tx_view["method"].map({"BANK": "Conta", "CASH": "Dinheiro", "CARD": "Cartão", "CARD_PAYMENT": "Pag. Cartão"})
    tx_view["Categoria"] = tx_view["category"].replace("", "—")
    tx_view["Descrição"] = tx_view["description"].replace("", "—")
    tx_view["Conta"] = tx_view["account_id"].fillna(0).astype(int).map(lambda i: acc_map.get(i, "—"))
    tx_view["Cartão"] = tx_view["card_id"].fillna(0).astype(int).map(lambda i: card_map.get(i, "—"))

    tx_view["Parcela"] = tx_view.apply(
        lambda r: fmt_installment(r.get("installment_no"), r.get("installments_total"), r.get("statement_month")),
        axis=1
    )

    tx_view["Valor"] = tx_view["amount"].astype(float)
    tx_view["Total (parcelado)"] = tx_view.apply(
        lambda r: float(r["amount"]) * int(r["installments_total"])
        if pd.notna(r.get("installments_total")) and int(r.get("installments_total") or 0) > 1 else float(r["amount"]),
        axis=1
    )

    tx_view = tx_view.sort_values(["dt", "id"], ascending=[False, False]).head(200)

    editor_df = tx_view[["id", "Data", "Tipo", "Status", "Meio", "Valor", "Total (parcelado)",
                         "Categoria", "Descrição", "Conta", "Cartão", "Parcela"]].copy()
    editor_df.insert(0, "Selecionar", False)

    edited = st.data_editor(
        editor_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Valor": st.column_config.NumberColumn(format="R$ %.2f"),
            "Total (parcelado)": st.column_config.NumberColumn(format="R$ %.2f"),
            "Selecionar": st.column_config.CheckboxColumn()
        },
        key="tx_editor"
    )

    selected_ids = edited.loc[edited["Selecionar"] == True, "id"].astype(int).tolist()

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Marcar como pago ✅", use_container_width=True, disabled=(len(selected_ids) == 0), key="btn_paid"):
            with conectar() as con:
                con.executemany("UPDATE transactions SET status='PAID' WHERE id=?", [(i,) for i in selected_ids])
                con.commit()
            st.success("Atualizado para Pago.")
            st.rerun()

    with c2:
        if st.button("Marcar como pendente ⏳", use_container_width=True, disabled=(len(selected_ids) == 0), key="btn_pending"):
            with conectar() as con:
                con.executemany("UPDATE transactions SET status='PENDING' WHERE id=?", [(i,) for i in selected_ids])
                con.commit()
            st.success("Atualizado para Pendente.")
            st.rerun()

    with c3:
        if st.button("Excluir selecionados 🗑️", use_container_width=True, disabled=(len(selected_ids) == 0), key="btn_del"):
            with conectar() as con:
                con.executemany("DELETE FROM transactions WHERE id=?", [(i,) for i in selected_ids])
                con.commit()
            st.success("Excluídos.")
            st.rerun()


# =========================
# Cartões (cadastro + edição + faturas)
# =========================
with tabs[2]:
    st.subheader("💳 Cartões de crédito")

    accounts = carregar_accounts()
    cards = carregar_cards()

    st.markdown("### Cadastrar cartão")
    card_name = st.text_input("Nome do cartão", key="card_name")
    closing_day = st.number_input("Dia de fechamento (1-28)", min_value=1, max_value=28, value=10, key="card_close")
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
    st.markdown("### ✏️ Editar cartão")

    cards = carregar_cards()
    accounts = carregar_accounts()
    if cards.empty:
        st.info("Nenhum cartão cadastrado.")
    else:
        card_edit_id = st.selectbox(
            "Selecione o cartão",
            cards["id"].tolist(),
            format_func=lambda i: cards.loc[cards["id"] == i, "name"].iloc[0],
            key="edit_card_sel"
        )
        card_row = cards[cards["id"] == card_edit_id].iloc[0]

        new_name = st.text_input("Nome", value=card_row["name"], key="edit_card_name")
        new_closing = st.number_input("Fechamento", min_value=1, max_value=28,
                                      value=int(card_row["closing_day"]), key="edit_card_close")
        new_due = st.number_input("Vencimento", min_value=1, max_value=28,
                                  value=int(card_row["due_day"]), key="edit_card_due")

        bank_accs = accounts[accounts["type"] == "BANK"].copy()
        pay_ids = bank_accs["id"].tolist()
        current_pay = int(card_row["pay_account_id"]) if pd.notna(card_row["pay_account_id"]) else pay_ids[0]
        index_pay = pay_ids.index(current_pay) if current_pay in pay_ids else 0

        new_pay_acc = st.selectbox(
            "Conta para pagar fatura",
            pay_ids,
            index=index_pay,
            format_func=lambda i: bank_accs.loc[bank_accs["id"] == i, "name"].iloc[0],
            key="edit_card_pay"
        )

        new_last4 = st.text_input("Final do cartão", value=str(card_row.get("last4", "") or ""), max_chars=4, key="edit_card_last4")

        if st.button("Salvar alterações do cartão 💾", use_container_width=True, key="edit_card_save"):
            atualizar_cartao(card_edit_id, new_name, new_closing, new_due, int(new_pay_acc), new_last4)
            st.success("Cartão atualizado com sucesso!")
            st.rerun()

    st.divider()
    st.markdown("### Faturas")

    cards = carregar_cards()
    tx = carregar_transactions()
    accounts = carregar_accounts()

    if cards.empty:
        st.info("Cadastre um cartão para ver faturas.")
    else:
        cid = st.selectbox("Cartão", cards["id"].tolist(),
                           format_func=lambda i: map_cards(cards).get(int(i), str(i)),
                           key="stmt_card")

        months = sorted(set(tx[(tx["method"] == "CARD") & (tx["card_id"] == cid)]["statement_month"]) - {""})
        stmt = st.selectbox("Fatura", months, index=len(months) - 1, format_func=fmt_month_br, key="stmt_month") if months else None

        if stmt:
            total = card_statement_total(cid, stmt, tx)
            st.metric("Total da fatura", fmt_currency(total))

            detail = card_statement_detail(cid, stmt, tx).sort_values(["dt", "id"])
            det = detail.copy()
            det["Data"] = det["dt"].apply(fmt_date_br)
            det["Parcela"] = det.apply(lambda r: fmt_installment(r.get("installment_no"), r.get("installments_total"), r.get("statement_month")), axis=1)
            det["Valor"] = det["amount"].map(fmt_currency)

            st.dataframe(det[["Data", "Valor", "category", "description", "status", "Parcela"]],
                         use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("### Pagar fatura (lança saída na conta bancária)")
            pay_acc = int(cards.loc[cards["id"] == cid, "pay_account_id"].iloc[0])
            acc_name = accounts.loc[accounts["id"] == pay_acc, "name"].iloc[0]
            st.caption(f"Conta de pagamento configurada: **{acc_name}**")

            pay_date = st.date_input("Data do pagamento", value=date.today(), key="pay_date")
            pay_amount = st.number_input("Valor a pagar", min_value=0.0, value=float(total), step=50.0, key="pay_amount")

            if st.button("Registrar pagamento de fatura ✅", use_container_width=True, key="pay_btn"):
                add_transaction(pay_date, "EXPENSE", pay_amount, "Cartão", f"Pagamento fatura {fmt_month_br(stmt)}", "PAID",
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
    acc_map = map_accounts(accounts)
    card_map = map_cards(cards)

    with st.expander("➕ Criar recorrência"):
        r_name = st.text_input("Nome", placeholder="Ex: Aluguel", key="rec_name")
        r_kind = st.selectbox("Tipo", ["INCOME", "EXPENSE"],
                              format_func=lambda x: "Entrada" if x == "INCOME" else "Saída",
                              key="rec_kind")
        r_amount = st.number_input("Valor", min_value=0.0, step=10.0, key="rec_amount")
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
                                            format_func=lambda i: acc_map.get(int(i), str(i)),
                                            key="rec_account")
        else:
            if not cards.empty:
                r_card_id = st.selectbox("Cartão", cards["id"].tolist(),
                                         format_func=lambda i: card_map.get(int(i), str(i)),
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
    target_ym = st.text_input("Gerar recorrências para o mês", value=date.today().strftime("%Y-%m"), key="rec_target_ym")
    st.caption("Use o formato YYYY-MM (ex: 2026-01).")
    if st.button("Gerar recorrências do mês ✅", use_container_width=True, key="rec_run_btn"):
        created = run_recurrences_for_month(target_ym)
        st.success(f"Criados {created} lançamentos recorrentes para {fmt_month_br(target_ym)}.")
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
        format_func=fmt_month_br,
        key="rep_month"
    )

    start, end = month_range(ym)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)

    f = tx[(tx["dt"] >= start_ts) & (tx["dt"] < end_ts) & (tx["status"] == "PAID")].copy()

    f_exp = f[(f["kind"] == "EXPENSE") & (f["method"].isin(["BANK", "CASH", "CARD_PAYMENT"]))].copy()
    group = st.selectbox("Agrupar por", ["Categoria", "Conta", "Cartão"], key="rep_group")

    if f_exp.empty:
        st.info("Sem despesas pagas nesse mês.")
    else:
        if group == "Categoria":
            key_series = f_exp["category"].replace("", "Sem categoria")
        elif group == "Conta":
            mp = map_accounts(accounts)
            key_series = f_exp["account_id"].fillna(0).astype(int).map(lambda i: mp.get(i, "—"))
        else:
            mp = map_cards(cards)
            key_series = f_exp["card_id"].fillna(0).astype(int).map(lambda i: mp.get(i, "—"))

        tab = f_exp.groupby(key_series)["amount"].sum().sort_values(ascending=False)
        st.bar_chart(tab)
        df_tab = tab.reset_index()
        df_tab.columns = [group, "Total"]
        df_tab["Total"] = df_tab["Total"].map(fmt_currency)
        st.dataframe(df_tab, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Detalhamento do mês (pagos)")
    f2 = f.sort_values(["dt", "id"], ascending=[False, False]).copy()
    f2["Data"] = f2["dt"].apply(fmt_date_br)
    f2["Status"] = f2["status"].map({"PAID": "Pago", "PENDING": "Pendente"})
    f2["Meio"] = f2["method"].map({"BANK": "Conta", "CASH": "Dinheiro", "CARD": "Cartão", "CARD_PAYMENT": "Pag. Cartão"})
    f2["Valor"] = f2["amount"].map(fmt_currency)
    st.dataframe(f2[["Data", "kind", "Valor", "category", "description", "Status", "Meio", "statement_month"]],
                 use_container_width=True, hide_index=True)


# =========================
# Contas
# =========================
with tabs[5]:
    st.subheader("🏦 Contas")

    st.markdown("### Cadastrar conta")
    acc_name = st.text_input("Nome da conta", key="acc_name")
    acc_type = st.selectbox("Tipo", ["BANK", "CASH"],
                            format_func=lambda x: "Conta bancária" if x == "BANK" else "Dinheiro",
                            key="acc_type")
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
    st.markdown("### Saldos (pagos)")

    accounts = carregar_accounts()
    tx = carregar_transactions()
    rows = [{"Conta": a["name"], "Tipo": a["type"], "Saldo": calc_account_balance(int(a["id"]), tx, accounts)}
            for _, a in accounts.iterrows()]
    df = pd.DataFrame(rows)
    df["Saldo"] = df["Saldo"].map(fmt_currency)
    st.dataframe(df, use_container_width=True, hide_index=True)


# =========================
# Metas
# =========================
with tabs[6]:
    st.subheader("🎯 Metas")

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
    st.subheader("📅 Meta por prazo (ex: 1 ano)")

    lg = carregar_long_goal()
    with st.expander("➕ Criar/atualizar meta por prazo"):
        g_name = st.text_input("Nome da meta", value="Reserva / Objetivo", key="lg_name")
        g_target = st.number_input("Valor alvo (R$)", min_value=0.0, step=100.0, key="lg_target")
        g_start_amount = st.number_input("Já tenho (R$)", min_value=0.0, step=100.0, key="lg_start_amount")
        g_start = st.date_input("Data início", value=date.today(), key="lg_start")
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
        st.write(f"Período: **{fmt_date_br(plan['start_date'])}** até **{fmt_date_br(plan['end_date'])}**  |  Meses: **{plan['total_months']}**")

        if mobile_mode:
            st.metric("Valor alvo", fmt_currency(plan["target_amount"]))
            st.metric("Valor atual estimado", fmt_currency(plan["current_amount"]))
            st.metric("Falta", fmt_currency(plan["remaining"]))
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Valor alvo", fmt_currency(plan["target_amount"]))
            c2.metric("Valor atual estimado", fmt_currency(plan["current_amount"]))
            c3.metric("Falta", fmt_currency(plan["remaining"]))

        st.progress(plan["progress"])
        st.caption(f"{plan['progress']*100:.1f}% da meta")
        st.info(f"📌 Para bater a meta, você precisa poupar em média **{fmt_currency(plan['need_per_month'])} / mês** daqui pra frente.")

    st.divider()
    st.subheader("🏷️ Categorias: Essenciais x Discricionários")

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
    tr = carregar_transfers()

    st.download_button("⬇️ Lançamentos (CSV)", tx.to_csv(index=False).encode("utf-8"),
                       file_name="lancamentos.csv", mime="text/csv", use_container_width=True, key="dl_tx")
    st.download_button("⬇️ Transferências (CSV)", tr.to_csv(index=False).encode("utf-8"),
                       file_name="transferencias.csv", mime="text/csv", use_container_width=True, key="dl_tr")
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

    st.divider()
    st.warning("⚠️ No Streamlit Cloud o armazenamento pode resetar em updates. Faça backup com frequência.")
