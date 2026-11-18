"""
Dawbu - Product Demand & Stock-out Dashboard
--------------------------------------------
Reads two CSV files:
  data/sales.csv      -> date, product, units_sold, price (price is optional)
  data/inventory.csv  -> product, current_stock

You can also upload new CSV files from the sidebar while the app is running.
"""

from datetime import timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# COLORS - change these to Dawbu's brand colors if you want
# ---------------------------------------------------------------------------
COLORS = {
    "primary": "#2a78d6",      # main bars / lines (blue)
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e87ba4"],  # multi-line chart
    "reorder_now": "#d03b3b",  # red    -> stock runs out before new stock can arrive
    "order_soon": "#fab219",   # yellow -> stock runs out soon
    "healthy": "#0ca30c",      # green  -> enough stock
    "text": "#0b0b0b",
    "muted": "#52514e",
    "grid": "#e6e5e0",
}

STATUS_ORDER = ["🔴 Reorder now", "🟡 Order soon", "🟢 Healthy"]
STATUS_COLORS = {
    "🔴 Reorder now": COLORS["reorder_now"],
    "🟡 Order soon": COLORS["order_soon"],
    "🟢 Healthy": COLORS["healthy"],
}

st.set_page_config(page_title="Dawbu Demand Dashboard", page_icon="📦", layout="wide")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def style_chart(fig, height=420):
    """Same clean look for every chart."""
    fig.update_layout(
        height=height,
        template="plotly_white",
        font=dict(family="Inter, system-ui, sans-serif", size=13, color=COLORS["text"]),
        margin=dict(l=10, r=20, t=30, b=10),
        plot_bgcolor="white",
        paper_bgcolor="white",
        barcornerradius=4,
        hoverlabel=dict(bgcolor="white", font_size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title_text=""),
    )
    fig.update_xaxes(gridcolor=COLORS["grid"], zeroline=False, title_font_color=COLORS["muted"])
    fig.update_yaxes(gridcolor=COLORS["grid"], zeroline=False, title_font_color=COLORS["muted"])
    return fig


def read_csv(uploaded, default_path):
    if uploaded is not None:
        return pd.read_csv(uploaded)
    return pd.read_csv(default_path)


def check_columns(df, needed, name):
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        st.error(f"**{name}** is missing these columns: {', '.join(missing)}. "
                 f"Required columns: {', '.join(needed)}")
        st.stop()
    return df


# ---------------------------------------------------------------------------
# Sidebar - data + settings
# ---------------------------------------------------------------------------
st.sidebar.header("📂 Data")
sales_file = st.sidebar.file_uploader("Sales CSV (date, product, units_sold, price)", type="csv")
stock_file = st.sidebar.file_uploader("Inventory CSV (product, current_stock)", type="csv")
st.sidebar.caption("No upload? The app uses the files in the `data/` folder.")

st.sidebar.header("⚙️ Settings")
lead_time = st.sidebar.slider(
    "Supplier lead time (days)", 1, 30, 7,
    help="How many days it takes for new stock to arrive after you order it.",
)
top_n = st.sidebar.slider("Products to show in charts", 5, 30, 10)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
sales = check_columns(read_csv(sales_file, "data/sales.csv"), ["date", "product", "units_sold"], "Sales CSV")
stock = check_columns(read_csv(stock_file, "data/inventory.csv"), ["product", "current_stock"], "Inventory CSV")

sales["date"] = pd.to_datetime(sales["date"], errors="coerce")
sales = sales.dropna(subset=["date"])
sales["units_sold"] = pd.to_numeric(sales["units_sold"], errors="coerce").fillna(0)
stock["current_stock"] = pd.to_numeric(stock["current_stock"], errors="coerce").fillna(0)
has_price = "price" in sales.columns
if has_price:
    sales["price"] = pd.to_numeric(sales["price"], errors="coerce").fillna(0)
    sales["revenue"] = sales["units_sold"] * sales["price"]

if sales.empty:
    st.error("The sales file has no valid rows.")
    st.stop()

# Last 30 days, counted back from the latest date in the sales file
end_date = sales["date"].max().normalize()
start_date = end_date - timedelta(days=29)
last30 = sales[sales["date"] >= start_date]

# ---------------------------------------------------------------------------
# Calculations per product
# ---------------------------------------------------------------------------
agg = {"units_sold": "sum"}
if has_price:
    agg["revenue"] = "sum"
summary = last30.groupby("product", as_index=False).agg(agg).rename(columns={"units_sold": "units_30d"})
summary["avg_daily_sales"] = summary["units_30d"] / 30

# Trend: last 15 days vs the 15 days before
mid = start_date + timedelta(days=15)
first_half = last30[last30["date"] < mid].groupby("product")["units_sold"].sum()
second_half = last30[last30["date"] >= mid].groupby("product")["units_sold"].sum()
trend = ((second_half - first_half) / first_half.replace(0, float("nan")) * 100).round(0)
summary["trend_pct"] = summary["product"].map(trend).fillna(0)

