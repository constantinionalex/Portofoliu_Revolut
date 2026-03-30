import os
import datetime
import requests
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(instance_path, 'portfolio.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

TWELVE_DATA_KEY = "0eef54e01c5b4f6aa18c054d569084de"
TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0) 
    high_price = db.Column(db.Float, default=0.0)    
    last_signal = db.Column(db.String(10), default="HOLD")
    last_alert_date = db.Column(db.String(20), default="")

with app.app_context():
    db.create_all()

def send_telegram(msg):
    if TELEGRAM_TOKEN and TELEGRAM_ID:
        try: requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={msg}", timeout=5)
        except: print("Eroare Telegram")

def get_indicators(symbol):
    base = "https://api.twelvedata.com"
    try:
        # Preluam datele pentru MA, MACD (12, 26, 9) si Stochastic
        ma = requests.get(f"{base}/ma?symbol={symbol}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
        macd = requests.get(f"{base}/macd?symbol={symbol}&interval=1day&fast_period=12&slow_period=26&signal_period=9&apikey={TWELVE_DATA_KEY}").json()
        stoch = requests.get(f"{base}/stoch?symbol={symbol}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
        
        return {
            "ma10": float(ma['values'][0]['ma']),
            "macd_line": float(macd['values'][0]['macd']),
            "signal_line": float(macd['values'][0]['macd_signal']),
            "k": float(stoch['values'][0]['slow_k']),
            "d": float(stoch['values'][0]['slow_d'])
        }
    except Exception as e:
        print(f"Eroare API {symbol}: {e}")
        return None

def check_prices():
    with app.app_context():
        stocks = Stock.query.all()
        if not stocks: return
        syms = [s.symbol for s in stocks]
        data = requests.get(f"https://api.twelvedata.com/quote?symbol={','.join(syms)}&apikey={TWELVE_DATA_KEY}").json()
        for s in stocks:
            res = data.get(s.symbol) if len(stocks) > 1 else data
            if res and "close" in res:
                s.current_price = float(res['close'])
                s.high_price = float(res.get('high', 0))
        db.session.commit()

def check_rule_one():
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            tech = get_indicators(s.symbol)
            if not tech: continue
            
            # Debug LOGS
            print(f"--- Analiza Phil Town: {s.symbol} ---")
            
            # 1. Media Mobila 10 zile
            c1_buy = s.current_price > tech['ma10']
            c1_sell = s.current_price < tech['ma10']
            
            # 2. MACD Crossover (Intersectia liniilor)
            c2_buy = tech['macd_line'] > tech['signal_line']
            c2_sell = tech['macd_line'] < tech['signal_line']
            
            # 3. Stochastic Crossover
            c3_buy = tech['k'] > tech['d']
            c3_sell = tech['k'] < tech['d']

            new_status = "HOLD"
            if c1_buy and c2_buy and c3_buy:
                new_status = "BUY"
            elif c1_sell and c2_sell and c3_sell:
                new_status = "SELL"
            
            if new_status != s.last_signal:
                if new_status in ["BUY", "SELL"]:
                    send_telegram(f"🔔 {s.symbol}: Semnal {new_status} (${s.current_price})")
                s.last_signal = new_status
            
            print(f"MA10: {c1_buy}, MACD Cross: {c2_buy}, Stoch Cross: {c3_buy} -> {new_status}")
        db.session.commit()

sched = BackgroundScheduler()
sched.add_job(check_prices, 'interval', minutes=3)
sched.add_job(check_rule_one, 'interval', minutes=60)
sched.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    ui = []
    for s in stocks:
        profit = round(((s.current_price - s.purchase_price) / s.purchase_price * 100), 2) if s.current_price > 0 else 0
        ui.append({'id':s.id, 'symbol':s.symbol, 'buy':s.purchase_price, 'current':s.current_price, 'high':s.high_price, 'signal':s.last_signal, 'profit':profit})
    return render_template('index.html', stocks=ui)

@app.route('/search')
def search():
    q = request.args.get('q', '')
    r = requests.get(f"https://query1.finance.yahoo.com/v1/finance/search?q={q}", headers={'User-Agent': 'Mozilla/5.0'})
    return jsonify([{'symbol': x['symbol'], 'name': x.get('shortname', '')} for x in r.json().get('quotes', []) if x.get('quoteType') in ['EQUITY', 'ETF']])

@app.route('/add', methods=['POST'])
def add():
    s, p = request.form.get('symbol').upper(), float(request.form.get('price', 0))
    if not Stock.query.filter_by(symbol=s).first():
        db.session.add(Stock(symbol=s, purchase_price=p))
        db.session.commit()
        check_prices(); check_rule_one()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete(id):
    s = db.session.get(Stock, id)
    if s: db.session.delete(s); db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
