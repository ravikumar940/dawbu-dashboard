# Dawbu – Product Demand & Stock-out Dashboard

Shows which products are in high demand (last 30 days of sales) and when each product will run out of stock.

## Update the data
Replace the two files in the `data/` folder (keep the same column names):

- `data/sales.csv` → `date, product, units_sold, price` (one row per product per day; `price` is optional)
- `data/inventory.csv` → `product, current_stock`

Or upload new CSVs from the sidebar of the live dashboard.

## Run on Render
- Build command: `pip install -r requirements.txt`
- Start command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
