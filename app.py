import os
import datetime
import requests
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///portfolio.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# CONFIGURARE - API NOU
TWELVE_DATA_KEY = "0eef54e01c5b4f6aa18c054d569084de"
TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0) 
    high_price = db.Column(db.Float, default=0.0)    
    last_alert_date = db.Column(db.String(20), default="")

# REPARARE AUTOMATĂ LA PORNIRE
with app.app_context():
    db.create_all()
    for col in ["current_price", "high_price"]:
        try:
            with db.engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE stock ADD COLUMN {col} FLOAT DEFAULT 0.0"))
                conn.commit()
                print(f"✅ Coloana {col} a fost adaugata cu succes.")
        except:
            pass # Coloana exista deja, totul e ok

def get_batch_data(symbols):
    if not symbols: return {}
    try:
        sym_str = ",".join(symbols)
        url = f"https://api.twelvedata.com/quote?symbol={sym_str}&apikey={TWELVE_DATA_KEY}"
        resp = requests.get(url, timeout=15).json()
        
        if isinstance(resp, dict) and resp.get("status") == "error":
            print(f"❌ API Error: {resp.get('message')}")
            return None
        return {symbols[0]: resp} if len(symbols) == 1 else resp
    except:
        return None

def check_prices():
    with app.app_context():
        stocks = Stock.query.all()
        if not stocks: return
        
        data = get_batch_data([s.symbol for s in stocks])
        if not data: return

        today = datetime.date.today().isoformat()
        for s in stocks:
            res = data.get(s.symbol)
            if res and "close" in res:
                s.current_price = float(res['close'])
                s.high_price = float(res['high'])
                
                # Alerte Telegram
                if s.last_alert_date != today:
                    msg = ""
                    if s.current_price <= s.purchase_price * 0.95:
                        msg = f"⚠️ {s.symbol}: -5% ({s.current_price}$)"
                    elif s.current_price <= s.high_price * 0.85:
                        msg = f"📉 {s.symbol}: -15% vs Maxim ({s.current_price}$)"
                    
                    if msg and TELEGRAM_TOKEN:
                        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={msg}")
                        s.last_alert_date = today
        db.session.commit()
        print(f"🔄 Update reusit la {datetime.datetime.now().strftime('%H:%M:%S')}")

# Pornire Scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=check_prices, trigger="interval", minutes=3)
scheduler.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    ui_data = []
    for s in stocks:
        profit = ((s.current_price - s.purchase_price) / s.purchase_price * 100) if s.current_price > 0 else 0
        ui_data.append({
            'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price,
            'current': round(s.current_price, 2), 'high': round(s.high_price, 2),
            'profit': round(profit, 2)
        })
    return render_template('index.html', stocks=ui_data)

@app.route('/add', methods=['POST'])
def add_stock():
    symbol = request.form.get('symbol', '').upper()
    price = float(request.form.get('price', 0))
    if symbol and not Stock.query.filter_by(symbol=symbol).first():
        db.session.add(Stock(symbol=symbol, purchase_price=price))
        db.session.commit()
        check_prices()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_stock(id):
    s = db.session.get(Stock, id)
    if s:
        db.session.delete(s)
        db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
