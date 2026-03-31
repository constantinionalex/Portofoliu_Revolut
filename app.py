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
TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0)
    last_signal = db.Column(db.String(10), default="HOLD")
    tech_details = db.Column(db.String(200), default="Se încarcă...")

with app.app_context():
    db.create_all()

def get_indicators(symbol):
    base = "https://api.twelvedata.com"
    try:
        # MA 10
        ma = requests.get(f"{base}/ma?symbol={symbol}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
        time.sleep(8) # Pauză obligatorie pentru limita de 8 req/min
        
        # MACD
        macd = requests.get(f"{base}/macd?symbol={symbol}&interval=1day&apikey={TWELVE_DATA_KEY}").json()
        time.sleep(8)
        
        # STOCH 14, 5, 5
        stoch = requests.get(f"{base}/stoch?symbol={symbol}&interval=1day&fast_k_period=14&slow_k_period=5&slow_d_period=5&apikey={TWELVE_DATA_KEY}").json()
        
        return {
            "ma": float(ma['values'][0]['ma']),
            "macd": float(macd['values'][0]['macd']),
            "sig": float(macd['values'][0]['macd_signal']),
            "k": float(stoch['values'][0]['slow_k']),
            "d": float(stoch['values'][0]['slow_d'])
        }
    except Exception as e:
        print(f"Eroare API {symbol}: {e}")
        return None

def update_all():
    with app.app_context():
        stocks = Stock.query.all()
        if not stocks: return
        
        # 1. Update Prețuri (1 singur apel batch)
        syms = ",".join([s.symbol for s in stocks])
        prices = requests.get(f"https://api.twelvedata.com/quote?symbol={syms}&apikey={TWELVE_DATA_KEY}").json()
        
        for s in stocks:
            p_data = prices.get(s.symbol) if len(stocks) > 1 else prices
            if p_data and "close" in p_data:
                s.current_price = float(p_data['close'])
            
            # 2. Update Indicatori (Pe rând, cu pauză)
            t = get_indicators(s.symbol)
            if t:
                c_sell = (s.current_price < t['ma']) and (t['macd'] < t['sig']) and (t['k'] < t['d'])
                c_buy = (s.current_price > t['ma']) and (t['macd'] > t['sig']) and (t['k'] > t['d'])
                
                s.tech_details = f"MA:{round(t['ma'],2)} | MACD:{round(t['macd'],2)}/{round(t['sig'],2)} | ST:{round(t['k'],1)}/{round(t['d'],1)}"
                new_status = "BUY" if c_buy else "SELL" if c_sell else "HOLD"
                
                if new_status != s.last_signal and new_status != "HOLD":
                    requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text=📢 {s.symbol}: {new_status} (${s.current_price})")
                s.last_signal = new_status
            else:
                s.tech_details = "Eroare Limită API (Așteaptă)"
            
            db.session.commit()
            time.sleep(2)

sched = BackgroundScheduler()
sched.add_job(update_all, 'interval', minutes=30) # Rulăm rar pentru a nu consuma creditele
sched.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

@app.route('/add', methods=['POST'])
def add():
    s = request.form.get('symbol', '').upper()
    p = float(request.form.get('price', 0))
    if s and not Stock.query.filter_by(symbol=s).first():
        db.session.add(Stock(symbol=s, purchase_price=p))
        db.session.commit()
        # Nu apelăm update_all aici ca să nu blocăm interfața, va rula scheduler-ul sau manual
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete(id):
    s = db.session.get(Stock, id)
    if s: db.session.delete(s); db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/refresh_manual')
def refresh_manual():
    update_all()
    return jsonify({"status": "refreshing"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
