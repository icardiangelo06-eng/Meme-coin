# Meme Coin Discord Bot — Railway Ready

This version is packaged for Railway so it can run 24/7 without your phone or computer staying on.

## What it does
- Scans Solana meme coins every 15 seconds
- Targets roughly $20K–$150K market cap by default
- Filters for liquidity, volume, buyers, freshness, and momentum
- Sends qualifying alerts to a Discord channel
- Includes the full contract address
- Signal-only: it does NOT automatically buy or sell

## Easiest phone setup

### 1. Create your Discord webhook
In Discord:
- Open your server
- Open the alert channel
- Edit Channel
- Integrations
- Webhooks
- New Webhook
- Copy Webhook URL

Keep that URL private.

### 2. Put this project on GitHub
Create a new GitHub repository and upload all files from this folder.

### 3. Deploy on Railway
Create a Railway project and deploy the GitHub repository.

Railway can use the included `railway.json` / Dockerfile and start the scanner with:

`python bot.py`

### 4. Add the Discord webhook variable
Inside your Railway service, open Variables and add:

`DISCORD_WEBHOOK_URL`

Paste your Discord webhook URL as the value.

### 5. Deploy/redeploy
Once deployed, open Railway logs. You should see:

`Meme Coin Signal Bot`

The scanner will keep running in the cloud.

## Optional tuning variables

You can add these in Railway Variables:

- `SCAN_SECONDS=15`
- `MIN_MC=20000`
- `MAX_MC=150000`
- `MIN_LIQ=8000`
- `MIN_VOL_1H=5000`
- `MIN_BUYS_1H=25`
- `MAX_AGE_HOURS=24`
- `MIN_SCORE=70`

## Important
The bot's risk checks are market-data heuristics, not a complete smart-contract audit. Low-cap meme coins can still rug, lose liquidity, or crash quickly.
