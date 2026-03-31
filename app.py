import os
import time
import requests
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'portfolio.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

TWELVE_DATA_KEY = "0eef54e01c5b4f6aa18c054d569084de"

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(15), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0)
    last_signal = db.Column(db.String(10), default="HOLD")
    tech_details = db.Column(db.String(200), default="Se încarcă...")

with app.app_context():
    db.create_all()

def get_bvb_data(symbol):
    """Extrage prețul pentru RO folosind direct API-ul de query Yahoo"""
    # Convertim H2O.RO in H2O.BVB daca e cazul
    clean_symbol = symbol.replace(".RO", ".BVB")
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={clean_symbol}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        result = data['quoteResponse']['result'][0]
        return {
            "price": float(result['regularMarketPrice']),
            "name": result.get('shortName', 'BVB Stock')
        }
    except:
        return None

def update_all():
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            # --- LOGICA PENTRU ROMANIA ---
            if ".RO" in s.symbol or ".BVB" in s.symbol or any(x in s.symbol for x in ["H2O", "SNN", "SNP"]):
                data = get_bvb_data(s.symbol)
                if data:
                    s.current_price = data['price']
                    s.tech_details = f"Preț RO: {data['price']} RON (Yahoo)"
                    s.last_signal = "HOLD"
                else:
                    s.tech_details = "Simbol RO negăsit pe Yahoo"
            
            # --- LOGICA PENTRU SUA (Twelve Data) ---
            else:
                # Update pret
                try:
                    p_res = requests.get(f"https://api.twelvedata.com/quote?symbol={s.symbol}&apikey={TWELVE_DATA_KEY}").json()
                    if "close" in p_res:
                        s.current_price = float(p_res['close'])
                except: pass

                # Update Indicatori (Rule #1)
                try:
                    # Folosim functia de indicatori definita anterior (MA, MACD, STOCH)
                    # O adaugam aici prescurtat pentru context
                    base = "https://api.twelvedata.com"
                    ma = requests.get(f"{base}/ma?symbol={s.symbol}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(8)
                    macd = requests.get(f"{base}/macd?symbol={s.symbol}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
                    time.sleep(8)
                    stoch = requests.get(f"{base}/stoch?symbol={s.symbol}&interval=1day&fast_k_period=14&slow_k_period=5&slow_d_period=5&apikey={TWELVE_DATA_KEY}").json()
                    
                    m, md, ms = float(ma['values'][0]['ma']), float(macd['values'][0]['macd']), float(macd['values'][0]['macd_signal'])
                    sk, sd = float(stoch['values'][0]['slow_k']), float(stoch['values'][0]['slow_d'])
                    
                    c_sell = (s.current_price < m) and (md < ms) and (sk < sd)
                    c_buy = (s.current_price > m) and (md > ms) and (sk > sd)
                    
                    s.tech_details = f"MA:{round(m,1)} | MACD:{round(md,2)}/{round(ms,2)} | ST:{round(sk,1)}/{round(sd,1)}"
                    s.last_signal = "BUY" if c_buy else "SELL" if c_sell else "HOLD"
                except:
                    s.tech_details = "Eroare indicatori SUA"

            db.session.commit()
            time.sleep(1)

# Scheduler & Rute (Rămân la fel)
sched = BackgroundScheduler()
sched.add_job(update_all, 'interval', minutes=60)
sched.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

# ... (restul rutelor search, add, delete ramane neschimbat)
