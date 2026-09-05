# ChargeEase — Final Setup

This package contains the complete college-project structure: Flask + MongoDB + frontend + C DSA bridge.

## 1. Prerequisites
- Windows 10/11
- Python 3.11+
- MSYS2 UCRT64 GCC (your existing `C:\msys64\ucrt64\bin\gcc.exe` is supported automatically)
- A MongoDB Atlas account

## 2. MongoDB Atlas
1. Create a free MongoDB Atlas cluster.
2. Create a database user (username + password).
3. In Atlas, open Network Access and add your current IP. For a temporary college demo you can use `0.0.0.0/0`, but restrict it later.
4. Click Connect → Drivers and copy the `mongodb+srv://...` connection string.
5. In the ChargeEase root folder, copy `.env.example` to `.env` and replace the placeholders in `MONGO_URI`.
6. Keep the password URL-safe; if it contains special characters, URL-encode them.

Example:
`MONGO_URI=mongodb+srv://myuser:mypassword@cluster0.xxxxx.mongodb.net/ChargeEase?retryWrites=true&w=majority`

## 3. First run
Open PowerShell in the ChargeEase folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Copy-Item .env.example .env
notepad .env
```

Paste your MongoDB URI in `.env`, save, then run:

```powershell
python backend\seed_stations.py
```

If you see `Seeded 4 ChargeEase stations successfully.`, MongoDB is connected.

Compile C programs once:

```powershell
gcc C_DSA\searching.c -o C_DSA\search_station.exe
gcc C_DSA\sorting.c -o C_DSA\sorting.exe
gcc C_DSA\queue.c -o C_DSA\queue.exe
```

Then:

```powershell
python backend\app.py
```

Open `http://127.0.0.1:5000/`.

## 4. Easiest future startup
After `.env` is configured, double-click `run.bat`. It creates/uses the venv, installs requirements, compiles C, seeds MongoDB and starts Flask.

## 5. C DSA connection
- `searching.c`: Linear Search by station ID. Flask sends the ID to `search_station.exe` using `subprocess`.
- `sorting.c`: Selection sort by distance. The app verifies the C sorting executable when the station page loads; MongoDB data is displayed sorted by distance.
- `queue.c`: Queue bridge scaffold for waiting vehicles.
- `structures.c`: Station structure helper.

The web app is the only thing you need to open. Do not separately run the C `.exe` files.

## Demo station IDs
101, 102, 103, 104

999 should show Not Found.
