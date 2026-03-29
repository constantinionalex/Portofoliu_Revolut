import os
import datetime
import requests
import yfinance as yf
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///portfolio.db'
db = SQLAlchemy(app)

# Configurare Telegram (din variabile de mediu)
TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    last_alert_date = db.Column(db.String(20), default="")

with app.app_context():
    db.create_all()

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={message}"
    requests.get(url)

def check_prices():
    with app.app_context():
        stocks = Stock.query.all()
        today = datetime.date.today().isoformat()
        
        for stock in stocks:
            if stock.last_alert_date == today:
                continue

            ticker = yf.Ticker(stock.symbol)
            hist = ticker.history(period="2d", interval="1h") # Luăm ultimele 24h+
            if hist.empty: continue

            current_price = hist['Close'].iloc[-1]
            peak_24h = hist['High'].max()
            
            alert_triggered = False
            msg = ""

            # Condiția 1: Scădere 5% față de achiziție
            if current_price <= stock.purchase_price * 0.95:
                msg = f"⚠️ Alertă {stock.symbol}: Scădere >5% față de achiziție!\nPreț achiziție: {stock.purchase_price:.2f}\nPreț actual: {current_price:.2f}"
                alert_triggered = True
            
            # Condiția 2: Scădere 15% față de vârful de 24h
            elif current_price <= peak_24h * 0.85:
                msg = f"📉 Alertă {stock.symbol}: Scădere >15% față de vârful 24h!\nVârf 24h: {peak_24h:.2f}\nPreț actual: {current_price:.2f}"
                alert_triggered = True

            if alert_triggered:
                send_telegram_msg(msg)
                stock.last_alert_date = today
                db.session.commit()

# Scheduler pentru verificare la fiecare 15 minute
scheduler = BackgroundScheduler()
scheduler.add_job(func=check_prices, trigger="interval", minutes=15)
scheduler.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    results = []
    for s in stocks:
        t = yf.Ticker(s.symbol)
        h = t.history(period="2d")
        peak = h['High'].max() if not h.empty else 0
        results.append({'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price, 'peak': round(peak, 2)})
    return render_template('index.html', stocks=results)

@app.route('/add', methods=['POST'])
def add_stock():
    symbol = request.form.get('symbol').upper()
    price = request.form.get('price')
    new_stock = Stock(symbol=symbol, purchase_price=float(price))
    db.session.add(new_stock)
    db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_stock(id):
    stock = Stock.query.get(id)
    db.session.delete(stock)
    db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
