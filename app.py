import os
import datetime
import requests
import yfinance as yf
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
# Baza de date va fi salvată în folderul instance, mapat la volumul Docker
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///portfolio.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Configurare Telegram din variabilele de mediu setate în Portainer
TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Model.id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    last_alert_date = db.Column(db.String(20), default="")

with app.app_context():
    db.create_all()

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={message}"
    try:
        requests.get(url)
    except Exception as e:
        print(f"Eroare trimitere Telegram: {e}")

def check_prices():
    with app.app_context():
        stocks = Stock.query.all()
        today = datetime.date.today().isoformat()
        
        for stock in stocks:
            if stock.last_alert_date == today:
                continue

            ticker = yf.Ticker(stock.symbol)
            # Luăm datele pe ultimele 2 zile pentru a calcula vârful de 24h
            hist = ticker.history(period="2d", interval="1h")
            if hist.empty: continue

            current_price = hist['Close'].iloc[-1]
            peak_24h = hist['High'].max()
            
            alert_triggered = False
            msg = ""

            # Regula 1: Scădere 5% față de prețul de achiziție
            if current_price <= stock.purchase_price * 0.95:
                msg = f"⚠️ Alertă {stock.symbol}: Scădere >5% față de achiziție!\nPreț achiziție: {stock.purchase_price:.2f}$\nPreț actual: {current_price:.2f}$"
                alert_triggered = True
            
            # Regula 2: Scădere 15% față de vârful ultimelor 24h
            elif current_price <= peak_24h * 0.85:
                msg = f"📉 Alertă {stock.symbol}: Scădere >15% față de vârful 24h!\nVârf 24h: {peak_24h:.2f}$\nPreț actual: {current_price:.2f}$"
                alert_triggered = True

            if alert_triggered:
                send_telegram_msg(msg)
                stock.last_alert_date = today
                db.session.commit()

# Pornim verificarea automată la fiecare 15 minute
scheduler = BackgroundScheduler()
scheduler.add_job(func=check_prices, trigger="interval", minutes=15)
scheduler.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    results = []
    for s in stocks:
        try:
            t = yf.Ticker(s.symbol)
            h = t.history(period="2d")
            peak = h['High'].max() if not h.empty else 0
            results.append({
                'id': s.id, 
                'symbol': s.symbol, 
                'buy': s.purchase_price, 
                'peak': round(peak, 2),
                'current': round(h['Close'].iloc[-1], 2) if not h.empty else 0
            })
        except:
            continue
    return render_template('index.html', stocks=results)

@app.route('/search')
def search_stock():
    query = request.args.get('q')
    if not query: return jsonify([])
    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers)
        data = resp.json()
        output = [{'symbol': q['symbol'], 'name': q.get('shortname', q.get('longname', ''))} 
                  for q in data.get('quotes', []) if q.get('quoteType') in ['EQUITY', 'ETF']]
        return jsonify(output)
    except:
        return jsonify([])

@app.route('/add', methods=['POST'])
def add_stock():
    symbol = request.form.get('symbol').upper()
    price = request.form.get('price')
    if not Stock.query.filter_by(symbol=symbol).first():
        new_stock = Stock(symbol=symbol, purchase_price=float(price))
        db.session.add(new_stock)
        db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_stock(id):
    stock = Stock.query.get(id)
    if stock:
        db.session.delete(stock)
        db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