# Demand level based on rank of units sold
rank = summary["units_30d"].rank(pct=True)
summary["demand"] = pd.cut(rank, bins=[0, 0.4, 0.75, 1.0], labels=["Low", "Medium", "High"],
                           include_lowest=True).astype(str)

# Join stock and work out the stock-out date
summary = summary.merge(stock[["product", "current_stock"]], on="product", how="left")
summary["current_stock"] = summary["current_stock"].fillna(0)
summary["days_left"] = summary.apply(
    lambda r: r["current_stock"] / r["avg_daily_sales"] if r["avg_daily_sales"] > 0 else 999, axis=1
).round(1)

today = pd.Timestamp.today().normalize()
summary["stockout_date"] = today + pd.to_timedelta(summary["days_left"].clip(upper=999), unit="D")
summary["reorder_by"] = summary["stockout_date"] - timedelta(days=lead_time)


def status_of(days):
    if days <= lead_time:
        return STATUS_ORDER[0]
    if days <= lead_time * 2:
        return STATUS_ORDER[1]
    return STATUS_ORDER[2]


summary["status"] = summary["days_left"].apply(status_of)
summary = summary.sort_values("units_30d", ascending=False).reset_index(drop=True)

# ---------------------------------------------------------------------------
# Header + KPI tiles
# ---------------------------------------------------------------------------
st.title("📦 Dawbu – Product Demand & Stock-out Dashboard")
st.caption(f"Sales from **{start_date:%d %b %Y}** to **{end_date:%d %b %Y}** (last 30 days) · "
           f"Stock-out dates counted from today, **{today:%d %b %Y}**")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Units sold (30 days)", f"{int(summary['units_30d'].sum()):,}")
if has_price:
    k2.metric("Revenue (30 days)", f"₹{summary['revenue'].sum():,.0f}")
else:
    k2.metric("Avg units / day", f"{summary['units_30d'].sum() / 30:,.0f}")
k3.metric("Top product", summary.iloc[0]["product"], f"{int(summary.iloc[0]['units_30d']):,} units",
          delta_color="off")
at_risk = int((summary["status"] == STATUS_ORDER[0]).sum())
k4.metric("🔴 Need reorder now", f"{at_risk} products", f"run out within {lead_time} days",
          delta_color="off")

st.divider()

# ---------------------------------------------------------------------------
# Row 1: High demand bar chart + daily sales line
# ---------------------------------------------------------------------------
c1, c2 = st.columns(2)

with c1:
    st.subheader("🔥 High-demand products (last 30 days)")
    top = summary.head(top_n).sort_values("units_30d")
    fig = px.bar(
        top, x="units_30d", y="product", orientation="h", text="units_30d",
        custom_data=["avg_daily_sales", "trend_pct", "demand"],
    )
    fig.update_traces(
        marker_color=COLORS["primary"], textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Units sold: %{x:,}<br>Avg / day: %{customdata[0]:.1f}"
                      "<br>Trend: %{customdata[1]:+.0f}%<br>Demand: %{customdata[2]}<extra></extra>",
    )
    fig.update_layout(xaxis_title="Units sold", yaxis_title="")
    st.plotly_chart(style_chart(fig), use_container_width=True)

with c2:
    st.subheader("📈 Daily sales trend")
    choices = summary["product"].tolist()
    picked = st.multiselect("Compare products", choices, default=choices[:3], max_selections=5)
    daily = (last30[last30["product"].isin(picked)]
             .groupby(["date", "product"], as_index=False)["units_sold"].sum())
    color_map = {p: COLORS["series"][i] for i, p in enumerate(picked)}
    fig = px.line(daily, x="date", y="units_sold", color="product", color_discrete_map=color_map,
                  category_orders={"product": picked})
    fig.update_traces(line_width=2,
                      hovertemplate="%{fullData.name}: %{y:,} units<extra></extra>")
    fig.update_layout(xaxis_title="", yaxis_title="Units sold per day", hovermode="x unified")
    st.plotly_chart(style_chart(fig, height=360), use_container_width=True)

# ---------------------------------------------------------------------------
# Row 2: Stock-out timeline
# ---------------------------------------------------------------------------
st.subheader("⏳ Stock-out timeline – when will each product run out?")
st.caption(f"Each bar runs from today to the day the product runs out at its current sales speed. "
           f"The dashed line is your reorder deadline (today + {lead_time}-day lead time).")

horizon = today + timedelta(days=90)
tl = summary.sort_values("days_left").head(top_n).copy()
tl["start"] = today
tl["end_shown"] = tl["stockout_date"].clip(upper=horizon)
tl["days_label"] = tl["days_left"].apply(lambda d: "90+ days" if d > 90 else f"{d:.0f} days")
tl["stockout_label"] = tl.apply(
    lambda r: "Not in next 90 days" if r["days_left"] > 90 else f"{r['stockout_date']:%d %b %Y}", axis=1)

