# btc-polymarket-deribit-scanner

A very simple first version of a BTC-only Streamlit dashboard for comparing Polymarket-style BTC above/below contracts with a Deribit terminal probability proxy.

## What this starter app does

- Uses sample data only.
- Focuses on BTC above/below markets.
- Shows Polymarket yes mid, Deribit proxy probability, and edge.
- Provides simple sidebar filters.
- Is designed to be easy to deploy to Railway.

## Files

- `app.py` - main Streamlit app
- `requirements.txt` - Python packages
- `README.md` - project notes
- `.gitignore` - recommended ignored files

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```

## Deploy to Railway

1. Create a new GitHub repo named `btc-polymarket-deribit-scanner`.
2. Add the files from this starter pack.
3. Push the repo to GitHub.
4. In Railway, click **New Project**.
5. Choose **Deploy from GitHub repo**.
6. Select your repo.
7. Set the start command to:

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0
```

## Next upgrades

1. Replace sample data with real BTC Polymarket data.
2. Add parsing for above/below titles and expiry rules.
3. Add live Deribit option data.
4. Build a real digital-probability proxy engine.
5. Add confidence flags and better ranking logic.