fig = px.timeline(
    tl, x_start="start", x_end="end_shown", y="product", color="status",
    color_discrete_map=STATUS_COLORS, category_orders={"status": STATUS_ORDER},
    text="days_label",
    custom_data=["current_stock", "avg_daily_sales", "stockout_label", "days_label"],
)
fig.update_traces(
    textposition="inside", insidetextanchor="end",
    hovertemplate="<b>%{y}</b><br>Stock left: %{customdata[0]:,}<br>Sells / day: %{customdata[1]:.1f}"
                  "<br>Runs out: %{customdata[2]} (%{customdata[3]})<extra></extra>",
)
fig.update_yaxes(categoryorder="array", categoryarray=tl["product"].tolist()[::-1], title="")
fig.update_xaxes(title="")
deadline = today + timedelta(days=lead_time)
fig.add_shape(type="line", x0=deadline, x1=deadline, y0=0, y1=1, yref="paper",
              line=dict(color=COLORS["muted"], width=2, dash="dash"))
fig.add_annotation(x=deadline, y=1, yref="paper", text="Reorder deadline", showarrow=False,
                   yanchor="bottom", font=dict(color=COLORS["muted"], size=12))
st.plotly_chart(style_chart(fig, height=max(320, 42 * len(tl))), use_container_width=True)

# ---------------------------------------------------------------------------
# Row 3: Days of stock left + demand level split
# ---------------------------------------------------------------------------
c3, c4 = st.columns([2, 1])

with c3:
    st.subheader("📦 Days of stock left")
    ds = summary.sort_values("days_left").head(top_n).copy()
    ds["days_shown"] = ds["days_left"].clip(upper=90)
    fig = px.bar(ds, x="product", y="days_shown", color="status", color_discrete_map=STATUS_COLORS,
                 category_orders={"status": STATUS_ORDER, "product": ds["product"].tolist()},
                 custom_data=["days_left", "current_stock"])
    fig.update_traces(hovertemplate="<b>%{x}</b><br>Days left: %{customdata[0]:.0f}"
                                    "<br>Stock: %{customdata[1]:,}<extra></extra>")
    fig.add_hline(y=lead_time, line_dash="dash", line_color=COLORS["muted"], line_width=2,
                  annotation_text=f"Lead time ({lead_time} days)", annotation_position="top right")
    fig.update_layout(xaxis_title="", yaxis_title="Days left (max 90 shown)", bargap=0.35)
    st.plotly_chart(style_chart(fig, height=380), use_container_width=True)

with c4:
    st.subheader("🎯 Demand level")
    dl = summary.groupby("demand", as_index=False)["product"].count().rename(columns={"product": "count"})
    order = ["High", "Medium", "Low"]
    dl["demand"] = pd.Categorical(dl["demand"], order, ordered=True)
    dl = dl.sort_values("demand")
    fig = px.bar(dl, x="demand", y="count", text="count",
                 color="demand", color_discrete_map={"High": "#184f95", "Medium": "#3987e5", "Low": "#9ec5f4"})
    fig.update_traces(textposition="outside", cliponaxis=False,
                      hovertemplate="%{x} demand: %{y} products<extra></extra>")
    fig.update_layout(xaxis_title="", yaxis_title="Number of products", showlegend=False)
    st.plotly_chart(style_chart(fig, height=380), use_container_width=True)

# ---------------------------------------------------------------------------
# Full table + download
# ---------------------------------------------------------------------------
st.subheader("📋 All products")
table = summary.copy()
table["stockout_date"] = table.apply(
    lambda r: "No stock-out soon" if r["days_left"] >= 999 else f"{r['stockout_date']:%d %b %Y}", axis=1)
table["reorder_by"] = table.apply(
    lambda r: "-" if r["days_left"] >= 999 else
    ("Today!" if r["reorder_by"] <= today else f"{r['reorder_by']:%d %b %Y}"), axis=1)
cols = ["product", "demand", "units_30d", "avg_daily_sales", "trend_pct", "current_stock",
        "days_left", "stockout_date", "reorder_by", "status"]
if has_price:
    cols.insert(3, "revenue")
table = table[cols].rename(columns={
    "product": "Product", "demand": "Demand", "units_30d": "Units sold (30d)", "revenue": "Revenue (30d)",
    "avg_daily_sales": "Avg / day", "trend_pct": "Trend %", "current_stock": "Stock left",
    "days_left": "Days left", "stockout_date": "Runs out on", "reorder_by": "Reorder by", "status": "Status",
})
st.dataframe(
    table, use_container_width=True, hide_index=True,
    column_config={
        "Avg / day": st.column_config.NumberColumn(format="%.1f"),
        "Trend %": st.column_config.NumberColumn(format="%+.0f%%"),
        "Revenue (30d)": st.column_config.NumberColumn(format="₹%d"),
        "Days left": st.column_config.NumberColumn(format="%.0f"),
    },
)
st.download_button("⬇️ Download this table (CSV)", table.to_csv(index=False).encode("utf-8"),
                   file_name="dawbu_demand_report.csv", mime="text/csv")
